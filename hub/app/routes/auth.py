from fastapi import APIRouter, Depends, HTTPException, status, Form, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, timezone
from typing import Optional
import hmac
import hashlib
import secrets
import logging

from app.database import get_db
from app.models import User, AuditLog, ServiceAccount, UserAppRole, UserSession, Application, UserTag, utcnow, get_apps_with_roles_and_workshops, workshop_role_for_app, ApprovalStatus
from app.m2m_scope import sa_apps_claim, sa_claim_is_workshop
from app.schemas import (
    Token, LoginRequest, TokenIntrospection, RefreshTokenRequest, LogoutRequest,
    PasswordChangeRequest, AppLaunchResponse,
    TotpSetupResponse, TotpConfirmRequest, TotpDisableRequest,
    RecoverVerifyRequest, RecoverVerifyResponse, RecoverResetRequest,
)
from app.routes.events import notify_stats_update, notify_event
from app.auth import (
    verify_password,
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
    rotate_refresh_token,
    revoke_token,
    revoke_all_user_tokens,
    verify_token,
    get_current_user,
    get_password_hash,
    get_settings,
    create_reset_token,
    decode_reset_token,
    password_fingerprint,
)
from app.routes.audit import start_session, end_session, log_action
from app.routes.service_accounts import encrypt_secret, decrypt_secret
from app.totp import (
    generate_totp_secret,
    build_provisioning_uri,
    build_qr_data_uri,
    verify_totp_code,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
settings = get_settings()

# ============================================================================
# RATE LIMITING
# ============================================================================
# Compteur simple en mémoire par IP pour le rate limiting des endpoints sensibles
# Compatible mono-worker (suffisant pour le déploiement actuel)
_login_attempts: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW = 60  # secondes
_RATE_LIMIT_MAX = 5  # max tentatives par fenêtre


def _get_client_ip(request: Request) -> str:
    """Retourne l'IP du client, en ne faisant confiance aux headers de proxy
    (X-Forwarded-For / X-Real-IP) que si la requête vient d'un proxy de confiance
    (réseau Docker = frontend nginx).

    Sans cette restriction, un client externe peut spoof son IP via ces headers
    pour bypasser le rate-limit de login (H7) et la décision interne/externe de
    /token/introspect qui exempte les apps internes du Bearer (H8).
    """
    from app.routes.apps import is_docker_network_ip

    direct_ip = request.client.host if request.client else "unknown"
    if direct_ip != "unknown" and is_docker_network_ip(direct_ip):
        # Requête venant d'un proxy de confiance (frontend nginx) : honorer le header.
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
    return direct_ip


def _check_rate_limit(ip: str) -> None:
    """Vérifie le rate limit pour une IP. Lève HTTPException 429 si dépassé."""
    import time
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Nettoyer les tentatives expirées
    attempts = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
    if len(attempts) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de tentatives de connexion. Réessayez dans {_RATE_LIMIT_WINDOW} secondes.",
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW)}
        )
    attempts.append(now)
    _login_attempts[ip] = attempts


# ============================================================================
# BEACON TOKEN — jeton éphémère HMAC pour sécuriser beacon-logout
# ============================================================================
_BEACON_SECRET = secrets.token_hex(32)  # Régénéré à chaque démarrage


def generate_beacon_token(user_id: int) -> str:
    """Génère un token HMAC lié au user_id pour sécuriser le beacon-logout."""
    message = f"{user_id}:{_BEACON_SECRET}"
    return hmac.new(message.encode(), digestmod=hashlib.sha256).hexdigest()[:32]


def verify_beacon_token(user_id: int, token: str) -> bool:
    """Vérifie qu'un beacon token est valide pour le user_id donné."""
    expected = generate_beacon_token(user_id)
    return hmac.compare_digest(expected, token)


