"""OIDC Provider pour le HUB POULIDOR (bridge OIDC pour apps externes).

Approche C : implémentation from-scratch avec pyjwt[crypto] déjà présent.
Réutilise settings.jwt_private_key, JWKS endpoint, hash_token(), get_password_hash().
"""
from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_token, utcnow, verify_token
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
    generate_refresh_token,
    validate_pkce,
)
from app.routes.audit import log_action

router = APIRouter(tags=["OIDC"])
settings = get_settings()


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
