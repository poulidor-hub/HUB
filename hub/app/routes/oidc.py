"""OIDC Provider pour le HUB POULIDOR (bridge OIDC pour apps externes).

Approche C : implémentation from-scratch avec pyjwt[crypto] déjà présent.
Réutilise settings.jwt_private_key, JWKS endpoint, hash_token(), get_password_hash().
"""
from datetime import timedelta
from typing import List, Optional
from urllib.parse import urlencode
import time

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin, hash_token, utcnow, verify_token
from app.config import get_settings
from app.database import get_db
from app.models import (
    Application,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
    User,
)
from app.oidc_helpers import (
    build_oidc_metadata,
    create_access_token_jwt,
    create_id_token,
    generate_authorization_code,
    generate_logout_token,
    generate_refresh_token,
    validate_pkce,
)
from app.routes.audit import log_action

router = APIRouter(tags=["OIDC"])
settings = get_settings()

# ============================================================================
# RATE LIMITING /api/oidc/token (spec §8.1 : 10 req/min/IP anti brute-force)
# ============================================================================
_oidc_token_attempts: dict[str, list[float]] = {}
OIDC_TOKEN_RATE_LIMIT = 10  # max requêtes par minute par IP
_OIDC_TOKEN_WINDOW = 60  # secondes


def _check_oidc_token_rate_limit(request: Request) -> None:
    """Rate-limit l'endpoint token : 10 req/min/IP. Lève 429 si dépassé."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = _oidc_token_attempts.get(ip, [])
    # Nettoyer les entrées expirées
    attempts = [t for t in attempts if now - t < _OIDC_TOKEN_WINDOW]
    if len(attempts) >= OIDC_TOKEN_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de requêtes sur /oidc/token, réessayez plus tard",
            headers={"Retry-After": str(_OIDC_TOKEN_WINDOW)},
        )
    attempts.append(now)
    _oidc_token_attempts[ip] = attempts


@router.get("/.well-known/openid-configuration")
async def openid_configuration():
    """Document de discovery OIDC standard (public)."""
    return build_oidc_metadata(settings)


def _verify_launch_access_token(access_token: str) -> tuple[str, dict]:
    """Valide le JWT HUB passé en query param (EventSource / redirect-friendly).

    verify_token est synchrone (signature : verify_token(token: str) -> dict) :
    on l'appelle directement, sans paramètre request.
    """
    try:
        payload = verify_token(access_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token HUB invalide ou expiré",
        )
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sans sub",
        )
    return username, payload


@router.get("/api/oidc/launch/{client_id}")
async def launch_oidc(
    client_id: str,
    request: Request,
    redirect_uri: str = Query(...),
    state: str = Query(None),
    nonce: str = Query(None),
    scope: str = Query("openid profile email"),
    code_challenge: str = Query(None),
    code_challenge_method: str = Query(None),
    access_token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Shortcut OIDC : valide un JWT HUB, génère un code OIDC one-shot, redirige.

    Spécifique au HUB : exploite le JWT HUB déjà détenu par le dashboard
    plutôt qu'une session cookie navigateur (le HUB n'a pas de cookie session).
    """
    # 1. Valider le JWT HUB (verify_token synchrone)
    username, payload = _verify_launch_access_token(access_token)
    apps_in_token = payload.get("apps", {})

    # 2. Lookup client OIDC
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    oauth_client = result.scalar_one_or_none()
    if not oauth_client:
        raise HTTPException(404, f"Client OIDC '{client_id}' introuvable")
    if not oauth_client.is_active:
        raise HTTPException(403, f"Client OIDC '{client_id}' désactivé")

    # 3. Vérifier l'accès user (rôle app via token HUB).
    # Convention : un client OIDC est lié à une app HUB via app_id. Quand c'est
    # le cas, on résout le slug de cette app séparément (la relation
    # OAuthClient.application est lazy="raise", donc non navigable en async).
    # Quand app_id est None, le client_id sert de slug de référence (cas Vikunja
    # où client_id == slug app). Dans les deux cas, ce slug doit être une clé du
    # dict apps du JWT HUB.
    if oauth_client.app_id:
        app_result = await db.execute(
            select(Application).where(Application.id == oauth_client.app_id)
        )
        linked_app = app_result.scalar_one_or_none()
        app_slug = linked_app.slug if linked_app else oauth_client.client_id
    else:
        app_slug = oauth_client.client_id

    if app_slug not in apps_in_token:
        raise HTTPException(
            403, f"Accès refusé à l'app '{app_slug}' (rôle manquant)"
        )

    # 4. Valider redirect_uri (whitelist stricte)
    if not oauth_client.check_redirect_uri(redirect_uri):
        raise HTTPException(400, "redirect_uri non autorisé pour ce client")

    # 5. Filtrer les scopes demandés vs autorisés
    requested_scopes = (scope or "").split()
    allowed_scopes = oauth_client.get_allowed_scopes(requested_scopes)
    if "openid" not in allowed_scopes:
        raise HTTPException(400, "Le scope 'openid' est obligatoire")
    final_scope = " ".join(allowed_scopes)

    # 6. Récupérer le user en DB (pour user_id)
    user_result = await db.execute(select(User).where(User.username == username))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, f"Utilisateur '{username}' introuvable")

    # 7. Générer code OIDC one-shot
    raw_code = generate_authorization_code()
    code_hash = hash_token(raw_code)
    new_code = OAuthAuthorizationCode(
        code_hash=code_hash,
        client_id=client_id,
        user_id=user.id,
        redirect_uri=redirect_uri,
        scope=final_scope,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        nonce=nonce,
        expires_at=utcnow() + timedelta(seconds=settings.oidc_code_ttl_seconds),
    )
    db.add(new_code)

    # 8. Audit + commit (log_action ne commit pas, l'appelant le fait)
    await log_action(
        db=db,
        user_id=user.id,
        action="oidc_launch",
        target=client_id,
        request=request,
        response_status=302,
        details=f"Launch OIDC client={client_id} scope={final_scope}",
    )
    await db.commit()

    # 9. Rediriger avec code + state
    params = {"code": raw_code}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{separator}{urlencode(params)}",
        status_code=302,
    )