@router.post("/token", response_model=Token)
async def login_for_token(
    request: Request,
    response: Response,
    grant_type: str = Form(default="password"),
    username: Optional[str] = Form(default=None),
    password: Optional[str] = Form(default=None),
    refresh_token: Optional[str] = Form(default=None),
    client_id: Optional[str] = Form(default=None),
    client_secret: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint OAuth2 unifié supportant:
    - grant_type=password : Login utilisateur classique
    - grant_type=refresh_token : Renouvellement de token
    - grant_type=client_credentials : Authentification M2M (service account)
    """
    
    # === GRANT TYPE: REFRESH TOKEN ===
    if grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="refresh_token requis"
            )
        
        result = await rotate_refresh_token(
            db, refresh_token,
            ip_address=_get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")
        )
        
        if not result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token invalide ou expiré"
            )
        
        new_refresh_token, refresh_obj = result
        
        beacon_token = None
        if refresh_obj.user:
            user = refresh_obj.user
            apps = await get_apps_with_roles_and_workshops(user, db)  # + ateliers autorisés
            sub = user.username
            beacon_token = generate_beacon_token(user.id)
            tags = user.get_tags()
            uid = str(user.uuid) if user.uuid else None
            prenom = user.prenom
            nom = user.nom
            
            # Mettre à jour l'activité de la session (le refresh = activité).
            # Si la session a été fermée par une race SSE, le refresh token
            # valide prouve que l'utilisateur est actif : recréer la session.
            # NB : start_session reste l'import module-level ; un import local
            # ferait de lui une variable locale et casserait le grant password.
            from app.routes.audit import update_session_activity
            session = await update_session_activity(db, user.id)
            if session is None:
                await start_session(
                    db, user.id,
                    ip_address=_get_client_ip(request),
                    user_agent=request.headers.get("user-agent", ""),
                )

            # Re-poser le cookie OIDC pour garder le SSO OIDC synchronisé avec
            # la session dashboard (sinon expiration après 8h malgré refresh 7j).
            from app.oidc_helpers import attach_oidc_session_cookie
            attach_oidc_session_cookie(response, user.oidc_sub or user.username, request, settings)
        else:
            sa = refresh_obj.service_account
            # Service accounts ont accès "service" aux apps
            # AN-2 : claim filtré (apps actives + approuvées uniquement)
            apps = sa_apps_claim(sa)
            sub = sa.client_id
            tags = None  # Service accounts n'ont pas de tags utilisateur
            uid = prenom = nom = None

        token, expire, jti = create_access_token(
            username=sub,
            apps=apps,
            is_service_account=refresh_obj.service_account_id is not None,
            tags=tags,
            uid=uid,
            prenom=prenom,
            nom=nom,
        )
        
        await db.commit()
        
        return Token(
            access_token=token,
            expires_at=expire,
            refresh_token=new_refresh_token,
            beacon_token=beacon_token
        )
    
    # === GRANT TYPE: CLIENT CREDENTIALS (M2M) ===
    if grant_type == "client_credentials":
        if not client_id or not client_secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="client_id et client_secret requis"
            )
        
        result = await db.execute(
            select(ServiceAccount)
            .options(selectinload(ServiceAccount.applications))
            .where(ServiceAccount.client_id == client_id)
        )
        service_account = result.scalar_one_or_none()
        
        if not service_account or not verify_password(client_secret, service_account.client_secret_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identifiants service account invalides"
            )
        
        if not service_account.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service account désactivé"
            )
        
        # Mettre à jour last_used_at
        service_account.last_used_at = utcnow()
        
        # Service accounts ont accès "service" aux apps (accès M2M)
        # AN-2 : claim filtré (apps actives + approuvées + même contexte atelier)
        apps = sa_apps_claim(service_account)
        is_workshop = sa_claim_is_workshop(service_account)
        
        token, expire, jti = create_access_token(
            username=client_id,
            apps=apps,
            is_service_account=True,
            is_workshop=is_workshop,
        )
        
        # Log
        audit = AuditLog(
            action="service_account_login",
            target=service_account.name,
            ip_address=_get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:255]
        )
        db.add(audit)
        await db.commit()
        
        # Pas de refresh token pour M2M (le client peut redemander un token)
        return Token(access_token=token, expires_at=expire)
    
    # === GRANT TYPE: PASSWORD (défaut) ===
    # Rate limiting sur le login par mot de passe
    client_ip = _get_client_ip(request)
    _check_rate_limit(client_ip)
    
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="username et password requis pour grant_type=password"
        )
    
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.tags).selectinload(UserTag.category),
        )
        .where(User.username == username)
    )
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(password, user.hashed_password):
        # Audit sécurité : tracer la tentative avec l'identifiant soumis,
        # même lorsque l'utilisateur n'existe pas. JAMAIS le mot de passe.
        await log_action(
            db,
            user_id=user.id if user else None,
            action="login_failed",
            target=username,
            details="Utilisateur inconnu" if not user else "Mot de passe incorrect",
            request=request,
            response_status=401,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants incorrects",
            headers={"WWW-Authenticate": "Bearer"}
        )

    if not user.is_active:
        await log_action(
            db,
            user_id=user.id,
            action="login_failed",
            target=username,
            details="Compte désactivé",
            request=request,
            response_status=403,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé"
        )

    # Construire les apps avec rôles
    # Construire les apps avec rôles (+ ateliers autorisés)
    apps = await get_apps_with_roles_and_workshops(user, db)
    
    token, expire, jti = create_access_token(
        username=user.username,
        apps=apps,
        tags=user.get_tags(),
        uid=str(user.uuid) if user.uuid else None,
        prenom=user.prenom,
        nom=user.nom,
    )
    
    # Créer un refresh token
    new_refresh_token = await create_refresh_token(
        db,
        user_id=user.id,
        ip_address=_get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")
    )
    
    # Démarrer une session utilisateur (audit)
    await start_session(
        db,
        user_id=user.id,
        ip_address=_get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")
    )
    
    # Log de l'action avec contexte enrichi
    await log_action(
        db,
        user_id=user.id,
        action="login",
        request=request,
        response_status=200
    )
    await db.commit()
    
    # Notifier SSE des stats mises à jour (nouvelle session)
    notify_stats_update()
    notify_event("audit_updated", {"action": "login", "username": user.username})

    # SLO poste partagé : lire l'ancien cookie AVANT de l'écraser.
    # Si un autre user était connecté sur ce poste, on collecte ses apps OIDC
    # pour que le frontend purge leurs sessions via iframe.
    from app.slo_helpers import detect_user_switch_and_collect_logouts
    remote_logout = await detect_user_switch_and_collect_logouts(
        request=request,
        db=db,
        new_username=user.username,
        settings=settings,
    )

    # SLO v2 : appeler les traducteurs M2M pour révoquer les sessions distantes
    # de l'ancien user (best-effort, ne bloque pas le login).
    if remote_logout:
        from app.slo_orchestrator import propagate_remote_revoke
        # Extraire l'identifiant (oidc_sub ou username) depuis le cookie décodé
        from app.oidc_helpers import decode_oidc_session_cookie
        old_cookie = request.cookies.get("hub_oidc_session", "")
        old_identifier = decode_oidc_session_cookie(old_cookie, settings) or ""
        if old_identifier:
            await propagate_remote_revoke(db, old_identifier, remote_logout, settings)

    # Poser le cookie de session OIDC (permet à /oidc/authorize d'identifier
    # l'user sans re-login, pour le SSO OIDC standard type Vikunja).
    from app.oidc_helpers import attach_oidc_session_cookie
    attach_oidc_session_cookie(response, user.oidc_sub or user.username, request, settings)

    return Token(
        access_token=token,
        expires_at=expire,
        refresh_token=new_refresh_token,
        must_change_password=user.must_change_password or False,
        beacon_token=generate_beacon_token(user.id),
        remote_logout=remote_logout,
    )


@router.post("/login")
async def login_with_redirect(
    request: Request,
    login_data: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """Login avec redirection optionnelle vers une application (Authorization Code simplifié)"""
    # Rate limiting
    client_ip = _get_client_ip(request)
    _check_rate_limit(client_ip)
    
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.tags).selectinload(UserTag.category),
        )
        .where(User.username == login_data.username)
    )
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(login_data.password, user.hashed_password):
        # Audit sécurité : tracer la tentative avec l'identifiant soumis,
        # même lorsque l'utilisateur n'existe pas. JAMAIS le mot de passe.
        await log_action(
            db,
            user_id=user.id if user else None,
            action="login_failed",
            target=login_data.username,
            details="Utilisateur inconnu" if not user else "Mot de passe incorrect",
            request=request,
            response_status=401,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants incorrects"
        )

    if not user.is_active:
        await log_action(
            db,
            user_id=user.id,
            action="login_failed",
            target=login_data.username,
            details="Compte désactivé",
            request=request,
            response_status=403,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé"
        )
    
    apps = await get_apps_with_roles_and_workshops(user, db)
    
    token, expire, jti = create_access_token(
        username=user.username,
        apps=apps,
        tags=user.get_tags(),
        uid=str(user.uuid) if user.uuid else None,
        prenom=user.prenom,
        nom=user.nom,
    )
    
    # Créer un refresh token
    new_refresh_token = await create_refresh_token(
        db,
        user_id=user.id,
        ip_address=_get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")
    )
    
    # Démarrer une session utilisateur (audit)
    await start_session(
        db,
        user_id=user.id,
        ip_address=_get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")
    )
    
    # Log avec contexte enrichi
    await log_action(
        db,
        user_id=user.id,
        action="login",
        target=login_data.redirect_to,
        request=request,
        response_status=200
    )
    await db.commit()
    
    # Si redirection demandée
    if login_data.redirect_to:
        separator = "&" if "?" in login_data.redirect_to else "?"
        return RedirectResponse(
            url=f"{login_data.redirect_to}{separator}access_token={token}",
            status_code=303
        )
    
    return Token(
        access_token=token,
        expires_at=expire,
        refresh_token=new_refresh_token,
        beacon_token=generate_beacon_token(user.id),
        must_change_password=user.must_change_password or False
    )


@router.get("/me")
async def get_current_user_info(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Récupère les infos de l'utilisateur connecté.
    Crée automatiquement une nouvelle session si aucune n'est active
    (cas de reconnexion après fermeture navigateur avec token encore valide).
    """
    from app.routes.audit import start_session, get_active_session_for_user
    
    # Vérifier si l'utilisateur a une session active
    active_session = await get_active_session_for_user(db, current_user.id)
    
    if not active_session:
        # Pas de session active - en créer une nouvelle (reconnexion)
        await start_session(
            db,
            user_id=current_user.id,
            ip_address=_get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")
        )
        await db.commit()
        notify_stats_update()
    
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "is_admin": current_user.is_admin,
        "must_change_password": current_user.must_change_password or False,
        "totp_enabled": current_user.totp_enabled or False,
        "beacon_token": generate_beacon_token(current_user.id),
        "applications": [
            {
                "id": ar.application.id,
                "name": ar.application.name,
                "slug": ar.application.slug,
                "icon": ar.application.icon,
                "color": ar.application.color,
                "frontend_url": ar.application.frontend_url,
                "role": ar.role
            }
            for ar in current_user.app_roles
        ]
    }


