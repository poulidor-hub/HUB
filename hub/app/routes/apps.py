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
    AppResponseWithMyRole,
    DockerScanResponse, DockerContainerInfo, DockerPortInfo,
    AppRegisterRequest, AppRegisterResponse, AppCredentialsResponse,
    AppDiscoveryItem, DiscoverResponse
)
from app.auth import get_current_admin, get_current_user, get_password_hash, get_settings, verify_token_not_revoked, oauth2_scheme
from app.routes.service_accounts import save_service_account_credentials, delete_service_account_credentials
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


async def delete_app_cascade(
    db: AsyncSession,
    app: Application,
    delete_credentials_file: bool = True
) -> dict:
    """
    Supprime une application avec tous ses Service Accounts M2M associés.
    
    Cette fonction supprime :
    1. Les Service Accounts dont cette app est le "propriétaire" (SA créé lors de l'auto-registration)
    2. Les fichiers .env de credentials associés
    3. L'application elle-même (avec cascade sur user_roles, app_roles via ORM)
    
    Args:
        db: Session de base de données
        app: Application à supprimer (doit être chargée avec selectinload(service_accounts))
        delete_credentials_file: Si True, supprime aussi les fichiers sa_*.env
    
    Returns:
        dict avec les détails de la suppression (pour audit)
    """
    app_slug = app.slug
    app_name = app.name
    deleted_service_accounts = []
    
    # Identifier les Service Accounts "propriétaires" de cette app
    # Convention : le SA créé lors de l'auto-registration a un nom qui contient le slug de l'app
    for sa in list(app.service_accounts):
        # Vérifier si ce SA est le "principal" de cette app (créé lors de l'auto-registration)
        # Le nom du SA suit le pattern "SA - <App Name>" ou contient le slug
        sa_name_lower = sa.name.lower()
        is_owner_sa = (
            app_slug in sa_name_lower or 
            app_name.lower() in sa_name_lower or
            sa.name == f"SA - {app_name}"
        )
        
        if is_owner_sa:
            logger.info(f"🗑️ Suppression du Service Account '{sa.name}' (id={sa.id}) de l'app '{app_slug}'")
            
            # Supprimer le fichier credentials si demandé
            if delete_credentials_file:
                delete_service_account_credentials(app_slug)
            
            # Supprimer le SA (cascade SQL supprimera les entrées dans la table d'association)
            await db.delete(sa)
            deleted_service_accounts.append({
                "id": sa.id,
                "name": sa.name,
                "client_id": sa.client_id
            })
        else:
            # Ce SA a accès à cette app mais n'est pas son "propriétaire"
            # On retire juste l'association
            logger.debug(f"Retrait de l'accès de SA '{sa.name}' à l'app '{app_slug}' (SA non supprimé)")
    
    # Vider les associations restantes (pour les SA non supprimés)
    app.service_accounts.clear()
    
    # Supprimer l'application (cascade ORM sur user_roles et available_roles)
    await db.delete(app)
    
    logger.info(f"✅ Application '{app_name}' (slug: {app_slug}) supprimée avec {len(deleted_service_accounts)} SA(s)")
    
    return {
        "app_slug": app_slug,
        "app_name": app_name,
        "deleted_service_accounts": deleted_service_accounts
    }


