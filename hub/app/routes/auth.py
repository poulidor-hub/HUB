from fastapi import APIRouter, Depends, HTTPException, status, Form, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
from typing import Optional

from app.database import get_db
from app.models import User, AuditLog, ServiceAccount, UserAppRole, UserSession
from app.schemas import Token, LoginRequest, TokenIntrospection, RefreshTokenRequest, LogoutRequest
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
    get_settings
)
from app.routes.audit import start_session, end_session

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
settings = get_settings()


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
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")
        )
        
        if not result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token invalide ou expiré"
            )
        
        new_refresh_token, refresh_obj = result
        
        if refresh_obj.user:
            user = refresh_obj.user
            roles = ["admin"] if user.is_admin else ["user"]
            apps = user.get_apps_with_roles()  # Dict {app_slug: role}
            sub = user.username
        else:
            sa = refresh_obj.service_account
            roles = ["service_account"]
            # Service accounts ont accès "service" aux apps
            apps = {app.slug: "service" for app in sa.applications}
            sub = sa.client_id
        
        token, expire, jti = create_access_token(
            username=sub,
            roles=roles,
            apps=apps,
            is_service_account=refresh_obj.service_account_id is not None
        )
        
        return Token(
            access_token=token,
            expires_at=expire,
            refresh_token=new_refresh_token
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
        service_account.last_used_at = datetime.utcnow()
        
        roles = ["service_account"]
        # Service accounts ont accès "service" aux apps (accès M2M)
        apps = {app.slug: "service" for app in service_account.applications}
        
        token, expire, jti = create_access_token(
            username=client_id,
            roles=roles,
            apps=apps,
            is_service_account=True
        )
        
        # Log
        audit = AuditLog(
            action="service_account_login",
            target=service_account.name,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:255]
        )
        db.add(audit)
        await db.commit()
        
        # Pas de refresh token pour M2M (le client peut redemander un token)
        return Token(access_token=token, expires_at=expire)
    
    # === GRANT TYPE: PASSWORD (défaut) ===
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="username et password requis pour grant_type=password"
        )
    
    result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
        .where(User.username == username)
    )
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants incorrects",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé"
        )
    
    # Construire les rôles globaux et apps avec rôles
    roles = ["admin"] if user.is_admin else ["user"]
    apps = user.get_apps_with_roles()  # Dict {app_slug: role}
    
    token, expire, jti = create_access_token(
        username=user.username,
        roles=roles,
        apps=apps
    )
    
    # Créer un refresh token
    new_refresh_token = await create_refresh_token(
        db,
        user_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")
    )
    
    # Démarrer une session utilisateur (audit)
    await start_session(
        db,
        user_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")
    )
    
    # Log de l'action
    audit = AuditLog(
        user_id=user.id,
        action="login",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:255]
    )
    db.add(audit)
    await db.commit()
    
    return Token(
        access_token=token,
        expires_at=expire,
        refresh_token=new_refresh_token
    )


@router.post("/login")
async def login_with_redirect(
    request: Request,
    login_data: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """Login avec redirection optionnelle vers une application (Authorization Code simplifié)"""
    result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
        .where(User.username == login_data.username)
    )
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants incorrects"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé"
        )
    
    roles = ["admin"] if user.is_admin else ["user"]
    apps = user.get_apps_with_roles()  # Dict {app_slug: role}
    
    token, expire, jti = create_access_token(
        username=user.username,
        roles=roles,
        apps=apps
    )
    
    # Créer un refresh token
    new_refresh_token = await create_refresh_token(
        db,
        user_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")
    )
    
    # Démarrer une session utilisateur (audit)
    await start_session(
        db,
        user_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")
    )
    
    # Log
    audit = AuditLog(
        user_id=user.id,
        action="login",
        target=login_data.redirect_to,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:255]
    )
    db.add(audit)
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
        refresh_token=new_refresh_token
    )


@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """Récupère les infos de l'utilisateur connecté"""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "is_admin": current_user.is_admin,
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
                    expires_at=datetime.fromtimestamp(exp),
                    reason="logout"
                )
        except:
            pass  # Token déjà invalide, on continue
    
    # Révoquer le refresh token spécifique s'il est fourni
    if logout_data and logout_data.refresh_token:
        try:
            refresh_token_obj = await verify_refresh_token(db, logout_data.refresh_token)
            if refresh_token_obj and refresh_token_obj.user_id == current_user.id:
                refresh_token_obj.revoked_at = datetime.utcnow()
                await db.commit()
        except:
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
        ip_address=request.client.host if request.client else None
    )
    db.add(audit)
    await db.commit()
    
    return {"message": "Déconnexion réussie"}