@router.put("/change-password")
async def change_password(
    request: Request,
    password_data: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Permet à un utilisateur de changer son mot de passe.
    Vérifie l'ancien mot de passe avant de permettre le changement.
    """
    # Vérifier si l'utilisateur peut changer son mot de passe
    if current_user.can_change_password is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous n'êtes pas autorisé à modifier votre mot de passe. Contactez un administrateur."
        )
    
    # Vérifier l'ancien mot de passe
    if not verify_password(password_data.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mot de passe actuel incorrect"
        )
    
    # Vérifier que le nouveau mot de passe est différent
    if verify_password(password_data.new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le nouveau mot de passe doit être différent de l'ancien"
        )
    
    # Mettre à jour le mot de passe
    current_user.hashed_password = get_password_hash(password_data.new_password)
    current_user.must_change_password = False  # Réinitialiser le flag
    
    # Log de l'action
    audit = AuditLog(
        user_id=current_user.id,
        action="password_changed",
        ip_address=_get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:255]
    )
    db.add(audit)
    await db.commit()
    
    # Notifier via SSE
    notify_event("audit_updated", {"action": "password_changed", "username": current_user.username})
    
    return {"message": "Mot de passe modifié avec succès"}


@router.post("/totp/setup", response_model=TotpSetupResponse)
async def totp_setup(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Génère un secret TOTP en attente (non activé) et renvoie le QR code."""
    secret = generate_totp_secret()
    account = current_user.email or current_user.username
    uri = build_provisioning_uri(secret, account, settings.totp_issuer)

    current_user.totp_secret = encrypt_secret(secret)
    current_user.totp_enabled = False
    current_user.totp_confirmed_at = None
    current_user.totp_last_counter = None
    await db.commit()

    return TotpSetupResponse(
        otpauth_uri=uri,
        secret=secret,
        qr_data_uri=build_qr_data_uri(uri),
    )


@router.post("/totp/confirm")
async def totp_confirm(
    request: Request,
    payload: TotpConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirme l'enrôlement TOTP en vérifiant un premier code."""
    if not current_user.totp_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun enrôlement TOTP en attente")

    secret = decrypt_secret(current_user.totp_secret)
    counter = verify_totp_code(secret, payload.code, current_user.totp_last_counter)
    if counter is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Code invalide")

    current_user.totp_enabled = True
    current_user.totp_confirmed_at = utcnow()
    current_user.totp_last_counter = counter

    db.add(AuditLog(
        user_id=current_user.id,
        action="totp_enabled",
        ip_address=_get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:255],
    ))
    await db.commit()
    return {"message": "Authentification par token activée"}


@router.post("/totp/disable")
async def totp_disable(
    request: Request,
    payload: TotpDisableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Désactive le TOTP après vérification du mot de passe actuel."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mot de passe actuel incorrect")

    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_confirmed_at = None
    current_user.totp_last_counter = None

    db.add(AuditLog(
        user_id=current_user.id,
        action="totp_disabled",
        ip_address=_get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:255],
    ))
    await db.commit()
    return {"message": "Authentification par token désactivée"}


