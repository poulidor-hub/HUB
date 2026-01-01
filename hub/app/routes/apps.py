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
    DockerScanResponse, DockerContainerInfo, DockerPortInfo,
    AppRegisterRequest, AppRegisterResponse, AppCredentialsResponse
)
from app.auth import get_current_admin, get_current_user, get_password_hash, get_settings
from app.routes.service_accounts import save_service_account_credentials
from app.routes.audit import log_action

router = APIRouter(prefix="/api/apps", tags=["Applications"])
settings = get_settings()
logger = logging.getLogger(__name__)


# ===== IP Whitelist pour auto-enregistrement =====
# Réseaux Docker autorisés pour l'auto-enregistrement (sans authentification)
DOCKER_NETWORK_PREFIXES = [
    "172.17.",      # docker0 (bridge par défaut)
    "172.18.",      # Réseaux Docker custom
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "10.0.",        # Réseaux Docker overlay
    "192.168.",     # Réseaux locaux
]


def is_docker_network_ip(ip: str) -> bool:
    """Vérifie si l'IP provient d'un réseau Docker autorisé"""
    if not ip:
        return False
    return any(ip.startswith(prefix) for prefix in DOCKER_NETWORK_PREFIXES)


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
    frontend_url = None
    callback_url = None

    if app_data.frontend_port:
        frontend_protocol = "https" if app_data.frontend_https else "http"
        frontend_url = f"{frontend_protocol}://{host}:{app_data.frontend_port}"
        callback_url = f"{frontend_url}/callback"
    
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


# ===== Auto-Registration Endpoints =====

