# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""OIDC Provider pour le HUB POULIDOR (bridge OIDC pour apps externes).

Approche C : implémentation from-scratch avec pyjwt[crypto] déjà présent.
Réutilise settings.jwt_private_key, JWKS endpoint, hash_token(), get_password_hash().
"""
from datetime import timedelta
from typing import List, Optional
from urllib.parse import urlencode, urlparse, quote
import logging
import time

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import jwt_keyring
from app.auth import (
    get_current_admin,
    get_current_service_account,
    hash_token,
    resolve_user_by_oidc_sub,
    utcnow,
    verify_token,
)
from app.config import get_settings
from app.database import get_db
from app.models import (
    Application,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
    User,
    UserAppRole,
)
from app.oidc_helpers import (
    build_oidc_metadata,
    create_access_token_jwt,
    create_id_token,
    decode_oidc_session_cookie,
    generate_authorization_code,
    generate_logout_token,
    generate_refresh_token,
    validate_pkce,
)
from app.routes.audit import ensure_session_presence, log_action

router = APIRouter(tags=["OIDC"])
settings = get_settings()

# ============================================================================
# Anti-abus /api/oidc/token : throttle sur les ÉCHECS, par client (pas par IP)
# ============================================================================
# L'ancien rate-limit IP (10 req/min, spec §8.1) pénalisait le SSO : l'échange
# code→token est effectué server-to-server par le backend de l'app consommatrice
# (ex: conteneur Vikunja), donc tous les utilisateurs partagent la même IP source
# → 429 « Too Many Requests » dès quelques connexions rapprochées.
#
# Remplacement : plafond d'ÉCHECS (401/400) par client_id. Raisons :
#   - les SUCCÈS ne sont jamais comptés → le SSO légitime ne peut plus être
#     pénalisé, quel que soit le nombre de connexions simultanées ;
#   - le hammering / brute-force (client_secret, codes, refresh tokens) est
#     freiné exactement là où il se manifeste : des échecs répétés ;
#   - les requêtes garbage sans client_id (scans) sont plafonnées par IP —
#     aucun trafic légitime n'omet le client_id (form ou Basic header).
#
# La sécurité cryptographique reste assurée par : client confidentiel (secret
# bcrypt), codes d'autorisation one-shot 384 bits à durée courte (30s), PKCE
# S256, refresh tokens opaques hashés avec rotation.
logger = logging.getLogger(__name__)

_token_failures: dict[str, list[float]] = {}
TOKEN_FAILURE_LIMIT = 30  # échecs max par fenêtre glissante, par clé
_TOKEN_FAILURE_WINDOW = 60  # secondes


def _token_failure_key(request: Request, client_id_form: Optional[str]) -> str:
    """Clé de throttle : client_id (form OU Basic header) si fourni, sinon IP."""
    client_id = client_id_form
    if not client_id:
        cid_basic, _ = _decode_basic_auth(request)
        client_id = cid_basic
    if client_id:
        return f"client:{client_id}"
    ip = request.client.host if request.client else "unknown"
    return f"ip:{ip}"


def _check_token_failure_throttle(key: str) -> None:
    """Lève 429 si la clé a atteint TOKEN_FAILURE_LIMIT échecs dans la fenêtre."""
    now = time.time()
    attempts = [t for t in _token_failures.get(key, []) if now - t < _TOKEN_FAILURE_WINDOW]
    _token_failures[key] = attempts
    if len(attempts) >= TOKEN_FAILURE_LIMIT:
        logger.warning(
            "Throttle anti-abus /api/oidc/token : %d échecs/60s pour %s",
            len(attempts), key,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop d'échecs sur /oidc/token pour ce client, réessayez plus tard",
            headers={"Retry-After": str(_TOKEN_FAILURE_WINDOW)},
        )


def _record_token_failure(key: str) -> None:
    now = time.time()
    attempts = [t for t in _token_failures.get(key, []) if now - t < _TOKEN_FAILURE_WINDOW]
    attempts.append(now)
    _token_failures[key] = attempts


def _clear_token_failures(key: str) -> None:
    """Succès → réinitialise le compteur d'échecs du client."""
    _token_failures.pop(key, None)


@router.get("/.well-known/openid-configuration")
async def openid_configuration():
    """Document de discovery OIDC standard (public)."""
    return build_oidc_metadata(settings)