@router.get("/public-key")
async def get_public_key():
    """Expose la clé publique pour les applications clientes"""
    return {"public_key": settings.jwt_public_key}


@router.get("/.well-known/jwks.json")
async def get_jwks():
    """
    Endpoint JWKS (JSON Web Key Set) - Standard OIDC.
    
    Expose la clé publique au format JWK pour permettre aux applications
    de récupérer dynamiquement la clé de vérification des tokens JWT.
    
    Les applications doivent appeler cet endpoint à chaque démarrage
    pour récupérer la clé publique actuelle.
    
    Returns:
        dict: JWKS contenant la clé publique RSA
    """
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    
    # Charger la clé publique PEM
    public_key_pem = settings.jwt_public_key.encode('utf-8')
    public_key = serialization.load_pem_public_key(public_key_pem, backend=default_backend())
    
    # Extraire les composants RSA (n et e)
    public_numbers = public_key.public_numbers()
    
    # Convertir en base64url (sans padding)
    def int_to_base64url(value: int) -> str:
        """Convertit un entier en base64url sans padding"""
        # Calculer le nombre d'octets nécessaires
        byte_length = (value.bit_length() + 7) // 8
        value_bytes = value.to_bytes(byte_length, byteorder='big')
        return base64.urlsafe_b64encode(value_bytes).rstrip(b'=').decode('ascii')
    
    # Construire le JWK
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": settings.jwt_algorithm,
        "kid": "hub-jwt-key-1",  # Key ID pour identifier cette clé
        "n": int_to_base64url(public_numbers.n),
        "e": int_to_base64url(public_numbers.e)
    }
    
    return {
        "keys": [jwk]
    }


@router.post("/token/introspect", response_model=TokenIntrospection)
async def introspect_token(
    token: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Introspection de token (RFC 7662).
    Permet aux applications de vérifier si un token est valide et non révoqué.
    """
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
            roles=payload.get("roles"),
            apps=payload.get("apps"),
            exp=payload.get("exp"),
            iat=payload.get("iat"),
            jti=jti,
            token_type="Bearer",
            client_id=payload.get("sub") if payload.get("type") == "service_account" else None
        )
    except:
        return TokenIntrospection(active=False)


@router.post("/token/refresh", response_model=Token)
async def refresh_access_token(
    request: Request,
    refresh_data: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Renouvelle un access token en utilisant un refresh token.
    Le refresh token est automatiquement rotaté (l'ancien est invalidé).
    """
    result = await rotate_refresh_token(
        db, refresh_data.refresh_token,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")
    )
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalide ou expiré"
        )
    
    new_refresh_token, refresh_obj = result
    
    if refresh_obj.user:
        user = refresh_obj.user
        roles = ["admin"] if user.is_admin else ["user"]
        apps = [app.slug for app in user.applications]
        sub = user.username
        is_sa = False
    else:
        sa = refresh_obj.service_account
        roles = ["service_account"]
        apps = [app.slug for app in sa.applications]
        sub = sa.client_id
        is_sa = True
    
    token, expire, jti = create_access_token(
        username=sub,
        roles=roles,
        apps=apps,
        is_service_account=is_sa
    )
    
    return Token(
        access_token=token,
        expires_at=expire,
        refresh_token=new_refresh_token
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
    
    # 1. Vérifier l'utilisateur
    result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
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
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Accès refusé à l'application {app_slug}"
            )
    
    # 4. Créer le token avec accès à l'app
    roles = ["admin"] if user.is_admin else ["user"]
    apps = user.get_apps_with_roles()
    
    # S'assurer que l'app demandée est dans les apps (admin a toujours accès)
    if user.is_admin and app_slug not in apps:
        apps[app_slug] = "admin"
    
    token, expire, jti = create_access_token(
        username=user.username,
        roles=roles,
        apps=apps
    )
    
    # 5. Log
    audit = AuditLog(
        user_id=user.id,
        action="sso_login",
        target=app_slug,
        ip_address=request.client.host if request.client else None,
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

