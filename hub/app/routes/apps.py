from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional, Union
import re

from pathlib import Path
import logging

from app.database import get_db
from app.models import Application, User, ServiceAccount, UserAppRole
from app.schemas import AppCreate, AppCreateSimplified, AppUpdate, AppResponse, AppWithUsers
from app.auth import get_current_admin, get_current_user, get_password_hash, get_settings
from app.routes.service_accounts import save_service_account_credentials

router = APIRouter(prefix="/api/apps", tags=["Applications"])
settings = get_settings()


def generate_slug(name: str) -> str:
    """Génère un slug à partir du nom"""
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug


@router.get("/", response_model=List[AppResponse])
async def list_apps(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Liste les applications (admin voit tout, user voit les siennes)"""
    if current_user.is_admin:
        result = await db.execute(select(Application))
        return result.scalars().all()
    else:
        # Retourne uniquement les apps de l'utilisateur via app_roles
        result = await db.execute(
            select(User)
            .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
            .where(User.id == current_user.id)
        )
        user = result.scalar_one()
        return [ar.application for ar in user.app_roles]


@router.get("/all")
async def list_all_apps_with_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Liste toutes les applications avec leurs utilisateurs et rôles (admin uniquement)"""
    result = await db.execute(
        select(Application).options(
            selectinload(Application.user_roles).selectinload(UserAppRole.user)
        )
    )
    apps = result.scalars().all()
    return [AppWithUsers.from_orm_model(app) for app in apps]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_app(
    app_data: AppCreateSimplified,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Crée une nouvelle application (admin uniquement).
    
    Utilise les ports pour construire automatiquement les URLs.
    Par défaut, utilise le même host que le Hub.
    
    Si `create_service_account=true`, un service account M2M est créé
    et les credentials sont sauvegardés dans `secrets/service_accounts/`.
    """
    # Vérifier si le slug existe
    existing = await db.execute(
        select(Application).where(Application.slug == app_data.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce slug existe déjà"
        )
    
    # Déterminer le host (custom ou celui du Hub via config)
    if app_data.custom_host:
        host = app_data.custom_host
    else:
        # Utiliser le host configuré ou auto-détecté
        host = settings.get_hub_host()
    
    # Construire les URLs à partir des ports
    api_url = f"http://{host}:{app_data.api_port}"
    frontend_url = f"http://{host}:{app_data.frontend_port}" if app_data.frontend_port else None
    callback_url = f"{frontend_url}/callback" if frontend_url else None
    
    # Créer l'application avec les URLs construites
    app = Application(
        name=app_data.name,
        slug=app_data.slug,
        description=app_data.description,
        icon=app_data.icon,
        color=app_data.color,
        api_url=api_url,
        frontend_url=frontend_url,
        callback_url=callback_url,
        requires_auth=app_data.requires_auth
    )
    db.add(app)
    await db.flush()  # Pour obtenir l'ID de l'app
    
    # Créer un service account uniquement si demandé
    service_account_info = None
    if app_data.create_service_account:
        client_id = ServiceAccount.generate_client_id()
        client_secret = ServiceAccount.generate_client_secret()
        
        service_account = ServiceAccount(
            name=f"SA - {app.name}",
            description=f"Service account pour l'application {app.name}",
            client_id=client_id,
            client_secret_hash=get_password_hash(client_secret)
        )
        db.add(service_account)
        await db.flush()
        
        # Associer le service account à l'application
        result = await db.execute(
            select(ServiceAccount)
            .options(selectinload(ServiceAccount.applications))
            .where(ServiceAccount.id == service_account.id)
        )
        service_account = result.scalar_one()
        service_account.applications.append(app)
        
        # Sauvegarder les credentials automatiquement
        secrets_file = save_service_account_credentials(
            name=app_data.slug,
            client_id=client_id,
            client_secret=client_secret,
            app_slugs=[app_data.slug]
        )
        
        service_account_info = {
            "id": service_account.id,
            "name": service_account.name,
            "client_id": client_id,
            "client_secret": client_secret,
            "secrets_file": secrets_file,
            "message": f"📋 Ajoutez ces credentials dans le .env de votre application {app_data.slug}:\n"
                      f"   CLIENT_ID={client_id}\n"
                      f"   CLIENT_SECRET={client_secret}\n"
                      f"   HUB_HOST={host}"
        }
    
    await db.commit()
    await db.refresh(app)
    
    response = {
        "application": {
            "id": app.id,
            "name": app.name,
            "slug": app.slug,
            "description": app.description,
            "icon": app.icon,
            "color": app.color,
            "api_url": app.api_url,
            "frontend_url": app.frontend_url,
            "callback_url": app.callback_url,
            "is_active": app.is_active,
            "requires_auth": app.requires_auth,
            "created_at": app.created_at
        }
    }
    
    if service_account_info:
        response["service_account"] = service_account_info
    else:
        response["hint"] = "💡 Pour créer un service account M2M, ajoutez 'create_service_account: true' ou utilisez POST /api/service-accounts/"
    
    return response


@router.get("/{app_id}", response_model=AppWithUsers)
async def get_app(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Récupère une application par ID (admin uniquement)"""
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.users))
        .where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    return app


@router.patch("/{app_id}", response_model=AppResponse)
async def update_app(
    app_id: int,
    app_data: AppUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Met à jour une application (admin uniquement)"""
    result = await db.execute(
        select(Application).where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    update_data = app_data.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        setattr(app, field, value)
    
    await db.commit()
    await db.refresh(app)
    
    return app


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Supprime une application (admin uniquement)"""
    result = await db.execute(
        select(Application).where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    await db.delete(app)
    await db.commit()


@router.post("/{app_id}/users")
async def set_app_users(
    app_id: int,
    user_ids: List[int],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Définit les utilisateurs ayant accès à une application"""
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.users))
        .where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    # Récupérer les users
    users_result = await db.execute(
        select(User).where(User.id.in_(user_ids))
    )
    users = users_result.scalars().all()
    
    app.users = list(users)
    await db.commit()
    
    return {"message": f"Utilisateurs mis à jour pour {app.name}", "users": [u.username for u in users]}