# ===== Découverte M2M =====
@router.get("/discover", response_model=DiscoverResponse)
async def discover_shared_services(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
):
    """
    🆕 Découverte des APIs partagées pour la communication M2M.
    
    Retourne la liste des applications marquées comme services partagés
    (`is_shared_service=True`), accessibles par tous les Service Accounts.
    
    **Authentification requise** : Token M2M (Service Account) uniquement.
    
    **Usage** : Permet aux applications de découvrir dynamiquement les APIs
    de données disponibles (ex: API Salariés, API Référentiels) sans
    configuration manuelle.
    
    **Exemple de réponse** :
    ```json
    {
        "shared_services": [
            {
                "slug": "salarie",
                "name": "API Salariés",
                "description": "Données RH des employés",
                "api_url": "http://192.0.2.11:18087",
                "is_shared_service": true
            }
        ],
        "total_shared": 1
    }
    ```
    """
    # Vérifier le token et qu'il s'agit d'un service account
    payload = await verify_token_not_revoked(token, db)
    
    if payload.get("type") != "service_account":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cet endpoint est réservé aux Service Accounts (authentification M2M)"
        )
    
    # Récupérer toutes les apps partagées actives
    result = await db.execute(
        select(Application)
        .where(
            Application.is_shared_service == True,
            Application.is_active == True
        )
        .order_by(Application.name)
    )
    shared_apps = result.scalars().all()
    
    # Construire la réponse
    shared_services = [
        AppDiscoveryItem(
            slug=app.slug,
            name=app.name,
            description=app.description,
            api_url=app.api_url,
            is_shared_service=True
        )
        for app in shared_apps
    ]
    
    logger.info(f"🔍 Découverte M2M par SA '{payload.get('sub')}': {len(shared_services)} services partagés")
    
    return DiscoverResponse(
        shared_services=shared_services,
        total_shared=len(shared_services)
    )


