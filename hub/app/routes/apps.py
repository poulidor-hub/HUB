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
from app.schemas import (
    AppCreate, AppCreateSimplified, AppUpdate, AppResponse, AppWithUsers,
    DockerScanResponse, DockerContainerInfo, DockerPortInfo
)
from app.auth import get_current_admin, get_current_user, get_password_hash, get_settings
from app.routes.service_accounts import save_service_account_credentials
from app.routes.audit import log_action

router = APIRouter(prefix="/api/apps", tags=["Applications"])
settings = get_settings()
logger = logging.getLogger(__name__)


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


@router.post("/access/{app_slug}")
async def log_app_access(
    app_slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Enregistre l'accès d'un utilisateur à une application.
    Appelé par le frontend avant de rediriger vers l'application.
    """
    # Vérifier que l'app existe
    result = await db.execute(select(Application).where(Application.slug == app_slug))
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    # Vérifier que l'utilisateur a accès à cette app
    user_role = current_user.get_role_for_app(app_slug)
    if not current_user.is_admin and not user_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès non autorisé à cette application"
        )
    
    # Logger l'accès
    await log_action(
        db=db,
        user_id=current_user.id,
        action="access_app",
        target=app_slug,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
        details=f"Accès à {app.name} avec rôle: {user_role or 'admin'}"
    )
    
    await db.commit()
    
    return {
        "message": "Accès enregistré",
        "app_slug": app_slug,
        "app_name": app.name,
        "user_role": user_role or "admin"
    }


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


# ===== Docker Scanner =====

@router.get("/docker/scan", response_model=DockerScanResponse)
async def scan_docker_containers(
    include_stopped: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Scanne les conteneurs Docker en cours d'exécution sur le serveur.
    
    Retourne la liste des conteneurs avec leurs ports exposés,
    permettant d'ajouter facilement des applications au Hub.
    
    ⚠️ Admin uniquement - Nécessite l'accès au socket Docker.
    
    Args:
        include_stopped: Inclure les conteneurs arrêtés (défaut: False)
    """
    try:
        from app.docker_scanner import docker_scanner
    except ImportError as e:
        logger.warning(f"Module docker_scanner non disponible: {e}")
        return DockerScanResponse(
            available=False,
            error="Module Docker Scanner non disponible"
        )
    
    # Récupérer les slugs des applications déjà enregistrées
    result = await db.execute(select(Application.slug))
    existing_slugs = {row[0] for row in result.fetchall()}
    
    # Scanner Docker
    try:
        docker_info = docker_scanner.get_docker_info()
        
        if not docker_info.get("available"):
            return DockerScanResponse(
                available=False,
                error=docker_info.get("error", "Docker non accessible")
            )
        
        containers = docker_scanner.scan_containers(include_stopped=include_stopped)
        
        # Convertir en schémas Pydantic
        discovered = []
        for container in containers:
            # Marquer si le slug suggéré est déjà utilisé
            suggested_slug = container.suggested_slug
            if suggested_slug in existing_slugs:
                # Ajouter un suffixe pour éviter les conflits
                i = 2
                while f"{suggested_slug}-{i}" in existing_slugs:
                    i += 1
                suggested_slug = f"{suggested_slug}-{i}"
            
            discovered.append(DockerContainerInfo(
                container_id=container.container_id,
                name=container.name,
                image=container.image,
                status=container.status,
                ports=[
                    DockerPortInfo(
                        container_port=p.container_port,
                        host_port=p.host_port,
                        protocol=p.protocol
                    )
                    for p in container.ports
                ],
                suggested_slug=suggested_slug,
                suggested_name=container.suggested_name,
                labels=container.labels
            ))
        
        logger.info(f"Scan Docker: {len(discovered)} conteneurs découverts par {current_user.username}")
        
        return DockerScanResponse(
            available=True,
            docker_version=docker_info.get("version"),
            containers_running=docker_info.get("containers_running", 0),
            containers_total=docker_info.get("containers_total", 0),
            discovered=discovered
        )
        
    except Exception as e:
        logger.error(f"Erreur lors du scan Docker: {e}")
        return DockerScanResponse(
            available=False,
            error=str(e)
        )