# ============================================================================
# /oidc/authorize : endpoint standard OIDC Authorization (consommé par Vikunja)
# ============================================================================
# Flow standard : Vikunja génère state/nonce, redirige vers /oidc/authorize.
# On lit le cookie hub_oidc_session (posé au login HUB) pour identifier l'user.
# Si user valide + a accès à l'app → génère code → redirige vers redirect_uri.

def _is_browser_request(request: Request) -> bool:
    """True si la requête vient d'un navigateur (header Accept: text/html).

    Les clients non-navigateur (curl, httpx des tests, intégrations) conservent
    la réponse JSON 401 historique.
    """
    return "text/html" in request.headers.get("accept", "").lower()


def _login_redirect_response(
    request: Request, settings, authz_params: dict
) -> RedirectResponse:
    """302 vers le login dashboard avec reprise automatique du flow OIDC.

    L'URL de reprise est /oidc/authorize RECONSTRUITE depuis les paramètres
    déjà validés de la requête entrante (jamais depuis une URL externe :
    anti open-redirect). Le dashboard stocke ce callback (?callback=...),
    et après login — ou simple refresh token, qui re-pose le cookie
    hub_oidc_session — redirige vers cette URL : l'authorize aboutit alors
    (302 code vers l'app externe, ex. Vikunja).
    """
    resume_qs = {k: v for k, v in authz_params.items() if v}
    resume_url = (
        f"{settings.oidc_issuer.rstrip('/')}/oidc/authorize?{urlencode(resume_qs)}"
    )
    # Origine du dashboard : hostname demandé + port frontend public du HUB
    # (port omis quand il est standard pour le schéma : 80/443).
    scheme = request.url.scheme
    hostname = request.url.hostname
    port = settings.hub_frontend_port
    netloc = (
        hostname
        if (scheme, port) in (("http", 80), ("https", 443))
        else f"{hostname}:{port}"
    )
    login_url = f"{scheme}://{netloc}/?callback={quote(resume_url, safe='')}"
    return RedirectResponse(url=login_url, status_code=302)


