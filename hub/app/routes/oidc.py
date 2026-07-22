"""OIDC Provider pour le HUB POULIDOR (bridge OIDC pour apps externes).

Approche C : implémentation from-scratch avec pyjwt[crypto] déjà présent.
Réutilise settings.jwt_private_key, JWKS endpoint, hash_token(), get_password_hash().
"""
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_token, utcnow, verify_token
from app.config import get_settings
from app.database import get_db
from app.models import Application, OAuthAuthorizationCode, OAuthClient, User
from app.oidc_helpers import build_oidc_metadata, generate_authorization_code
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