@router.post("/register", response_model=AppRegisterResponse)
async def register_app(
    app_data: AppRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Auto-enregistrement d'une application depuis le réseau Docker.
    
    ⚠️ Accessible UNIQUEMENT depuis les réseaux Docker (IP whitelist).
    
    L'application envoie son slug (défini dans son .env) et reçoit :
    - Ses credentials M2M (client_id, client_secret) si nouvelle app
    - Un message de confirmation si l'app existe déjà
    
    Flux :
    1. L'app démarre et appelle cet endpoint avec son slug
    2. Le Hub crée l'app + service account si elle n'existe pas
    3. L'app reçoit ses credentials et les stocke en mémoire (pas dans .env)
    """
    client_ip = request.client.host if request.client else None
    
    # Vérifier que l'IP provient d'un réseau Docker
    if not is_docker_network_ip(client_ip):
        logger.warning(f"🚫 Tentative d'auto-enregistrement depuis IP non autorisée: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Auto-enregistrement autorisé uniquement depuis le réseau Docker (IP: {client_ip})"
        )
    
    logger.info(f"📝 Demande d'auto-enregistrement pour '{app_data.slug}' depuis {client_ip}")
    
    # Vérifier si l'application existe déjà
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.service_accounts))
        .where(Application.slug == app_data.slug)
    )
    existing_app = result.scalar_one_or_none()
    
    if existing_app:
        # Application existante - retourner les infos sans le secret
        logger.info(f"✅ Application '{app_data.slug}' déjà enregistrée")
        
        # Trouver le service account associé
        sa = next((sa for sa in existing_app.service_accounts), None)
        
        return AppRegisterResponse(
            message=f"Application '{app_data.slug}' déjà enregistrée. Utilisez /api/apps/me/credentials pour récupérer vos credentials.",
            application=AppResponse.model_validate(existing_app),
            credentials={"client_id": sa.client_id} if sa else None,
            is_new=False
        )
    
    # Nouvelle application - créer l'app et le service account
    host = settings.get_hub_host()
    api_url = f"http://{host}:{app_data.api_port}"
    frontend_url = f"http://{host}:{app_data.frontend_port}" if app_data.frontend_port else None
    callback_url = f"{frontend_url}/callback" if frontend_url else None
    
    app = Application(
        name=app_data.name or app_data.slug.replace("-", " ").title(),
        slug=app_data.slug,
        description=app_data.description or f"Application {app_data.slug} (auto-enregistrée)",
        api_url=api_url,
        frontend_url=frontend_url,
        callback_url=callback_url,
        requires_auth=True
    )
    db.add(app)
    await db.flush()
    
    # Créer le service account automatiquement
    client_id = ServiceAccount.generate_client_id()
    client_secret = ServiceAccount.generate_client_secret()
    
    service_account = ServiceAccount(
        name=f"SA - {app.name}",
        description=f"Service account auto-créé pour {app.name}",
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
    
    await db.commit()
    await db.refresh(app)
    
    # Sauvegarder les credentials dans le fichier (backup)
    save_service_account_credentials(
        name=app_data.slug,
        client_id=client_id,
        client_secret=client_secret,
        app_slugs=[app_data.slug]
    )
    
    logger.info(f"✅ Nouvelle application '{app_data.slug}' auto-enregistrée depuis {client_ip}")
    
    return AppRegisterResponse(
        message=f"Application '{app_data.slug}' créée avec succès. Credentials M2M générés.",
        application=AppResponse.model_validate(app),
        credentials={
            "client_id": client_id,
            "client_secret": client_secret,
            "hub_host": host,
            "hub_api_port": settings.hub_api_port if hasattr(settings, 'hub_api_port') else 8000
        },
        is_new=True
    )


@router.get("/me/credentials", response_model=AppCredentialsResponse)
async def get_app_credentials(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Récupère les credentials M2M d'une application.
    
    ⚠️ Accessible UNIQUEMENT depuis les réseaux Docker (IP whitelist).
    ⚠️ Le client_secret n'est PAS retourné (sécurité).
    
    L'application doit fournir son client_id dans le header X-Client-ID
    pour s'identifier.
    """
    client_ip = request.client.host if request.client else None
    
    if not is_docker_network_ip(client_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Accès autorisé uniquement depuis le réseau Docker (IP: {client_ip})"
        )
    
    client_id = request.headers.get("X-Client-ID")
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header X-Client-ID requis"
        )
    
    # Trouver le service account
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.client_id == client_id)
    )
    sa = result.scalar_one_or_none()
    
    if not sa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service account non trouvé"
        )
    
    if not sa.applications:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune application associée à ce service account"
        )
    
    app = sa.applications[0]
    host = settings.get_hub_host()
    
    return AppCredentialsResponse(
        app_slug=app.slug,
        app_name=app.name,
        client_id=sa.client_id,
        client_secret=None,  # Jamais retourné ici pour la sécurité
        hub_host=host,
        hub_api_port=8000,
        message="Credentials récupérés. Le client_secret n'est pas retourné pour des raisons de sécurité. Utilisez /me/regenerate-secret si vous avez perdu votre secret."
    )