@router.get("/oidc/authorize")
async def authorize_oidc(
    request: Request,
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query("code"),
    scope: str = Query("openid profile email"),
    state: Optional[str] = Query(None),
    nonce: Optional[str] = Query(None),
    code_challenge: Optional[str] = Query(None),
    code_challenge_method: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """OIDC Authorization Endpoint standard (RFC 6749 §4.1.1 + OIDC Core §3.1.2).

    Identifie l'user via le cookie hub_oidc_session posé au login HUB.
    Si valide : génère un code OIDC one-shot et redirige vers redirect_uri.
    """
    # 1. Valider response_type
    if response_type != "code":
        raise HTTPException(400, f"response_type '{response_type}' non supporté (attendu: code)")

    # 2. Lookup client
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    oauth_client = result.scalar_one_or_none()
    if not oauth_client:
        raise HTTPException(404, f"Client OIDC '{client_id}' introuvable")
    if not oauth_client.is_active:
        raise HTTPException(403, f"Client OIDC '{client_id}' désactivé")

    # 3. Valider redirect_uri (whitelist stricte)
    if not oauth_client.check_redirect_uri(redirect_uri):
        raise HTTPException(400, "redirect_uri non autorisé pour ce client")

    # 4. Filtrer les scopes (openid obligatoire)
    requested_scopes = (scope or "").split()
    allowed_scopes = oauth_client.get_allowed_scopes(requested_scopes)
    if "openid" not in allowed_scopes:
        raise HTTPException(400, "Le scope 'openid' est obligatoire")
    final_scope = " ".join(allowed_scopes)

    # 5. Identifier l'user via le cookie de session OIDC
    # Reprise du flow : les paramètres validés ci-dessus permettent de
    # reconstruire l'authorize après login (voir _login_redirect_response).
    authz_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": response_type,
        "scope": scope,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    }
    cookie_value = request.cookies.get("hub_oidc_session")
    if not cookie_value:
        if _is_browser_request(request):
            return _login_redirect_response(request, settings, authz_params)
        raise HTTPException(
            401,
            "Session HUB requise. Connectez-vous au dashboard HUB d'abord, "
            "puis relancez Vikunja depuis le dashboard."
        )
    username = decode_oidc_session_cookie(cookie_value, settings)
    if not username:
        if _is_browser_request(request):
            return _login_redirect_response(request, settings, authz_params)
        raise HTTPException(
            401,
            "Session HUB expirée ou invalide. Reconnectez-vous au dashboard HUB."
        )

    # 6. Lookup user (par oidc_sub du cookie, fallback username pré-déploiement)
    user = await resolve_user_by_oidc_sub(db, username)
    if not user:
        raise HTTPException(401, f"Utilisateur '{username}' introuvable")
    if not user.is_active:
        raise HTTPException(403, "Utilisateur désactivé")

    # Vérifier l'accès app (admin global OU UserAppRole explicite)
    # On lit app_id (colonne directe) au lieu de oauth_client.application (lazy='raise').
    linked_app = None
    if oauth_client.app_id:
        app_result = await db.execute(
            select(Application).where(Application.id == oauth_client.app_id)
        )
        linked_app = app_result.scalar_one_or_none()
        if not user.is_admin:
            # Vérifier UserAppRole directement (évite lazy='raise' sur User.app_roles)
            # NB: UserAppRole expose la colonne FK `app_id` (pas `application_id`,
            # qui n'existe pas — la relation SQLAlchemy s'appelle `application`).
            role_result = await db.execute(
                select(UserAppRole).where(
                    UserAppRole.user_id == user.id,
                    UserAppRole.app_id == linked_app.id
                )
            )
            if not role_result.scalar_one_or_none():
                raise HTTPException(
                    403,
                    f"Accès refusé à l'app '{linked_app.slug}' (rôle manquant)"
                )

    # Présence : le passage SSO maintient la session + ouvre le segment de
    # l'app liée (après le contrôle d'accès ; réutilise linked_app résolu).
    await ensure_session_presence(
        db, user.id,
        app_slug=linked_app.slug if linked_app else None,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:255] or None,
    )

    # 7. Générer le code OIDC one-shot
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

    # 8. Audit + commit
    await log_action(
        db=db,
        user_id=user.id,
        action="oidc_authorize",
        target=client_id,
        request=request,
        response_status=302,
        details=f"Authorize OIDC client={client_id} scope={final_scope}",
    )
    await db.commit()

    # 9. Rediriger vers redirect_uri avec code + state
    params = {"code": raw_code}
    if state is not None:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{separator}{urlencode(params)}",
        status_code=302,
    )


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
    access_token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Redirecteur OIDC : valide un JWT HUB, redirige vers la page de login de l'app externe.

    Le dashboard HUB ouvre toujours `{callback_url}?access_token=<jwt>` au clic
    sur une app. Pour les apps OIDC (Vikunja), ce endpoint sert de tampon :
    il valide le JWT (audit + contrôle d'accès), puis redirige vers le
    `frontend_url` de l'app liée (page de login OIDC du client externe) SANS
    l'access_token. L'user clique alors sur le provider OIDC côté app externe,
    qui initie le flow standard via /oidc/authorize.
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

    # 3. Lookup app liée + récupérer frontend_url (page de login du client externe)
    if not oauth_client.app_id:
        raise HTTPException(
            400,
            f"Le client OIDC '{client_id}' n'est pas lié à une app HUB "
            f"(app_id requis pour la redirection de lancement)."
        )
    app_result = await db.execute(
        select(Application).where(Application.id == oauth_client.app_id)
    )
    linked_app = app_result.scalar_one_or_none()
    if not linked_app:
        raise HTTPException(404, f"App HUB liée (id={oauth_client.app_id}) introuvable")
    if not linked_app.frontend_url:
        raise HTTPException(500, f"L'app '{linked_app.slug}' n'a pas de frontend_url configuré")

    # 4. Vérifier l'accès user (admin global OU rôle explicite sur l'app)
    app_slug = linked_app.slug
    if app_slug not in apps_in_token:
        raise HTTPException(
            403, f"Accès refusé à l'app '{app_slug}' (rôle manquant)"
        )

    # 5. Lookup user pour audit — Le sub du JWT applicatif EST le username
    # (jamais renommé côté applicatif) — lookup par username, PAS par oidc_sub
    # (le fallback oidc_sub du helper résoudrait vers l'ancien porteur du
    # username en cas de recyclage, et attribuerait audit/présence au mauvais user).
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, f"Utilisateur '{username}' introuvable")

    # Présence : lancement de l'app SSO = activité sur cette app
    await ensure_session_presence(
        db, user.id, app_slug=linked_app.slug,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:255] or None,
    )

    # 6. Audit + commit
    await log_action(
        db=db,
        user_id=user.id,
        action="oidc_launch",
        target=client_id,
        request=request,
        response_status=200,
        details=f"Launch OIDC client={client_id} -> auto-logout + redirect to authorize",
    )
    await db.commit()

    # 7. Rediriger vers la page de logout de Vikunja, qui efface
    #    le cookie de session avant d'afficher la page de login.
    #    L'utilisateur clique ensuite sur « HUB POULIDOR » pour un
    #    flux OIDC frais avec le bon compte.
    #
    #    NOTE : on utilise une redirection directe (302) plutôt qu'un
    #    iframe JS car Vikunja ne purge pas sa session via iframe.
    #    Le ?provider=hub tente un auto-déclenchement si Vikunja le supporte.
    base_url = str(linked_app.frontend_url).rstrip("/")
    logout_url = f"{base_url}/logout?provider=hub"
    return RedirectResponse(url=logout_url, status_code=302)


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
    key = _token_failure_key(request, client_id)
    _check_token_failure_throttle(key)
    try:
        oauth_client = await _authenticate_client(
            request, db, client_id, client_secret
        )

        if grant_type == "authorization_code":
            response = await _handle_authorization_code_grant(
                request, db, oauth_client, code, redirect_uri, code_verifier
            )
        elif grant_type == "refresh_token":
            response = await _handle_refresh_token_grant(
                request, db, oauth_client, refresh_token
            )
        else:
            raise HTTPException(400, f"grant_type '{grant_type}' non supporté")
    except HTTPException as exc:
        # Comptabiliser uniquement les échecs (jamais les succès) : un traffic
        # SSO légitime ne génère pas d'échecs et ne peut pas être throtté.
        if exc.status_code in (400, 401):
            _record_token_failure(key)
        raise
    _clear_token_failures(key)
    return response


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
        sub=user.oidc_sub or user.username,  # défensif : backfill pas encore passé
        username=user.username,
        client_id=oauth_client.client_id,
        nonce=auth_code.nonce,
        scope=auth_code.scope,
        settings=settings,
        uid=user.uuid,
        prenom=user.prenom,
        nom=user.nom,
    )
    access_token = create_access_token_jwt(
        sub=user.oidc_sub or user.username,
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
        sub=user.oidc_sub or user.username,  # défensif : backfill pas encore passé
        username=user.username,
        client_id=oauth_client.client_id,
        nonce=None,  # pas de nonce au refresh
        scope=rt.scope,
        settings=settings,
        uid=user.uuid,
        prenom=user.prenom,
        nom=user.nom,
    )
    access_token = create_access_token_jwt(
        sub=user.oidc_sub or user.username,
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
    """Décode et valide un access_token OIDC signé par le HUB (kid-aware)."""
    entry, public_pem = jwt_keyring.select_verification_key(credentials.credentials)
    if public_pem is None:
        raise HTTPException(401, "Token invalide: kid inconnu")
    try:
        payload = pyjwt.decode(
            credentials.credentials,
            public_pem,
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
    db: AsyncSession = Depends(get_db),
):
    """OIDC UserInfo : retourne les claims de l'utilisateur selon le scope du token."""
    if not credentials:
        raise HTTPException(401, "Authorization Bearer requis")
    payload = await _decode_oidc_access_token(credentials)

    token_sub = payload.get("sub")
    scope_str = payload.get("scope", "")
    scopes = set(scope_str.split())

    from app.oidc_helpers import compute_display_name, compute_email_claim

    # Lookup DB par oidc_sub (le sub du token est l'identifiant immuable)
    # → le username courant reste résoluble même après rename
    # (user None si token pré-migration ou user supprimé)
    result = await db.execute(select(User).where(User.oidc_sub == token_sub))
    user = result.scalar_one_or_none()

    claims = {"sub": token_sub}
    if user and user.uuid:
        claims["uid"] = user.uuid
    if "profile" in scopes:
        claims["preferred_username"] = user.username if user else token_sub
        claims["name"] = compute_display_name(
            user.prenom if user else None,
            user.nom if user else None,
            user.username if user else token_sub,
        )
    if "email" in scopes:
        claims["email"] = compute_email_claim(token_sub, settings)
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

    # 2. Lookup user (par sub = oidc_sub émis au client, fallback username
    # pour les clients qui stockent encore l'ancien sub pré-déploiement)
    user = await resolve_user_by_oidc_sub(db, payload.sub)
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
    # Le client connaît l'user sous le sub reçu dans les id_tokens (= oidc_sub
    # depuis la migration) : lui renvoyer le même, pas le username courant.
    logout_token = generate_logout_token(
        client_id=client_id,
        sub=user.oidc_sub or user.username,
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


class SelfClientRequest(BaseModel):
    """Self-service M2M : provisionne le client OIDC de l'app appelante.

    `app_slug` est requis quand le SA a accès à plusieurs applications
    (parc : SAs multi-apps) et doit appartenir à son périmètre.
    """
    redirect_uris: List[str]
    regenerate: bool = True
    scopes: List[str] = ["openid", "profile", "email"]
    app_slug: Optional[str] = None

    @field_validator("redirect_uris")
    @classmethod
    def _validate_uris(cls, v: List[str]) -> List[str]:
        if not v or len(v) > 5:
            raise ValueError("1 à 5 redirect_uris requis")
        for uri in v:
            if urlparse(uri).scheme.lower() not in ("http", "https"):
                raise ValueError(f"redirect_uri invalide (http/https requis) : {uri}")
        return v


def _resolve_self_client_app(sa_app: tuple, app_slug: Optional[str]):
    """Sélectionne l'app cible du self-client : app_slug validé contre le
    périmètre du SA, ou l'app unique si le SA est mono-app. Sinon 409/403."""
    sa, apps = sa_app
    if app_slug:
        app = next((a for a in apps if a.slug == app_slug), None)
        if app is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Application '{app_slug}' hors du périmètre du service account"
            )
        return sa, app
    if len(apps) == 1:
        return sa, apps[0]
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Service account multi-applications — précisez app_slug"
    )


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