@router.get("/", response_model=List[AppResponseWithMyRole])
async def list_apps(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Liste les applications (admin voit tout, user voit les siennes)
    
    Retourne également le rôle de l'utilisateur courant sur chaque app (my_role)
    """
    # Charger les rôles de l'utilisateur courant
    user_result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
        .where(User.id == current_user.id)
    )
    user = user_result.scalar_one()
    user_roles_map = {ar.application.slug: ar.role for ar in user.app_roles}
    
    if current_user.is_admin:
        result = await db.execute(select(Application))
        apps = result.scalars().all()
        return [
            AppResponseWithMyRole.from_app_with_role(app, user_roles_map.get(app.slug))
            for app in apps
        ]
    else:
        # Retourne uniquement les apps de l'utilisateur via app_roles
        return [
            AppResponseWithMyRole.from_app_with_role(ar.application, ar.role)
            for ar in user.app_roles
        ]


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


# Rôles disponibles pour les apps
AVAILABLE_ROLES = ["admin", "manager", "user", "viewer"]


@router.put("/me/role/{app_slug}")
async def update_my_role(
    app_slug: str,
    role: Optional[str] = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Permet à un admin de modifier son propre rôle sur une application.
    
    - Si role est fourni: définit/modifie le rôle
    - Si role est null ou vide: supprime le rôle (l'admin garde accès via is_admin)
    
    ⚠️ Admin uniquement - Les admins ont toujours accès à toutes les apps,
    ce rôle est pour définir leur rôle "officiel" dans l'application.
    """
    # Vérifier que l'app existe
    result = await db.execute(select(Application).where(Application.slug == app_slug))
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    # Valider le rôle si fourni
    if role and role not in AVAILABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Rôle invalide. Rôles disponibles: {', '.join(AVAILABLE_ROLES)}"
        )
    
    # Chercher le rôle existant
    role_result = await db.execute(
        select(UserAppRole).where(
            UserAppRole.user_id == current_user.id,
            UserAppRole.app_id == app.id
        )
    )
    existing_role = role_result.scalar_one_or_none()
    
    if role:
        # Définir/modifier le rôle
        if existing_role:
            existing_role.role = role
        else:
            new_role = UserAppRole(
                user_id=current_user.id,
                app_id=app.id,
                role=role
            )
            db.add(new_role)
        
        await log_action(
            db=db,
            user_id=current_user.id,
            action="update_my_role",
            target=app_slug,
            ip_address=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent", "") if request else "",
            details=f"Rôle défini à '{role}' sur {app.name}"
        )
        
        await db.commit()
        
        return {
            "message": f"Rôle '{role}' défini sur {app.name}",
            "app_slug": app_slug,
            "app_name": app.name,
            "my_role": role
        }
    else:
        # Supprimer le rôle
        if existing_role:
            await db.delete(existing_role)
            
            await log_action(
                db=db,
                user_id=current_user.id,
                action="remove_my_role",
                target=app_slug,
                ip_address=request.client.host if request and request.client else None,
                user_agent=request.headers.get("user-agent", "") if request else "",
                details=f"Rôle supprimé sur {app.name}"
            )
            
            await db.commit()
            
            return {
                "message": f"Rôle supprimé sur {app.name}",
                "app_slug": app_slug,
                "app_name": app.name,
                "my_role": None
            }
        else:
            return {
                "message": f"Aucun rôle à supprimer sur {app.name}",
                "app_slug": app_slug,
                "app_name": app.name,
                "my_role": None
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
        
        # Ajouter automatiquement les applications partagées (is_shared_service=True)
        shared_result = await db.execute(
            select(Application).where(Application.is_shared_service == True)
        )
        shared_apps = shared_result.scalars().all()
        shared_slugs = []
        for shared_app in shared_apps:
            if shared_app not in service_account.applications:
                service_account.applications.append(shared_app)
                shared_slugs.append(shared_app.slug)
        
        # Sauvegarder les credentials automatiquement
        all_app_slugs = [app_data.slug] + shared_slugs
        secrets_file = save_service_account_credentials(
            name=app_data.slug,
            client_id=client_id,
            client_secret=client_secret,
            app_slugs=all_app_slugs
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
    - Remplacement automatique si l'app existe déjà (selon FORCE_REPLACE_APP)
    
    Flux :
    1. L'app démarre et appelle cet endpoint avec son slug
    2. Si l'app existe et FORCE_REPLACE_APP=true : suppression + recréation
    3. Le Hub crée l'app + service account
    4. L'app reçoit ses credentials et les stocke en mémoire (pas dans .env)
    
    Le remplacement automatique permet aux applications de se resynchroniser
    après un redéploiement sans intervention manuelle de l'admin.
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
    
    replaced = False
    old_app_info = None
    
    if existing_app:
        # Application existante - vérifier si on doit la remplacer
        if settings.force_replace_app:
            logger.info(f"🔄 Remplacement automatique de l'application '{app_data.slug}' (FORCE_REPLACE_APP=true)")
            
            # Sauvegarder les infos pour l'audit
            old_app_info = await delete_app_cascade(db, existing_app, delete_credentials_file=True)
            await db.flush()  # Flush pour libérer le slug
            replaced = True
            
            logger.info(f"✅ Ancienne application '{app_data.slug}' supprimée, recréation en cours...")
        else:
            # Mode sans remplacement - retourner les infos sans le secret
            logger.info(f"✅ Application '{app_data.slug}' déjà enregistrée (FORCE_REPLACE_APP=false)")
            
            # Trouver le service account associé
            sa = next((sa for sa in existing_app.service_accounts), None)
            
            return AppRegisterResponse(
                message=f"Application '{app_data.slug}' déjà enregistrée. Utilisez /api/apps/me/credentials pour récupérer vos credentials.",
                application=AppResponse.model_validate(existing_app),
                credentials={"client_id": sa.client_id} if sa else None,
                is_new=False
            )
    
    # Nouvelle application (ou remplacement) - créer l'app et le service account
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
    
    # Ajouter automatiquement les applications partagées (is_shared_service=True)
    shared_result = await db.execute(
        select(Application).where(Application.is_shared_service == True)
    )
    shared_apps = shared_result.scalars().all()
    shared_slugs = []
    for shared_app in shared_apps:
        if shared_app not in service_account.applications:
            service_account.applications.append(shared_app)
            shared_slugs.append(shared_app.slug)
    
    # Logger l'audit si c'est un remplacement (action système sans user_id)
    if replaced:
        deleted_sa_names = [sa["name"] for sa in old_app_info.get("deleted_service_accounts", [])]
        await log_action(
            db=db,
            user_id=None,  # Action système (auto-registration)
            action="replace_app",
            target=app_data.slug,
            ip_address=client_ip,
            user_agent=None,
            details=f"Remplacement automatique de l'application '{app_data.slug}' via auto-registration. Anciens SA supprimés: {deleted_sa_names}"
        )
    
    await db.commit()
    await db.refresh(app)
    
    # Sauvegarder les credentials dans le fichier (backup)
    all_app_slugs = [app_data.slug] + shared_slugs
    save_service_account_credentials(
        name=app_data.slug,
        client_id=client_id,
        client_secret=client_secret,
        app_slugs=all_app_slugs
    )
    
    # Message différent selon création ou remplacement
    if replaced:
        message = f"Application '{app_data.slug}' remplacée avec succès. Nouveaux credentials M2M générés."
        logger.info(f"🔄 Application '{app_data.slug}' remplacée depuis {client_ip} (ancien SA supprimé: {old_app_info})")
    else:
        message = f"Application '{app_data.slug}' créée avec succès. Credentials M2M générés."
        logger.info(f"✅ Nouvelle application '{app_data.slug}' auto-enregistrée depuis {client_ip}")
    
    return AppRegisterResponse(
        message=message,
        application=AppResponse.model_validate(app),
        credentials={
            "client_id": client_id,
            "client_secret": client_secret,
            "hub_host": host,
            "hub_api_port": settings.hub_api_port if hasattr(settings, 'hub_api_port') else 8000
        },
        is_new=True  # True aussi pour le remplacement car nouveaux credentials
    )


@router.post("/auto-register", response_model=AppRegisterResponse)
async def admin_auto_register_app(
    app_data: AppRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Auto-enregistrement d'une application déclenché par un admin.
    
    Permet à un administrateur de pré-créer une application avec ses
    credentials M2M, comme si l'application s'était auto-enregistrée.
    
    L'admin fournit juste le slug et les ports, le Hub génère le reste.
    Les credentials sont sauvegardés dans `secrets/service_accounts/`.
    """
    logger.info(f"📝 Auto-enregistrement admin pour '{app_data.slug}'")
    
    # Vérifier si l'application existe déjà
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.service_accounts))
        .where(Application.slug == app_data.slug)
    )
    existing_app = result.scalar_one_or_none()
    
    if existing_app:
        # Application existante - retourner les infos sans le secret
        logger.info(f"⚠️ Application '{app_data.slug}' déjà enregistrée")
        
        # Trouver le service account associé
        sa = next((sa for sa in existing_app.service_accounts), None)
        
        return AppRegisterResponse(
            message=f"Application '{app_data.slug}' existe déjà.",
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
        description=app_data.description or f"Application {app_data.slug} (pré-enregistrée)",
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
    
    # Ajouter automatiquement les applications partagées (is_shared_service=True)
    shared_result = await db.execute(
        select(Application).where(Application.is_shared_service == True)
    )
    shared_apps = shared_result.scalars().all()
    shared_slugs = []
    for shared_app in shared_apps:
        if shared_app not in service_account.applications:
            service_account.applications.append(shared_app)
            shared_slugs.append(shared_app.slug)
    
    await db.commit()
    await db.refresh(app)
    
    # Sauvegarder les credentials dans le fichier
    all_app_slugs = [app_data.slug] + shared_slugs
    secrets_file = save_service_account_credentials(
        name=app_data.slug,
        client_id=client_id,
        client_secret=client_secret,
        app_slugs=all_app_slugs
    )
    
    logger.info(f"✅ Application '{app_data.slug}' pré-enregistrée par admin, credentials sauvés dans {secrets_file}")
    
    return AppRegisterResponse(
        message=f"Application '{app_data.slug}' créée avec succès. Credentials M2M générés et sauvegardés.",
        application=AppResponse.model_validate(app),
        credentials={
            "client_id": client_id,
            "client_secret": client_secret,
            "hub_host": host,
            "hub_api_port": settings.hub_api_port if hasattr(settings, 'hub_api_port') else 8000,
            "secrets_file": secrets_file
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
    
    L'application doit fournir soit :
    - `X-Client-ID` : son client_id (ex: sa_xxxx...)
    - `X-Slug` : son slug d'application (ex: mon-app)
    """
    client_ip = request.client.host if request.client else None
    
    if not is_docker_network_ip(client_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Accès autorisé uniquement depuis le réseau Docker (IP: {client_ip})"
        )
    
    client_id = request.headers.get("X-Client-ID")
    app_slug = request.headers.get("X-Slug")
    
    if not client_id and not app_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header X-Client-ID ou X-Slug requis"
        )
    
    # Trouver le service account (par client_id ou par slug)
    if client_id:
        result = await db.execute(
            select(ServiceAccount)
            .options(selectinload(ServiceAccount.applications))
            .where(ServiceAccount.client_id == client_id)
        )
        sa = result.scalar_one_or_none()
    else:
        # Recherche par slug de l'application via la table d'association
        from app.models import service_account_app_access
        
        result = await db.execute(
            select(ServiceAccount)
            .options(selectinload(ServiceAccount.applications))
            .join(service_account_app_access, ServiceAccount.id == service_account_app_access.c.service_account_id)
            .join(Application, Application.id == service_account_app_access.c.app_id)
            .where(Application.slug == app_slug)
        )
        # Utiliser scalars().first() car la jointure peut créer des doublons
        sa = result.scalars().first()
        
        if not sa:
            # Vérifier si l'app existe mais n'a pas de service account
            app_result = await db.execute(
                select(Application).where(Application.slug == app_slug)
            )
            if not app_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Application '{app_slug}' non trouvée"
                )
    
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
    
    L'application doit fournir soit :
    - `X-Client-ID` : son client_id (ex: sa_xxxx...)
    - `X-Slug` : son slug d'application (ex: mon-app)
    
    Le nouveau secret est retourné UNE SEULE FOIS dans cette réponse.
    """
    client_ip = request.client.host if request.client else None
    
    if not is_docker_network_ip(client_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Accès autorisé uniquement depuis le réseau Docker (IP: {client_ip})"
        )
    
    client_id = request.headers.get("X-Client-ID")
    app_slug = request.headers.get("X-Slug")
    
    if not client_id and not app_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header X-Client-ID ou X-Slug requis"
        )
    
    # Trouver le service account (par client_id ou par slug)
    if client_id:
        result = await db.execute(
            select(ServiceAccount)
            .options(selectinload(ServiceAccount.applications))
            .where(ServiceAccount.client_id == client_id)
        )
        sa = result.scalar_one_or_none()
    else:
        # Recherche par slug de l'application via la table d'association
        from app.models import service_account_app_access
        
        result = await db.execute(
            select(ServiceAccount)
            .options(selectinload(ServiceAccount.applications))
            .join(service_account_app_access, ServiceAccount.id == service_account_app_access.c.service_account_id)
            .join(Application, Application.id == service_account_app_access.c.app_id)
            .where(Application.slug == app_slug)
        )
        # Utiliser scalars().first() car la jointure peut créer des doublons
        sa = result.scalars().first()
        
        if not sa:
            # Vérifier si l'app existe mais n'a pas de service account
            app_result = await db.execute(
                select(Application).where(Application.slug == app_slug)
            )
            if not app_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Application '{app_slug}' non trouvée"
                )
    
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
    """
    Supprime une application avec ses Service Accounts M2M associés (admin uniquement).
    
    Cette suppression inclut :
    - L'application elle-même
    - Le Service Account M2M créé lors de l'auto-registration
    - Le fichier de credentials (.env) associé
    - Les rôles utilisateurs et rôles applicatifs (cascade ORM)
    
    Les Service Accounts qui ont simplement accès à cette app (mais ne lui appartiennent pas)
    conservent leurs accès aux autres applications.
    """
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
    
    # Supprimer l'application avec cascade (SA + fichiers credentials)
    deletion_info = await delete_app_cascade(db, app, delete_credentials_file=True)
    
    # Logger l'action avant le commit
    deleted_sa_names = [sa["name"] for sa in deletion_info.get("deleted_service_accounts", [])]
    await log_action(
        db=db,
        user_id=current_user.id,
        action="delete_app",
        target=app_slug,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
        details=f"Suppression de l'application {app_name} (slug: {app_slug}) avec {len(deleted_sa_names)} SA(s): {deleted_sa_names}"
    )
    
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
        .options(selectinload(Application.user_roles))
        .where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    # Supprimer les anciens rôles
    for ur in app.user_roles:
        await db.delete(ur)
    
    # Récupérer les users et créer les nouveaux rôles
    if user_ids:
        users_result = await db.execute(
            select(User).where(User.id.in_(user_ids))
        )
        users = users_result.scalars().all()
        
        for user in users:
            new_role = UserAppRole(
                user_id=user.id,
                app_id=app.id,
                role="user"  # Rôle par défaut
            )
            db.add(new_role)
    else:
        users = []
    
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


@router.post("/docker/reconnect")
async def reconnect_docker(
    current_user: User = Depends(get_current_admin)
):
    """
    Réinitialise la connexion Docker.
    
    Utile si la connexion a échoué au démarrage à cause de permissions
    ou si le socket Docker a été redémarré.
    
    ⚠️ Admin uniquement.
    """
    try:
        from app.docker_scanner import docker_scanner
        
        # Réinitialiser
        docker_scanner.reset()
        
        # Tenter une nouvelle connexion
        if docker_scanner.is_available:
            docker_info = docker_scanner.get_docker_info()
            logger.info(f"✅ Docker reconnecté par {current_user.username}")
            return {
                "success": True,
                "message": "Docker reconnecté avec succès",
                "docker_version": docker_info.get("version"),
                "containers_running": docker_info.get("containers_running", 0)
            }
        else:
            return {
                "success": False,
                "message": "Impossible de se connecter à Docker",
                "error": docker_scanner.last_error
            }
    except Exception as e:
        logger.error(f"Erreur reconnexion Docker: {e}")
        return {
            "success": False,
            "message": "Erreur lors de la reconnexion",
            "error": str(e)
        }


@router.put("/{app_id}/shared")
async def set_app_shared(
    app_id: int,
    is_shared: bool,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Marque une application comme service partagé (admin uniquement).
    
    Si is_shared=True, cette application sera automatiquement accessible
    par tous les nouveaux service accounts créés.
    
    ⚠️ Les service accounts existants ne sont pas modifiés.
    """
    result = await db.execute(
        select(Application).where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    app.is_shared_service = is_shared
    db.add(app)
    await db.commit()
    
    logger.info(f"🔧 Application '{app.slug}' is_shared_service={is_shared} par {current_user.username}")
    
    return {
        "message": f"Application '{app.slug}' mise à jour",
        "app_id": app.id,
        "slug": app.slug,
        "is_shared_service": is_shared
    }


# ===== Docker Sync =====

@router.post("/docker/sync")
async def sync_docker_applications(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Synchronise l'état des applications avec les conteneurs Docker.
    
    - Désactive (is_active=False) les applications dont le conteneur est arrêté
    - Réactive (is_active=True) les applications dont le conteneur est en cours d'exécution
    
    ⚠️ Admin uniquement - Nécessite l'accès au socket Docker.
    
    Returns:
        Rapport de synchronisation avec les apps activées/désactivées
    """
    try:
        from app.docker_scanner import docker_scanner
    except ImportError as e:
        logger.warning(f"Module docker_scanner non disponible: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Module Docker Scanner non disponible"
        )
    
    if not docker_scanner.is_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Docker non accessible: {docker_scanner.last_error}"
        )
    
    # Récupérer toutes les applications en base
    result = await db.execute(
        select(Application)
    )
    apps = result.scalars().all()
    
    if not apps:
        return {
            "message": "Aucune application à synchroniser",
            "activated": [],
            "deactivated": [],
            "unchanged": []
        }
    
    # Scanner tous les conteneurs Docker (y compris arrêtés)
    containers = docker_scanner.scan_containers(include_stopped=True)
    
    # Extraire les ports hôte des conteneurs en cours d'exécution
    running_ports = set()
    stopped_ports = set()
    
    for container in containers:
        for port in container.ports:
            if port.host_port:
                if container.status == "running":
                    running_ports.add(port.host_port)
                else:
                    stopped_ports.add(port.host_port)
    
    activated = []
    deactivated = []
    unchanged = []
    
    for app in apps:
        # Extraire le port depuis l'api_url de l'application
        from urllib.parse import urlparse
        app_port = None
        if app.api_url:
            try:
                parsed = urlparse(app.api_url)
                app_port = parsed.port
            except Exception:
                pass
        
        if not app_port:
            # Pas de port identifiable, on ne touche pas à l'app
            unchanged.append({
                "slug": app.slug,
                "reason": "Port non identifiable"
            })
            continue
        
        # Vérifier si le port est dans les conteneurs en cours d'exécution
        if app_port in running_ports:
            if not app.is_active:
                # Réactiver l'application
                app.is_active = True
                activated.append(app.slug)
                logger.info(f"✅ Application '{app.slug}' réactivée (conteneur en cours d'exécution sur port {app_port})")
            else:
                unchanged.append({
                    "slug": app.slug,
                    "reason": "Déjà active"
                })
        elif app_port in stopped_ports:
            if app.is_active:
                # Désactiver l'application
                app.is_active = False
                deactivated.append(app.slug)
                logger.info(f"⏸️ Application '{app.slug}' désactivée (conteneur arrêté sur port {app_port})")
            else:
                unchanged.append({
                    "slug": app.slug,
                    "reason": "Déjà inactive"
                })
        else:
            # Port non trouvé dans les conteneurs Docker
            # On considère que l'app n'est plus disponible
            if app.is_active:
                app.is_active = False
                deactivated.append(app.slug)
                logger.info(f"⏸️ Application '{app.slug}' désactivée (aucun conteneur sur port {app_port})")
            else:
                unchanged.append({
                    "slug": app.slug,
                    "reason": "Conteneur introuvable, déjà inactive"
                })
    
    await db.commit()
    
    # Log si des changements
    if activated or deactivated:
        await log_action(
            db=db,
            user_id=current_user.id if current_user else None,
            action="docker_sync",
            target="applications",
            ip_address=None,
            user_agent=None,
            details=f"Sync Docker: {len(activated)} activées, {len(deactivated)} désactivées"
        )
    
    return {
        "message": f"Synchronisation terminée",
        "activated": activated,
        "deactivated": deactivated,
        "unchanged_count": len(unchanged),
        "docker_info": docker_scanner.get_docker_info()
    }


async def auto_sync_docker_applications(db: AsyncSession) -> dict:
    """
    Version interne de la synchronisation Docker (pour la tâche périodique).
    
    Ne nécessite pas d'authentification, utilisé uniquement en interne.
    """
    try:
        from app.docker_scanner import docker_scanner
    except ImportError:
        return {"error": "Module Docker Scanner non disponible"}
    
    if not docker_scanner.is_available:
        return {"error": f"Docker non accessible: {docker_scanner.last_error}"}
    
    # Récupérer toutes les applications en base
    result = await db.execute(select(Application))
    apps = result.scalars().all()
    
    if not apps:
        return {"message": "Aucune application", "changes": 0}
    
    # Scanner tous les conteneurs Docker
    containers = docker_scanner.scan_containers(include_stopped=True)
    
    # Extraire les ports hôte des conteneurs en cours d'exécution
    running_ports = set()
    for container in containers:
        if container.status == "running":
            for port in container.ports:
                if port.host_port:
                    running_ports.add(port.host_port)
    
    changes = 0
    
    for app in apps:
        from urllib.parse import urlparse
        app_port = None
        if app.api_url:
            try:
                parsed = urlparse(app.api_url)
                app_port = parsed.port
            except Exception:
                pass
        
        if not app_port:
            continue
        
        should_be_active = app_port in running_ports
        
        if should_be_active and not app.is_active:
            app.is_active = True
            changes += 1
            logger.info(f"✅ [Auto-Sync] Application '{app.slug}' réactivée")
        elif not should_be_active and app.is_active:
            app.is_active = False
            changes += 1
            logger.info(f"⏸️ [Auto-Sync] Application '{app.slug}' désactivée")
    
    if changes > 0:
        await db.commit()
    
    return {"message": "Sync auto terminée", "changes": changes}