@router.post("/me/regenerate-secret", response_model=AppCredentialsResponse)
async def regenerate_app_secret(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Régénère le client_secret d'une application.
    
    ⚠️ Accessible UNIQUEMENT depuis les réseaux Docker (IP whitelist).
    ⚠️ L'ancien secret sera IMMÉDIATEMENT invalidé !
    
    L'application doit fournir son client_id dans le header X-Client-ID.
    Le nouveau secret est retourné UNE SEULE FOIS dans cette réponse.
    """
    client_ip = request.client.host if request.client else None
    
    if not is_docker_network_ip(client_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Accès autorisé uniquement depuis le réseau Docker (IP: {client_ip})"
        )
    
    client_id = request.headers.get("X-Client-ID")
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header X-Client-ID requis"
        )
    
    # Trouver le service account
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.client_id == client_id)
    )
    sa = result.scalar_one_or_none()
    
    if not sa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service account non trouvé"
        )
    
    if not sa.applications:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune application associée à ce service account"
        )
    
    app = sa.applications[0]
    
    # Générer un nouveau secret
    new_secret = ServiceAccount.generate_client_secret()
    sa.client_secret_hash = get_password_hash(new_secret)
    
    await db.commit()
    
    # Mettre à jour le fichier de backup
    save_service_account_credentials(
        name=app.slug,
        client_id=sa.client_id,
        client_secret=new_secret,
        app_slugs=[app.slug]
    )
    
    host = settings.get_hub_host()
    logger.info(f"🔄 Secret régénéré pour '{app.slug}' depuis {client_ip}")
    
    return AppCredentialsResponse(
        app_slug=app.slug,
        app_name=app.name,
        client_id=sa.client_id,
        client_secret=new_secret,  # Retourné UNE SEULE FOIS !
        hub_host=host,
        hub_api_port=8000,
        message="⚠️ NOUVEAU SECRET GÉNÉRÉ ! L'ancien secret est maintenant invalide. Stockez ce secret en mémoire, il ne sera plus affiché."
    )


@router.get("/{app_id}", response_model=AppWithUsers)
async def get_app(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Récupère une application par ID (admin uniquement)"""
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.user_roles).selectinload(UserAppRole.user))
        .where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    return AppWithUsers.from_orm_model(app)


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
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Supprime une application (admin uniquement)"""
    # Charger l'app avec ses relations pour pouvoir les nettoyer
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.service_accounts))
        .where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    app_name = app.name
    app_slug = app.slug
    
    # Supprimer les associations avec les service accounts (table intermédiaire)
    # Les cascades SQL devraient le faire, mais on s'assure explicitement
    app.service_accounts.clear()
    
    # Logger l'action avant la suppression
    await log_action(
        db=db,
        user_id=current_user.id,
        action="delete_app",
        target=app_slug,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
        details=f"Suppression de l'application {app_name} (slug: {app_slug})"
    )
    
    await db.delete(app)
    await db.commit()
    
    logger.info(f"Application supprimée: {app_name} (slug: {app_slug}) par {current_user.username}")


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
    show_all: bool = False,
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
        show_all: Afficher tous les conteneurs, même ceux déjà enregistrés (défaut: False)
    """
    try:
        from app.docker_scanner import docker_scanner
    except ImportError as e:
        logger.warning(f"Module docker_scanner non disponible: {e}")
        return DockerScanResponse(
            available=False,
            error="Module Docker Scanner non disponible"
        )
    
    # Récupérer les slugs et URLs des applications déjà enregistrées
    result = await db.execute(select(Application.slug, Application.api_url, Application.frontend_url))
    existing_slugs = set()
    existing_ports = set()
    for row in result.fetchall():
        existing_slugs.add(row[0])
        # Extraire les ports depuis api_url et frontend_url (format: http://host:port)
        from urllib.parse import urlparse
        for url in [row[1], row[2]]:  # api_url et frontend_url
            if url:
                try:
                    parsed = urlparse(url)
                    if parsed.port:
                        existing_ports.add(parsed.port)
                except Exception:
                    pass
    
    # Scanner Docker
    try:
        docker_info = docker_scanner.get_docker_info()
        
        if not docker_info.get("available"):
            return DockerScanResponse(
                available=False,
                error=docker_info.get("error", "Docker non accessible")
            )
        
        containers = docker_scanner.scan_containers(include_stopped=include_stopped)

        # Filtrer les conteneurs (sauf si show_all=True)
        filtered_containers = []
        for container in containers:
            # Vérifier par slug (label ou suggéré)
            container_slug = container.labels.get("hub.app_slug") or container.suggested_slug
            is_registered = container_slug in existing_slugs
            
            # Vérifier par ports - si un des ports hôte du conteneur est déjà utilisé par une app
            container_ports = {p.host_port for p in container.ports if p.host_port}
            has_registered_port = bool(container_ports & existing_ports)
            
            # Si show_all=False, exclure les conteneurs déjà enregistrés
            if not show_all and (is_registered or has_registered_port):
                logger.debug("Conteneur filtré (déjà enregistré): %s", container.name)
                continue
            
            # Marquer le conteneur comme déjà enregistré (pour l'affichage)
            container.is_registered = is_registered or has_registered_port
            filtered_containers.append(container)

        containers = filtered_containers
        
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
                labels=container.labels,
                is_registered=getattr(container, 'is_registered', False)
            ))
        
        logger.info(f"Scan Docker: {len(discovered)} conteneurs découverts par {current_user.username} (show_all={show_all})")
        
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