# ============================================================
# SELF-SERVICE M2M — l'app provisionne SON client OIDC
# ============================================================

@router.post("/api/oidc/self-client")
async def upsert_self_oidc_client(
    payload: SelfClientRequest,
    sa_app: tuple = Depends(get_current_service_account),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Crée/régénère le client OIDC lié à l'app appelante (token M2M requis).

    Le secret n'est retourné qu'à la création ou à la régénération
    (stocké bcrypt côté Hub).
    """
    sa, app = _resolve_self_client_app(sa_app, payload.app_slug)
    from app.auth import get_password_hash

    result = await db.execute(
        select(OAuthClient).where(OAuthClient.app_id == app.id)
    )
    client = result.scalars().first()

    raw_secret = None
    code = 200
    if client is None:
        raw_secret = OAuthClient.generate_client_secret()
        client = OAuthClient(
            client_id=OAuthClient.generate_client_id(),
            client_secret_hash=get_password_hash(raw_secret),
            name=app.name,
            redirect_uris=payload.redirect_uris,
            scopes=payload.scopes,
            auto_approve=True,
            app_id=app.id,
        )
        db.add(client)
        code = 201
        action = "oidc_self_client_create"
    elif payload.regenerate:
        raw_secret = OAuthClient.generate_client_secret()
        client.client_secret_hash = get_password_hash(raw_secret)
        client.redirect_uris = payload.redirect_uris
        client.scopes = payload.scopes
        action = "oidc_self_client_regenerate"
    else:
        action = "oidc_self_client_get"

    await log_action(
        db=db, user_id=None, action=action,
        target=client.client_id, request=request,
        service_account_id=sa.id, response_status=code,
    )
    await db.commit()
    await db.refresh(client)

    data = _serialize_client(client)
    if raw_secret is not None:
        data["client_secret"] = raw_secret  # affiché UNE SEULE FOIS
    meta = build_oidc_metadata(get_settings())
    data["issuer"] = meta["issuer"]
    data["authorization_endpoint"] = meta["authorization_endpoint"]
    data["token_endpoint"] = meta["token_endpoint"]
    return JSONResponse(status_code=code, content=data)


@router.get("/api/oidc/self-client")
async def get_self_oidc_client(
    sa_app: tuple = Depends(get_current_service_account),
    db: AsyncSession = Depends(get_db),
    app_slug: Optional[str] = Query(None),
):
    """Métadonnées du client OIDC de l'app appelante (sans secret)."""
    _sa, app = _resolve_self_client_app(sa_app, app_slug)
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.app_id == app.id)
    )
    client = result.scalars().first()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucun client OIDC lié à cette application"
        )
    data = _serialize_client(client)
    meta = build_oidc_metadata(get_settings())
    data["issuer"] = meta["issuer"]
    data["authorization_endpoint"] = meta["authorization_endpoint"]
    data["token_endpoint"] = meta["token_endpoint"]
    return data