# =============================================================================
# /api/oidc/token : échange code (ou refresh) contre tokens OIDC
# =============================================================================

def _decode_basic_auth(request: Request) -> tuple[Optional[str], Optional[str]]:
    """Extrait client_id/client_secret depuis Authorization Basic header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return None, None
    import base64
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        cid, _, csec = decoded.partition(":")
        return cid, csec
    except Exception:
        return None, None


async def _authenticate_client(
    request: Request, db: AsyncSession,
    client_id_form: Optional[str], client_secret_form: Optional[str]
) -> OAuthClient:
    """Authentifie le client via Basic header ou form body. Retourne le OAuthClient."""
    # Form body prioritaire ; Basic header en fallback pour chaque champ manquant
    # (RFC 6749 §2.3.1 : client_secret_basic utilisable même si client_id est
    # fourni dans le corps).
    client_id = client_id_form
    client_secret = client_secret_form
    if not client_id or not client_secret:
        cid_basic, csec_basic = _decode_basic_auth(request)
        client_id = client_id or cid_basic
        client_secret = client_secret or csec_basic
    client_secret = client_secret or ""
    if not client_id:
        raise HTTPException(401, "client_id manquant")

    result = await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    oauth_client = result.scalar_one_or_none()
    if not oauth_client or not oauth_client.is_active:
        raise HTTPException(401, "Client inconnu ou inactif")
    if not oauth_client.check_client_secret(client_secret):
        raise HTTPException(401, "client_secret invalide")
    return oauth_client


@router.post("/api/oidc/token")
async def token_endpoint(
    request: Request,
    grant_type: str = Form(...),
    code: Optional[str] = Form(None),
    redirect_uri: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None),
    code_verifier: Optional[str] = Form(None),
    refresh_token: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Endpoint standard OAuth2/OIDC token : échange code ou refresh contre tokens."""
    _check_oidc_token_rate_limit(request)
    oauth_client = await _authenticate_client(
        request, db, client_id, client_secret
    )

    if grant_type == "authorization_code":
        return await _handle_authorization_code_grant(
            request, db, oauth_client, code, redirect_uri, code_verifier
        )
    elif grant_type == "refresh_token":
        return await _handle_refresh_token_grant(
            request, db, oauth_client, refresh_token
        )
    else:
        raise HTTPException(400, f"grant_type '{grant_type}' non supporté")


