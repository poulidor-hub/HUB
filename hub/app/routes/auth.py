from fastapi import APIRouter, Depends, HTTPException, status, Form, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import timedelta

from app.database import get_db
from app.models import User, AuditLog
from app.schemas import Token, LoginRequest
from app.auth import (
    verify_password,
    create_access_token,
    get_current_user,
    get_settings
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
settings = get_settings()


@router.post("/token", response_model=Token)
async def login_for_token(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """Endpoint OAuth2 standard pour obtenir un token"""
    result = await db.execute(
        select(User)
        .options(selectinload(User.applications))
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
    
    # Construire les rôles et apps
    roles = ["admin"] if user.is_admin else ["user"]
    apps = [app.slug for app in user.applications]
    
    token, expire = create_access_token(
        username=user.username,
        roles=roles,
        apps=apps
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
    
    return Token(access_token=token, expires_at=expire)


@router.post("/login")
async def login_with_redirect(
    request: Request,
    login_data: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """Login avec redirection optionnelle vers une application"""
    result = await db.execute(
        select(User)
        .options(selectinload(User.applications))
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
    apps = [app.slug for app in user.applications]
    
    token, expire = create_access_token(
        username=user.username,
        roles=roles,
        apps=apps
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
    
    return Token(access_token=token, expires_at=expire)


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
                "id": app.id,
                "name": app.name,
                "slug": app.slug,
                "icon": app.icon,
                "color": app.color,
                "frontend_url": app.frontend_url
            }
            for app in current_user.applications
        ]
    }


@router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Déconnexion (log uniquement, le token reste valide côté client)"""
    audit = AuditLog(
        user_id=current_user.id,
        action="logout",
        ip_address=request.client.host if request.client else None
    )
    db.add(audit)
    await db.commit()
    
    return {"message": "Déconnexion réussie"}


@router.get("/public-key")
async def get_public_key():
    """Expose la clé publique pour les applications clientes"""
    return {"public_key": settings.jwt_public_key}