_RECOVER_GENERIC_ERROR = "Code de récupération invalide ou récupération indisponible"


@router.post("/recover/verify", response_model=RecoverVerifyResponse)
async def recover_verify(
    request: Request,
    payload: RecoverVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Étape 1 de récupération : vérifie le code TOTP, renvoie un jeton de reset."""
    client_ip = _get_client_ip(request)
    _check_rate_limit(client_ip)

    result = await db.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()

    if not user or not user.is_active or not user.totp_enabled or not user.totp_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_RECOVER_GENERIC_ERROR)

    secret = decrypt_secret(user.totp_secret)
    counter = verify_totp_code(secret, payload.totp_code, user.totp_last_counter)
    if counter is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_RECOVER_GENERIC_ERROR)

    user.totp_last_counter = counter
    db.add(AuditLog(
        user_id=user.id,
        action="password_recovery_verified",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", "")[:255],
    ))
    await db.commit()

    reset_token = create_reset_token(user.username, user.hashed_password)
    return RecoverVerifyResponse(reset_token=reset_token)


@router.post("/recover/reset")
async def recover_reset(
    request: Request,
    payload: RecoverResetRequest,
    db: AsyncSession = Depends(get_db),
):
    """Étape 2 de récupération : applique le nouveau mot de passe via le jeton."""
    client_ip = _get_client_ip(request)
    _check_rate_limit(client_ip)

    token_payload = decode_reset_token(payload.reset_token)
    username = token_payload.get("sub")

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Jeton de réinitialisation invalide ou expiré")

    if token_payload.get("pfp") != password_fingerprint(user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Jeton de réinitialisation invalide ou expiré")

    user.hashed_password = get_password_hash(payload.new_password)
    user.must_change_password = False
    db.add(AuditLog(
        user_id=user.id,
        action="password_recovered",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", "")[:255],
    ))
    await db.commit()

    notify_event("audit_updated", {"action": "password_recovered", "username": user.username})
    return {"message": "Mot de passe réinitialisé avec succès"}


@router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    revoke_all: bool = False,
    logout_data: Optional[LogoutRequest] = None
):
    """
    Déconnexion - révoque le token actuel.
    Si revoke_all=True, révoque aussi tous les refresh tokens de l'utilisateur.
    Si un refresh_token est fourni dans le body, il sera révoqué spécifiquement.
    """
    # Récupérer le token depuis le header pour obtenir le jti
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = verify_token(token)
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                await revoke_token(
                    db, jti,
                    expires_at=datetime.fromtimestamp(exp, tz=timezone.utc).replace(tzinfo=None),
                    reason="logout"
                )
        except Exception:
            pass  # Token déjà invalide, on continue
    
    # Révoquer le refresh token spécifique s'il est fourni
    if logout_data and logout_data.refresh_token:
        try:
            refresh_token_obj = await verify_refresh_token(db, logout_data.refresh_token)
            if refresh_token_obj and refresh_token_obj.user_id == current_user.id:
                refresh_token_obj.revoked_at = utcnow()
                await db.commit()
        except Exception:
            pass  # Token déjà invalide ou n'appartient pas à l'utilisateur
    
    # Révoquer tous les refresh tokens si demandé
    if revoke_all:
        await revoke_all_user_tokens(db, current_user.id)
    
    # Terminer la session utilisateur (audit)
    await end_session(db, current_user.id, reason="explicit")
    
    audit = AuditLog(
        user_id=current_user.id,
        action="logout",
        details="all_tokens_revoked" if revoke_all else "single_token",
        ip_address=_get_client_ip(request)
    )
    db.add(audit)
    await db.commit()
    
    # Notifier SSE des stats mises à jour (session terminée)
    notify_stats_update()
    notify_event("audit_updated", {"action": "logout", "username": current_user.username})
    
    return {"message": "Déconnexion réussie"}


@router.post("/beacon-logout")
async def beacon_logout(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint pour le logout via navigator.sendBeacon() lors de la fermeture du navigateur.
    
    Sécurisé par un beacon_token HMAC lié au user_id, fourni au login.
    sendBeacon ne peut pas envoyer de headers Authorization, donc on utilise
    un jeton éphémère régénéré à chaque démarrage du serveur.
    """
    try:
        body = await request.body()
        data = {}
        if body:
            import json
            data = json.loads(body.decode('utf-8'))
        
        user_id = data.get('user_id')
        beacon_token = data.get('beacon_token', '')
        reason = data.get('reason', 'browser_closed')
        
        if not user_id:
            return {"message": "user_id manquant"}
        
        # Vérifier le beacon token
        if not beacon_token or not verify_beacon_token(user_id, beacon_token):
            logger.warning(f"⚠️ beacon-logout rejeté: token invalide pour user_id={user_id} depuis {_get_client_ip(request)}")
            return {"message": "Token invalide"}
        
        # Fermer la session de l'utilisateur
        session = await end_session(db, user_id, reason=reason)
        
        if session:
            await db.commit()
            notify_stats_update()
            return {"message": "Session fermée", "user_id": user_id}
        
        return {"message": "Pas de session active"}
    except Exception as e:
        return {"message": f"Erreur: {str(e)}"}


@router.get("/public-key")
async def get_public_key():
    """Expose la clé publique active pour les applications clientes"""
    from app import jwt_keyring
    return {"public_key": jwt_keyring.get_active_pem_pair()[1]}


@router.get("/.well-known/jwks.json")
async def get_jwks():
    """
    Endpoint JWKS (JSON Web Key Set) - Standard OIDC.

    Publie les clés publiques RSA acceptées en vérification : la clé active
    (signature) + les clés `retiring` pendant la fenêtre d'overlap (spec §5.3).
    """
    from app import jwt_keyring
    return jwt_keyring.build_jwks()


@router.post("/token/introspect", response_model=TokenIntrospection)
async def introspect_token(
    request: Request,
    token: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Introspection de token (RFC 7662).
    Permet aux applications de vérifier si un token est valide et non révoqué.

    Sécurité : les appels internes (réseaux whitelistés = apps du parc) sont
    autorisés sans authentification supplémentaire. Tout appel externe doit
    présenter un token Bearer valide (Authorization: Bearer ...), conformément
    à la RFC 7662 §2.1, sinon 401.
    """
    from app.routes.apps import is_docker_network_ip

    client_ip = _get_client_ip(request)
    auth_header = request.headers.get("authorization", "")

    if not is_docker_network_ip(client_ip):
        # Appel externe : exiger un token Bearer valide pour introspecter.
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Introspection depuis un réseau externe : un token Bearer valide est requis (Authorization: Bearer ...).",
            )
        try:
            verify_token(auth_header.removeprefix("Bearer ").strip())
        except Exception:
            raise HTTPException(status_code=401, detail="Token d'autorisation invalide.")
    elif not auth_header:
        logger.warning(f"⚠️ DEPRECATED: Introspection interne sans Authorization depuis {client_ip}")
    try:
        payload = verify_token(token)
        jti = payload.get("jti")
        
        # Vérifier si le token est révoqué
        if jti:
            from app.auth import is_token_revoked
            if await is_token_revoked(db, jti):
                return TokenIntrospection(active=False)
        
        return TokenIntrospection(
            active=True,
            sub=payload.get("sub"),
            apps=payload.get("apps"),
            exp=payload.get("exp"),
            iat=payload.get("iat"),
            jti=jti,
            token_type="Bearer",
            client_id=payload.get("sub") if payload.get("type") == "service_account" else None
        )
    except Exception:
        return TokenIntrospection(active=False)


@router.post("/token/refresh", response_model=Token)
async def refresh_access_token(
    request: Request,
    response: Response,
    refresh_data: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Renouvelle un access token en utilisant un refresh token.
    Le refresh token est automatiquement rotaté (l'ancien est invalidé).
    """
    result = await rotate_refresh_token(
        db, refresh_data.refresh_token,
        ip_address=_get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")
    )
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalide ou expiré"
        )
    
    new_refresh_token, refresh_obj = result
    
    beacon_token = None
    if refresh_obj.user:
        user = refresh_obj.user
        apps = await get_apps_with_roles_and_workshops(user, db)
        sub = user.username
        is_sa = False
        beacon_token = generate_beacon_token(user.id)
        tags = user.get_tags()
        uid = str(user.uuid) if user.uuid else None
        prenom = user.prenom
        nom = user.nom

        # Re-poser le cookie OIDC pour garder le SSO OIDC synchronisé avec
        # la session dashboard (sinon expiration après 8h malgré refresh 7j).
        from app.oidc_helpers import attach_oidc_session_cookie
        attach_oidc_session_cookie(response, user.oidc_sub or user.username, request, settings)
    else:
        sa = refresh_obj.service_account
        # Service accounts ont accès "service" aux apps
        # AN-2 : claim filtré (apps actives + approuvées uniquement)
        apps = sa_apps_claim(sa)
        sub = sa.client_id
        is_sa = True
        tags = None  # Service accounts n'ont pas de tags utilisateur
        uid = prenom = nom = None

    token, expire, jti = create_access_token(
        username=sub,
        apps=apps,
        is_service_account=is_sa,
        tags=tags,
        uid=uid,
        prenom=prenom,
        nom=nom,
    )
    
    return Token(
        access_token=token,
        expires_at=expire,
        refresh_token=new_refresh_token,
        beacon_token=beacon_token
    )


@router.post("/apps/{app_slug}/launch", response_model=AppLaunchResponse)
async def create_app_launch_token(
    app_slug: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Prépare l'ouverture d'une application depuis le dashboard Hub.
    Retourne un token de lancement frais et une URL d'entrée privilégiant callback_url.
    """
    result = await db.execute(
        select(Application).where(Application.slug == app_slug)
    )
    app = result.scalar_one_or_none()

    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_slug}' non trouvée"
        )

    if not app.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Application désactivée"
        )

    raw_launch_url = app.callback_url or app.frontend_url
    if not raw_launch_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application sans URL de lancement configurée"
        )
    launch_url = str(raw_launch_url)
    current_user_id = int(current_user.id)
    current_username = str(current_user.username)
    app_slug_value = str(app.slug)
    app_name = str(app.name)

    user_role = current_user.get_role_for_app(app_slug)
    if not current_user.is_admin and not user_role:
        # Accès atelier : {slug}-ws autorisé si activation sur l'app de base
        user_role = await workshop_role_for_app(db, current_user.id, app_slug)
        if not user_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Accès refusé à l'application {app_slug}"
            )

    apps = await get_apps_with_roles_and_workshops(current_user, db)
    role = user_role
    if current_user.is_admin and app_slug not in apps:
        apps[app_slug] = "admin"
        role = "admin"
    elif current_user.is_admin and not role:
        role = apps.get(app_slug, "admin")

    token, expire, jti = create_access_token(
        username=current_username,
        apps=apps,
        tags=current_user.get_tags(),
        uid=str(current_user.uuid) if current_user.uuid else None,
        prenom=current_user.prenom,
        nom=current_user.nom,
    )

    from app.routes.audit import update_session_activity
    await update_session_activity(db, current_user_id, app_slug=app_slug)
    await log_action(
        db=db,
        user_id=current_user_id,
        action="access_app",
        target=app_slug,
        request=request,
        response_status=200,
        details=f"Lancement de {app_name} avec rôle: {role}"
    )
    await db.commit()

    return AppLaunchResponse(
        access_token=token,
        expires_at=expire,
        launch_url=launch_url,
        app_slug=app_slug_value,
        role=role or "user"
    )


@router.post("/sso")
async def sso_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    app_slug: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Login SSO - Authentifie l'utilisateur et redirige vers l'application cible.
    
    Cette route est utilisée par le frontend du Hub pour le flow SSO :
    1. L'utilisateur se connecte sur le Hub
    2. Le Hub vérifie les credentials
    3. Le Hub vérifie que l'utilisateur a accès à l'app demandée
    4. Le Hub redirige vers le callback de l'app avec le token
    """
    from app.models import Application
    
    # Rate limiting
    client_ip = _get_client_ip(request)
    _check_rate_limit(client_ip)
    
    # 1. Vérifier l'utilisateur
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.tags).selectinload(UserTag.category),
        )
        .where(User.username == username)
    )
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants incorrects"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé"
        )
    
    # 2. Récupérer l'application
    app_result = await db.execute(
        select(Application).where(Application.slug == app_slug)
    )
    app = app_result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_slug}' non trouvée"
        )
    
    if not app.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Application désactivée"
        )
    
    # 3. Vérifier que l'utilisateur a accès à l'app (sauf si admin Hub)
    if not user.is_admin:
        user_role = user.get_role_for_app(app_slug)
        if not user_role:
            # Accès atelier : {slug}-ws autorisé si activation sur l'app de base
            user_role = await workshop_role_for_app(db, user.id, app_slug)
            if not user_role:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Accès refusé à l'application {app_slug}"
                )
    
    # 4. Créer le token avec accès à l'app
    apps = await get_apps_with_roles_and_workshops(user, db)
    
    # S'assurer que l'app demandée est dans les apps (admin a toujours accès)
    if user.is_admin and app_slug not in apps:
        apps[app_slug] = "admin"
    
    token, expire, jti = create_access_token(
        username=user.username,
        apps=apps,
        tags=user.get_tags(),
        uid=str(user.uuid) if user.uuid else None,
        prenom=user.prenom,
        nom=user.nom,
    )
    
    # 5. Log
    audit = AuditLog(
        user_id=user.id,
        action="sso_login",
        target=app_slug,
        ip_address=_get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:255]
    )
    db.add(audit)
    await db.commit()
    
    # 6. Redirection vers le callback de l'app
    callback_url = app.callback_url
    if not callback_url:
        # Fallback sur frontend_url + /callback
        callback_url = f"{app.frontend_url}/callback" if app.frontend_url else None
    
    if not callback_url:
        # Si pas de callback, retourner le token en JSON
        return {"access_token": token, "expires_at": expire.isoformat()}
    
    separator = "&" if "?" in callback_url else "?"
    return RedirectResponse(
        url=f"{callback_url}{separator}access_token={token}",
        status_code=303
    )