async def _handle_authorization_code_grant(
    request, db, oauth_client, code, redirect_uri, code_verifier
):
    if not code or not redirect_uri:
        raise HTTPException(400, "code et redirect_uri requis")

    # 1. Lookup code par hash
    code_hash = hash_token(code)
    result = await db.execute(
        select(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code_hash == code_hash,
            OAuthAuthorizationCode.client_id == oauth_client.client_id,
        )
    )
    auth_code = result.scalar_one_or_none()
    if not auth_code:
        raise HTTPException(400, "code invalide ou inconnu")
    if auth_code.is_used:
        raise HTTPException(400, "code déjà utilisé (one-shot)")
    if auth_code.is_expired:
        raise HTTPException(400, "code expiré")
    if auth_code.redirect_uri != redirect_uri:
        raise HTTPException(400, "redirect_uri mismatch")

    # 2. PKCE
    if not validate_pkce(
        code_verifier, auth_code.code_challenge, auth_code.code_challenge_method
    ):
        raise HTTPException(400, "PKCE validation échouée")

    # 3. Marquer code comme utilisé
    auth_code.used_at = utcnow()

    # 4. Lookup user
    user_result = await db.execute(
        select(User).where(User.id == auth_code.user_id)
    )
    user = user_result.scalar_one()

    # 5. Générer tokens
    id_token = create_id_token(
        username=user.username,
        client_id=oauth_client.client_id,
        nonce=auth_code.nonce,
        scope=auth_code.scope,
        settings=settings,
    )
    access_token = create_access_token_jwt(
        username=user.username,
        client_id=oauth_client.client_id,
        scope=auth_code.scope,
        settings=settings,
    )
    raw_refresh = generate_refresh_token()
    refresh_hash = hash_token(raw_refresh)
    new_rt = OAuthRefreshToken(
        token_hash=refresh_hash,
        client_id=oauth_client.client_id,
        user_id=user.id,
        scope=auth_code.scope,
        expires_at=utcnow() + timedelta(days=settings.oidc_refresh_token_days),
    )
    db.add(new_rt)

    # 6. Audit + commit
    await log_action(
        db=db,
        user_id=user.id,
        action="oidc_token",
        target=oauth_client.client_id,
        request=request,
        response_status=200,
        details=f"Tokens OIDC émis (grant=authorization_code)",
    )
    await db.commit()

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": settings.oidc_access_token_ttl_minutes * 60,
        "refresh_token": raw_refresh,
        "id_token": id_token,
        "scope": auth_code.scope,
    }


async def _handle_refresh_token_grant(
    request, db, oauth_client, refresh_token
):
    if not refresh_token:
        raise HTTPException(400, "refresh_token requis")

    rt_hash = hash_token(refresh_token)
    result = await db.execute(
        select(OAuthRefreshToken).where(
            OAuthRefreshToken.token_hash == rt_hash,
            OAuthRefreshToken.client_id == oauth_client.client_id,
        )
    )
    rt = result.scalar_one_or_none()
    if not rt or not rt.is_valid:
        raise HTTPException(400, "refresh_token invalide ou révoqué")

    # Lookup user
    user_result = await db.execute(select(User).where(User.id == rt.user_id))
    user = user_result.scalar_one()

    # Rotation : révoquer l'ancien, créer un nouveau
    rt.revoked_at = utcnow()

    new_raw = generate_refresh_token()
    new_rt = OAuthRefreshToken(
        token_hash=hash_token(new_raw),
        client_id=oauth_client.client_id,
        user_id=user.id,
        scope=rt.scope,
        expires_at=utcnow() + timedelta(days=settings.oidc_refresh_token_days),
    )
    db.add(new_rt)

    # Nouveaux access + id tokens
    id_token = create_id_token(
        username=user.username,
        client_id=oauth_client.client_id,
        nonce=None,  # pas de nonce au refresh
        scope=rt.scope,
        settings=settings,
    )
    access_token = create_access_token_jwt(
        username=user.username,
        client_id=oauth_client.client_id,
        scope=rt.scope,
        settings=settings,
    )

    await log_action(
        db=db,
        user_id=user.id,
        action="oidc_token_refresh",
        target=oauth_client.client_id,
        request=request,
        response_status=200,
    )
    await db.commit()

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": settings.oidc_access_token_ttl_minutes * 60,
        "refresh_token": new_raw,
        "id_token": id_token,
        "scope": rt.scope,
    }


# =============================================================================
# /api/oidc/userinfo : UserInfo OIDC (Bearer access_token)
# =============================================================================

bearer_scheme = HTTPBearer(auto_error=False)


async def _decode_oidc_access_token(
    credentials: HTTPAuthorizationCredentials,
) -> dict:
    """Décode et valide un access_token OIDC signé par le HUB."""
    try:
        payload = pyjwt.decode(
            credentials.credentials,
            settings.jwt_public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        if payload.get("token_type") != "OIDC_ACCESS":
            raise HTTPException(401, "Token n'est pas un access_token OIDC")
        return payload
    except pyjwt.PyJWTError as e:
        raise HTTPException(401, f"Token invalide: {e}")


@router.get("/api/oidc/userinfo")
@router.post("/api/oidc/userinfo")
async def userinfo_endpoint(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """OIDC UserInfo : retourne les claims de l'utilisateur selon le scope du token."""
    if not credentials:
        raise HTTPException(401, "Authorization Bearer requis")
    payload = await _decode_oidc_access_token(credentials)

    username = payload.get("sub")
    scope_str = payload.get("scope", "")
    scopes = set(scope_str.split())

    from app.oidc_helpers import compute_email_claim
    claims = {"sub": username}
    if "profile" in scopes:
        claims["preferred_username"] = username
        claims["name"] = username
    if "email" in scopes:
        claims["email"] = compute_email_claim(username, settings)
        claims["email_verified"] = True
    return claims


# =============================================================================
# /api/oidc/backchannel-logout : Back-Channel Logout OIDC (admin HUB only)
# =============================================================================

class BackchannelLogoutRequest(BaseModel):
    sub: str


@router.post("/api/oidc/backchannel-logout/{client_id}")
async def trigger_backchannel_logout(
    client_id: str,
    payload: BackchannelLogoutRequest = Body(...),
    admin_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Déclenche un Back-Channel Logout OIDC vers un client (admin HUB only).

    1. Révoque tous les refresh tokens OIDC de l'user pour ce client.
    2. Envoie un logout_token JWT signé au backchannel_logout_uri du client.
    """
    if not settings.oidc_backchannel_logout_enabled:
        raise HTTPException(501, "Back-Channel Logout désactivé (OIDC_BACKCHANNEL_LOGOUT_ENABLED=false)")

    # 1. Lookup client
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    oauth_client = result.scalar_one_or_none()
    if not oauth_client:
        raise HTTPException(404, "Client OIDC introuvable")
    if not oauth_client.backchannel_logout_uri:
        raise HTTPException(400, "Client n'a pas de backchannel_logout_uri configuré")

    # 2. Lookup user (par sub = username)
    user_result = await db.execute(
        select(User).where(User.username == payload.sub)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, f"Utilisateur '{payload.sub}' introuvable")

    # 3. Révoquer tous les refresh tokens OIDC de l'user pour ce client
    rt_result = await db.execute(
        select(OAuthRefreshToken).where(
            OAuthRefreshToken.client_id == client_id,
            OAuthRefreshToken.user_id == user.id,
            OAuthRefreshToken.revoked_at.is_(None),
        )
    )
    rts = rt_result.scalars().all()
    for rt in rts:
        rt.revoked_at = utcnow()

    # 4. Générer + envoyer le logout_token
    logout_token = generate_logout_token(
        client_id=client_id,
        sub=user.username,
        settings=settings,
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.post(
                oauth_client.backchannel_logout_uri,
                data={"logout_token": logout_token},
            )
        delivered = resp.status_code < 400
        delivery_status = resp.status_code
    except Exception as e:
        delivered = False
        delivery_status = f"error: {e}"

    # 5. Audit + commit
    await log_action(
        db=db,
        user_id=admin_user.id,
        action="oidc_backchannel_logout",
        target=client_id,
        request=request,
        response_status=200,
        details=f"Back-channel logout sub={payload.sub} delivered={delivered} status={delivery_status}",
    )
    await db.commit()

    return {
        "client_id": client_id,
        "sub": payload.sub,
        "refresh_tokens_revoked": len(rts),
        "logout_token_delivered": delivered,
        "delivery_status": delivery_status,
    }


# =============================================================================
# /api/oidc/clients : CRUD admin des clients OIDC (secret one-shot display)
# =============================================================================

class OAuthClientCreate(BaseModel):
    name: str
    client_id: Optional[str] = None  # auto-généré si non fourni
    redirect_uris: List[str]
    scopes: List[str] = ["openid", "profile", "email"]
    auto_approve: bool = True
    backchannel_logout_uri: Optional[str] = None
    app_id: Optional[int] = None


class OAuthClientUpdate(BaseModel):
    name: Optional[str] = None
    redirect_uris: Optional[List[str]] = None
    scopes: Optional[List[str]] = None
    auto_approve: Optional[bool] = None
    backchannel_logout_uri: Optional[str] = None
    app_id: Optional[int] = None


def _serialize_client(c: OAuthClient) -> dict:
    """Sérialise un OAuthClient SANS exposer le secret hash."""
    return {
        "id": c.id,
        "client_id": c.client_id,
        "name": c.name,
        "redirect_uris": c.redirect_uris,
        "scopes": c.scopes,
        "is_active": c.is_active,
        "auto_approve": c.auto_approve,
        "backchannel_logout_uri": c.backchannel_logout_uri,
        "app_id": c.app_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@router.get("/api/oidc/clients")
async def list_oidc_clients(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(OAuthClient).order_by(OAuthClient.created_at.desc()))
    clients = result.scalars().all()
    return [_serialize_client(c) for c in clients]


@router.post("/api/oidc/clients", status_code=201)
async def create_oidc_client(
    payload: OAuthClientCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    from app.auth import get_password_hash
    raw_secret = OAuthClient.generate_client_secret()
    client = OAuthClient(
        client_id=payload.client_id or OAuthClient.generate_client_id(),
        client_secret_hash=get_password_hash(raw_secret),
        name=payload.name,
        redirect_uris=payload.redirect_uris,
        scopes=payload.scopes,
        auto_approve=payload.auto_approve,
        backchannel_logout_uri=payload.backchannel_logout_uri,
        app_id=payload.app_id,
    )
    db.add(client)
    await log_action(
        db=db, user_id=admin.id, action="oidc_client_create",
        target=client.client_id, request=request, response_status=201,
    )
    await db.commit()
    await db.refresh(client)
    data = _serialize_client(client)
    data["client_secret"] = raw_secret  # affiché UNE SEULE FOIS
    return data


@router.put("/api/oidc/clients/{client_id_pk}")
async def update_oidc_client(
    client_id_pk: int,
    payload: OAuthClientUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.id == client_id_pk)
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client OIDC introuvable")

    if payload.name is not None: client.name = payload.name
    if payload.redirect_uris is not None: client.redirect_uris = payload.redirect_uris
    if payload.scopes is not None: client.scopes = payload.scopes
    if payload.auto_approve is not None: client.auto_approve = payload.auto_approve
    if payload.backchannel_logout_uri is not None: client.backchannel_logout_uri = payload.backchannel_logout_uri
    if payload.app_id is not None: client.app_id = payload.app_id
    await db.commit()
    await db.refresh(client)
    return _serialize_client(client)


@router.patch("/api/oidc/clients/{client_id_pk}/toggle-active")
async def toggle_oidc_client_active(
    client_id_pk: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.id == client_id_pk)
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client OIDC introuvable")
    client.is_active = not client.is_active
    await db.commit()
    await db.refresh(client)
    return _serialize_client(client)


@router.post("/api/oidc/clients/{client_id_pk}/regenerate-secret")
async def regenerate_oidc_client_secret(
    client_id_pk: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    from app.auth import get_password_hash
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.id == client_id_pk)
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client OIDC introuvable")
    raw_secret = OAuthClient.generate_client_secret()
    client.client_secret_hash = get_password_hash(raw_secret)
    await log_action(
        db=db, user_id=admin.id, action="oidc_client_regenerate_secret",
        target=client.client_id, request=request, response_status=200,
    )
    await db.commit()
    return {
        "client_id": client.client_id,
        "client_secret": raw_secret,  # affiché UNE SEULE FOIS
    }


@router.delete("/api/oidc/clients/{client_id_pk}", status_code=204)
async def delete_oidc_client(
    client_id_pk: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.id == client_id_pk)
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client OIDC introuvable")
    client_id_value = client.client_id
    await db.delete(client)
    await log_action(
        db=db, user_id=admin.id, action="oidc_client_delete",
        target=client_id_value, request=request, response_status=204,
    )
    await db.commit()
    return None
