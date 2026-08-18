from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional, Union, Dict
from datetime import datetime, timezone
import re
import asyncio
import ipaddress
import json
import io

from pathlib import Path
import logging

from app.routes.events import notify_event, notify_stats_update
from app.routes.app_backups import reassociate_orphan_backups

from app.database import get_db
from app.models import Application, User, ServiceAccount, UserAppRole, ApprovalStatus, service_account_app_access, DEFAULT_ROLES, UserFavorite, utcnow, AppDependency, AppGroup, AppDeployment
from app.routes.app_groups import group_to_brief
from app.schemas import (
    AppCreate, AppCreateSimplified, AppUpdate, AppResponse, AppWithUsers, AppWithUsersAndDocker,
    AppResponseWithMyRole,
    DockerScanResponse, DockerContainerInfo, DockerPortInfo, DockerInfoResponse,
    DockerAppGroup,
    AppRegisterRequest, AppRegisterResponse, AppCredentialsResponse,
    AppDiscoveryItem, DiscoverResponse,
    AppApprovalRequest, AppApprovalResponse, PendingAppsResponse,
    BatchImportRequest, BatchImportResponse, BatchAppResult,
    AppOpenApiPush, AppOpenApiResponse,
    AllocatePortsRequest, AllocatePortsResponse,
    PortsStatusResponse, PortAllocationItem,
    DependencyEntry, DependenciesPush, DependenciesPushResponse,
    DependencyOut, DependenciesGraph,
    SyncReport,
)
from app.auth import get_current_admin, get_current_user, get_password_hash, get_settings, verify_token_not_revoked, oauth2_scheme, optional_oauth2_scheme, get_current_entity
from app.routes.service_accounts import save_service_account_credentials, delete_service_account_credentials
from app.routes.audit import log_action
from app.docker_scanner import docker_scanner
from app.port_allocator import allocate_ports_for_app, docker_occupied_ports, release_ports_for_app, PortPoolExhausted, SubnetPoolExhausted
from app.m2m_catalog_export import build_m2m_catalog_export

router = APIRouter(prefix="/api/apps", tags=["Applications"])
settings = get_settings()
logger = logging.getLogger(__name__)


async def _service_account_from_m2m_token(token: str, db: AsyncSession) -> ServiceAccount:
    """Résout un Bearer M2M valide en ServiceAccount chargé avec ses apps."""
    try:
        payload = await verify_token_not_revoked(token, db)
    except Exception as exc:
        logger.warning("Token M2M invalide pour auto-registration: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token M2M invalide ou expiré")
    if payload.get("type") != "service_account":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token M2M requis")
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.client_id == payload.get("sub"))
    )
    service_account = result.scalar_one_or_none()
    if service_account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service account non trouvé")
    return service_account


def normalize_frontend_url(url: str) -> str:
    """
    Normalise une URL frontend pour toujours utiliser HTTP.
    
    Les applications gèrent elles-mêmes la redirection HTTP→HTTPS,
    le Hub ne doit donc utiliser que le port HTTP pour les redirections utilisateur.
    """
    if url and url.startswith("https://"):
        return "http://" + url[len("https://"):]
    return url




# ===== Fonctions Helper pour Services Partagés M2M =====

async def add_shared_service_to_all_service_accounts(db: AsyncSession, app: Application) -> int:
    """
    Ajoute une application partagée (is_shared_service=True) aux permissions de tous les Service Accounts existants.
    
    Appelé automatiquement quand :
    - Une app est créée/enregistrée avec is_shared=True
    - Une app existante est mise à jour pour devenir is_shared=True
    
    Args:
        db: Session de base de données
        app: Application à ajouter (doit avoir is_shared_service=True)
        
    Returns:
        Nombre de Service Accounts mis à jour
    """
    if not app.is_shared_service:
        return 0
    
    # Récupérer tous les Service Accounts avec leurs applications
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
    )
    service_accounts = result.scalars().all()
    
    updated_count = 0
    for sa in service_accounts:
        # Vérifier si l'app n'est pas déjà dans les permissions du SA
        if app in sa.applications:
            continue
        # Isolation atelier : ne propager qu'aux SAs du même contexte
        sa_is_workshop = any(getattr(sa_app, 'is_workshop', False) for sa_app in sa.applications) if sa.applications else False
        if sa_is_workshop != bool(getattr(app, 'is_workshop', False)):
            continue
        sa.applications.append(app)
        updated_count += 1
        logger.info(f"🔗 Service partagé '{app.slug}' ajouté aux permissions de SA '{sa.name}'")
    
    if updated_count > 0:
        logger.info(f"✅ Service partagé '{app.slug}' ajouté à {updated_count} Service Account(s)")
    
    return updated_count


async def remove_shared_service_from_all_service_accounts(db: AsyncSession, app: Application) -> int:
    """
    Retire une application des permissions de tous les Service Accounts (sauf son SA propriétaire).
    
    Appelé automatiquement quand :
    - Une app is_shared=True passe à is_shared=False
    
    Args:
        db: Session de base de données
        app: Application à retirer
        
    Returns:
        Nombre de Service Accounts mis à jour
    """
    # Récupérer tous les Service Accounts avec leurs applications
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
    )
    service_accounts = result.scalars().all()
    
    updated_count = 0
    for sa in service_accounts:
        # Ne pas retirer l'app de son SA propriétaire (celui qui a le même slug dans le nom)
        sa_name_lower = sa.name.lower()
        is_owner_sa = (
            app.slug in sa_name_lower or 
            app.name.lower() in sa_name_lower or
            sa.name == f"SA - {app.name}"
        )
        
        if is_owner_sa:
            # C'est le SA propriétaire de cette app, on ne retire pas
            continue
        
        # Retirer l'app des permissions du SA si elle y est
        if app in sa.applications:
            sa.applications.remove(app)
            updated_count += 1
            logger.info(f"🔓 Service partagé '{app.slug}' retiré des permissions de SA '{sa.name}'")
    
    if updated_count > 0:
        logger.info(f"✅ Service partagé '{app.slug}' retiré de {updated_count} Service Account(s)")
    
    return updated_count


def _is_owner_service_account(app: Application, sa: ServiceAccount) -> bool:
    """Détermine si un service account est le SA propriétaire d'une application."""
    sa_name_lower = sa.name.lower()
    return (
        app.slug in sa_name_lower
        or app.name.lower() in sa_name_lower
        or sa.name == f"SA - {app.name}"
    )


def _get_owner_service_account_for_app(app: Application) -> Optional[ServiceAccount]:
    """Retourne le SA propriétaire de l'application, si identifiable."""
    owner_sa = next(
        (sa for sa in app.service_accounts if _is_owner_service_account(app, sa)),
        None,
    )
    if owner_sa:
        return owner_sa
    return next(iter(app.service_accounts), None)


# ===== IP Whitelist pour auto-enregistrement =====
# Réseaux Docker autorisés par défaut pour l'auto-enregistrement (sans authentification)
# SÉCURITÉ : Restreint aux réseaux Docker internes uniquement
# Les réseaux 192.168.* et 10.0.* sont exclus par sécurité par défaut
DEFAULT_DOCKER_NETWORK_PREFIXES = [
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
]


def get_whitelist_networks() -> list[str]:
    """
    Retourne la liste des préfixes de réseaux autorisés pour l'auto-enregistrement.
    
    Utilise APP_WHITELIST_NETWORKS depuis la config si défini,
    sinon utilise les réseaux Docker par défaut (172.17-31.*).
    
    Format de APP_WHITELIST_NETWORKS : préfixes séparés par des virgules
    Exemple : "172.17.,172.18.,192.168.50."
    """
    from app.config import get_settings
    settings = get_settings()
    
    if settings.app_whitelist_networks:
        # Parser la liste depuis la config (séparée par virgules)
        networks = [n.strip() for n in settings.app_whitelist_networks.split(",") if n.strip()]
        if networks:
            return networks
    
    # Par défaut : réseaux Docker uniquement
    return DEFAULT_DOCKER_NETWORK_PREFIXES


def is_docker_network_ip(ip: str) -> bool:
    """Vérifie si l'IP provient d'un réseau autorisé (whitelist) avec validation stricte."""
    if not ip:
        return False
    whitelist = get_whitelist_networks()
    try:
        addr = ipaddress.ip_address(ip)
        for prefix in whitelist:
            # Supporter la notation CIDR (ex: 172.17.0.0/16) et les préfixes simples
            try:
                network = ipaddress.ip_network(prefix, strict=False)
                if addr in network:
                    return True
            except ValueError:
                # Fallback pour les préfixes simples (ex: "172.17.")
                if ip.startswith(prefix):
                    return True
    except ValueError:
        return False
    return False


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

    # Libérer les ports alloués (rendre au pool)
    released_ports = await release_ports_for_app(db, app_slug)

    # Supprimer l'application (cascade ORM sur user_roles et available_roles)
    await db.delete(app)

    logger.info(f"✅ Application '{app_name}' (slug: {app_slug}) supprimée avec {len(deleted_service_accounts)} SA(s)")

    return {
        "app_slug": app_slug,
        "app_name": app_name,
        "deleted_service_accounts": deleted_service_accounts,
        "released_port_allocations": released_ports,
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
    
    # Déterminer le contexte atelier du SA appelant
    caller_is_workshop = payload.get("is_workshop", False)
    
    # Récupérer toutes les apps partagées actives du même contexte atelier
    result = await db.execute(
        select(Application)
        .where(
            Application.is_shared_service == True,
            Application.is_active == True,
            Application.is_workshop == caller_is_workshop,
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


@router.get("/m2m-catalog/export")
async def export_m2m_catalog(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """
    Exporte le catalogue M2M complet du parc (admin-only).

    Agrège la surface d'API de toutes les apps partagées (is_shared_service=True)
    en un fichier JSON auto-suffisant, destiné à être filé à une IA de dev pour
    générer/injecter des fausses données en environnement dev/test.

    Le fichier ne contient aucun secret.
    """
    catalog = await build_m2m_catalog_export(db)

    # Renseigner les métadonnées temporelles
    now = datetime.now(timezone.utc)
    catalog["export_metadata"]["generated_at"] = now.isoformat()
    try:
        from app.routes.docs import get_hub_version
        catalog["export_metadata"]["hub_version"] = get_hub_version()
    except Exception:
        catalog["export_metadata"]["hub_version"] = None

    payload = json.dumps(catalog, ensure_ascii=False, indent=2, default=str)
    date_tag = now.strftime("%Y%m%d")
    return StreamingResponse(
        io.BytesIO(payload.encode("utf-8")),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="m2m-catalog-{date_tag}.json"'
        },
    )


@router.get("/status")
async def get_apps_status(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_admin)
):
    """
    🔍 Vérifie le statut de connectivité de toutes les applications (admin uniquement).
    
    Retourne pour chaque application :
    - `status`: 'online', 'offline', 'error'
    - `response_time_ms`: temps de réponse en millisecondes
    - `last_check`: timestamp de la vérification
    """
    import httpx
    import asyncio
    from datetime import datetime, timezone
    
    result = await db.execute(
        select(Application).where(Application.is_system_app == False)
    )
    apps = result.scalars().all()
    
    loop = asyncio.get_running_loop()
    
    async def check_app(app: Application, client: httpx.AsyncClient) -> dict:
        """Vérifie la connectivité d'une application"""
        status_data = {
            "id": app.id,
            "slug": app.slug,
            "name": app.name,
            "api_url": app.api_url,
            "frontend_url": app.frontend_url,
            "is_active": app.is_active,
            "status": "offline",
            "response_time_ms": None,
            "error": None,
            "last_check": utcnow().isoformat()
        }
        
        if not app.api_url:
            status_data["error"] = "Pas d'URL API configurée"
            return status_data
        
        try:
            start = loop.time()
            # Essayer /api/health d'abord, puis / en fallback
            for endpoint in ["/api/health", "/"]:
                try:
                    url = app.api_url.rstrip('/') + endpoint
                    response = await client.get(url)
                    elapsed = (loop.time() - start) * 1000
                    
                    if response.status_code < 500:
                        status_data["status"] = "online"
                        status_data["response_time_ms"] = round(elapsed, 2)
                        return status_data
                except Exception:
                    continue
            
            # Aucun endpoint n'a répondu correctement
            status_data["status"] = "offline"
            status_data["error"] = "Aucun endpoint accessible"
        except httpx.TimeoutException:
            status_data["status"] = "offline"
            status_data["error"] = "Timeout (5s)"
        except httpx.ConnectError as e:
            status_data["status"] = "offline"
            status_data["error"] = f"Connexion refusée"
        except Exception as e:
            status_data["status"] = "error"
            status_data["error"] = str(e)[:100]
        
        return status_data
    
    # Vérifier toutes les apps en parallèle avec un seul client httpx réutilisé
    # verify=False pour accepter les certificats auto-signés (PKI interne)
    async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
        status_results = await asyncio.gather(*[check_app(app, client) for app in apps])
    
    # Statistiques globales
    online_count = sum(1 for s in status_results if s["status"] == "online")
    offline_count = sum(1 for s in status_results if s["status"] == "offline")
    error_count = sum(1 for s in status_results if s["status"] == "error")
    
    return {
        "apps": status_results,
        "summary": {
            "total": len(status_results),
            "online": online_count,
            "offline": offline_count,
            "error": error_count
        },
        "timestamp": utcnow().isoformat()
    }


async def _load_last_deployments(db: AsyncSession) -> dict[str, dict]:
    """Map {slug: {status, finished_at}} du dernier déploiement success/failed par app."""
    dep_result = await db.execute(
        select(AppDeployment.app_slug, AppDeployment.status, AppDeployment.finished_at)
        .where(AppDeployment.status.in_(["success", "failed"]))
        .order_by(AppDeployment.id.desc())
    )
    last: dict[str, dict] = {}
    for dep_slug, dep_status, dep_finished in dep_result:
        if dep_slug not in last:
            last[dep_slug] = {"status": dep_status, "finished_at": dep_finished}
    return last


@router.get("/", response_model=List[AppResponseWithMyRole])
async def list_apps(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    workshop_filter: str = Query("false", description="Filtre atelier: 'false' (prod uniquement, défaut), 'true' (ateliers uniquement), 'all' (tout)"),
):
    """Liste les applications (admin voit tout, user voit les siennes)
    
    Retourne également le rôle de l'utilisateur courant sur chaque app (my_role).
    Les indicateurs Docker/health ne sont plus inclus ici pour une réponse instantanée.
    Les changements de statut arrivent en temps réel via SSE.
    
    Paramètre workshop_filter (admin uniquement, ignoré pour les users) :
    - false (défaut) : apps production uniquement
    - true : apps atelier uniquement  
    - all : toutes les apps
    """
    logger.debug(f"list_apps appelé par {current_user.username} (is_admin={current_user.is_admin})")
    
    # Charger les rôles de l'utilisateur courant
    user_result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
        .where(User.id == current_user.id)
    )
    user = user_result.scalar_one()
    user_roles_map = {ar.application.slug: ar.role for ar in user.app_roles}

    # Charger les favoris de l'utilisateur courant
    fav_result = await db.execute(
        select(UserFavorite.app_id).where(UserFavorite.user_id == current_user.id)
    )
    favorite_app_ids = {row[0] for row in fav_result}

    # Map des groupes pour hydratation (une seule requête, pas de N+1)
    groups_result = await db.execute(select(AppGroup))
    groups_map = {g.id: g for g in groups_result.scalars().all()}

    def _group_brief(app_obj):
        if app_obj.group_id and app_obj.group_id in groups_map:
            return group_to_brief(groups_map[app_obj.group_id])
        return None

    # Dernier déploiement automatique (success/failed) par app — badge « Non déployée »
    last_deployments = await _load_last_deployments(db)
    
    # Appliquer le filtre atelier (admin uniquement ; pour les users, toujours prod)
    _apply_workshop_query = lambda query: (
        query.where(Application.is_workshop == True) if workshop_filter == "true"
        else query if workshop_filter == "all"
        else query.where(Application.is_workshop == False)
    )
    
    if current_user.is_admin:
        # Tri alphabétique par nom pour un ordre stable et prévisible
        query = select(Application).where(Application.is_system_app == False)
        query = _apply_workshop_query(query)
        result = await db.execute(query.order_by(Application.name))
        apps = result.scalars().all()
        logger.debug(f"Admin {current_user.username}: {len(apps)} applications trouvées (filter={workshop_filter})")
        return [
            AppResponseWithMyRole.from_app_with_role(
                app,
                user_roles_map.get(app.slug),
                app_version=app.version,
                is_favorite=app.id in favorite_app_ids,
                group=_group_brief(app),
                last_deployment=last_deployments.get(app.slug)
            )
            for app in apps
        ]
    else:
        # Retourne uniquement les apps de l'utilisateur via app_roles
        # Les utilisateurs non-admin ne voient jamais les ateliers
        apps_count = len(user.app_roles)
        logger.debug(f"User {current_user.username}: {apps_count} applications accessibles")
        visible_roles = [ar for ar in user.app_roles if not getattr(ar.application, "is_system_app", False)]
        # Toujours exclure les ateliers pour les non-admins
        visible_roles = [ar for ar in visible_roles
                         if not getattr(ar.application, "is_workshop", False)]
        sorted_roles = sorted(visible_roles, key=lambda ar: ar.application.name.lower())
        return [
            AppResponseWithMyRole.from_app_with_role(
                ar.application,
                ar.role,
                app_version=ar.application.version,
                is_favorite=ar.application.id in favorite_app_ids,
                group=_group_brief(ar.application),
                last_deployment=last_deployments.get(ar.application.slug)
            )
            for ar in sorted_roles
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
    Met à jour current_app_slug dans la session pour le tracking.
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
    
    # Mettre à jour la session avec l'app courante
    from app.routes.audit import update_session_activity
    await update_session_activity(db, current_user.id, app_slug=app_slug)
    
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


# Rôles disponibles pour les apps (— référence unique depuis models.py)
AVAILABLE_ROLES = DEFAULT_ROLES


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
    _: User = Depends(get_current_admin),
    workshop_filter: str = Query("false", description="Filtre atelier: 'false' (prod uniquement, défaut), 'true' (ateliers uniquement), 'all' (tout)"),
):
    """Liste toutes les applications avec leurs utilisateurs (admin uniquement)
    
    Les indicateurs Docker/health arrivent via SSE en temps réel.
    
    Paramètre workshop_filter :
    - false (défaut) : apps production uniquement
    - true : apps atelier uniquement  
    - all : toutes les apps
    """
    query = select(Application).options(
        selectinload(Application.user_roles).selectinload(UserAppRole.user)
    ).where(Application.is_system_app == False)
    
    if workshop_filter == "true":
        query = query.where(Application.is_workshop == True)
    elif workshop_filter != "all":
        query = query.where(Application.is_workshop == False)
    
    result = await db.execute(query)
    apps = result.scalars().all()
    # Dernier déploiement automatique par app (badge « Non déployée » + bouton Déployer)
    last_deployments = await _load_last_deployments(db)
    return [
        AppWithUsersAndDocker.from_orm_model_with_docker(
            app, last_deployment=last_deployments.get(app.slug)
        )
        for app in apps
    ]


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
    frontend_https_port = getattr(app_data, 'frontend_https_port', None)
    if app_data.frontend_port:
        if frontend_https_port:
            frontend_url = f"https://{host}:{frontend_https_port}"
        else:
            frontend_url = f"http://{host}:{app_data.frontend_port}"
        callback_url = f"{frontend_url}/callback"
    
    # Créer l'application avec les URLs construites
    app = Application(
        name=app_data.name,
        slug=app_data.slug,
        description=app_data.description,
        icon=app_data.icon,
        color=app_data.color,
        category=app_data.category,
        api_url=api_url,
        frontend_url=frontend_url,
        callback_url=callback_url,
        frontend_https_port=frontend_https_port,
        requires_auth=app_data.requires_auth,
        is_shared_service=app_data.is_shared_service,
        is_unmanaged=getattr(app_data, "is_unmanaged", False),
        git_repo_url=app_data.git_repo_url,
    )
    # Groupe visuel (facultatif, validé)
    if getattr(app_data, "group_id", None) is not None:
        grp = await db.execute(select(AppGroup).where(AppGroup.id == app_data.group_id))
        group = grp.scalar_one_or_none()
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Groupe introuvable"
            )
        app.group_id = group.id
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
    
    # Réassocier les backups orphelins qui ont le même slug
    orphan_count = await reassociate_orphan_backups(db, app)
    if orphan_count > 0:
        logger.info(f"📦 {orphan_count} backup(s) orphelin(s) réassocié(s) à l'application '{app.slug}'")
    
    # Si l'app est un service partagé, l'ajouter à tous les SA existants
    shared_added_count = 0
    if app_data.is_shared_service:
        shared_added_count = await add_shared_service_to_all_service_accounts(db, app)
        await db.commit()
    
    # Notifier les clients SSE de la nouvelle application
    notify_event("apps_updated", {"action": "created", "app_slug": app.slug, "app_name": app.name})
    notify_stats_update()
    
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
            "is_shared_service": app.is_shared_service,
            "created_at": app.created_at
        }
    }
    
    # Ajouter info sur le partage M2M si applicable
    if app_data.is_shared_service and shared_added_count > 0:
        response["shared_service_info"] = {
            "message": f"🔗 Service partagé ajouté aux permissions de {shared_added_count} Service Account(s)",
            "service_accounts_updated": shared_added_count
        }
    
    if service_account_info:
        response["service_account"] = service_account_info
    else:
        response["hint"] = "💡 Pour créer un service account M2M, ajoutez 'create_service_account: true' ou utilisez POST /api/service-accounts/"
    
    return response


# ===== Auto-Registration Endpoints =====

@router.post("/allocate-ports", response_model=AllocatePortsResponse)
async def allocate_ports(
    request: Request,
    payload: AllocatePortsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Attribue dynamiquement les ports d'une application (pré-vol).

    ⚠️ Accessible UNIQUEMENT depuis les réseaux Docker / whitelist (IP).
    Idempotent : un même (slug, host) reçoit toujours les mêmes ports.
    """
    client_ip = request.client.host if request.client else None
    if not is_docker_network_ip(client_ip):
        logger.warning(f"🚫 allocate-ports refusé depuis IP non autorisée: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Allocation autorisée uniquement depuis le réseau Docker (IP: {client_ip})",
        )

    host = payload.host or settings.get_hub_host()
    try:
        alloc, reused = await allocate_ports_for_app(db, payload.slug, host, settings)
    except PortPoolExhausted as e:
        logger.error(f"❌ Pool de ports saturé pour '{payload.slug}': {e}")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except SubnetPoolExhausted as e:
        logger.error(f"❌ Pool de sous-réseaux saturé pour '{payload.slug}': {e}")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return AllocatePortsResponse(
        api_port=alloc.api_port,
        frontend_port=alloc.frontend_port,
        frontend_https_port=alloc.frontend_https_port,
        subnet=alloc.subnet,
        host=host,
        reused=reused,
    )


@router.get("/admin/ports/status", response_model=PortsStatusResponse)
async def ports_status(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """État du pool de ports d'applications (admin)."""
    from app.models import PortAllocation as _PA

    allocs = (await db.execute(select(_PA))).scalars().all()
    used_api = sorted({a.api_port for a in allocs})
    used_frontend = sorted(
        {a.frontend_port for a in allocs} | {a.frontend_https_port for a in allocs}
    )

    api_lo, api_hi = settings.app_port_api_start, settings.app_port_api_end
    fe_lo, fe_hi = settings.app_port_frontend_start, settings.app_port_frontend_end

    occupied = await docker_occupied_ports(settings, settings.get_hub_host())
    occupied_api = {p for p in occupied if api_lo <= p <= api_hi}
    occupied_fe = {p for p in occupied if fe_lo <= p <= fe_hi}
    host_occupied_ports = sorted(occupied_api | occupied_fe)

    api_total = api_hi - api_lo + 1
    frontend_slots = len(range(fe_lo, fe_hi, 2))

    fe_used = set(used_frontend) | occupied_fe
    blocked_slots = sum(
        1 for p in range(fe_lo, fe_hi, 2) if p in fe_used or (p + 1) in fe_used
    )

    return PortsStatusResponse(
        api_range=[api_lo, api_hi],
        frontend_range=[fe_lo, fe_hi],
        used_api_ports=used_api,
        used_frontend_ports=used_frontend,
        free_api_count=api_total - len(set(used_api) | occupied_api),
        free_frontend_count=frontend_slots - blocked_slots,
        host_occupied_ports=host_occupied_ports,
        allocations=[
            PortAllocationItem(
                id=a.id,
                app_slug=a.app_slug,
                host=a.host,
                api_port=a.api_port,
                frontend_port=a.frontend_port,
                frontend_https_port=a.frontend_https_port,
                subnet=a.subnet,
            )
            for a in allocs
        ],
    )


@router.delete("/admin/ports/allocations/{alloc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_port_allocation(
    alloc_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Supprime manuellement une allocation de ports (admin).

    Utile pour nettoyer une allocation orpheline (app supprimée hors HUB,
    ancien test, etc.) et rendre les ports au pool.
    """
    from app.models import PortAllocation as _PA

    alloc = (
        await db.execute(select(_PA).where(_PA.id == alloc_id))
    ).scalar_one_or_none()
    if not alloc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allocation non trouvée")

    await db.delete(alloc)
    await db.commit()
    logger.info("🧹 Allocation de ports #%d (%s) supprimée par %s", alloc_id, alloc.app_slug, current_admin.username)


@router.post("/register", response_model=AppRegisterResponse)
async def register_app(
    app_data: AppRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(optional_oauth2_scheme),
):
    """
    Auto-enregistrement d'une application depuis le réseau Docker.

    🔶 LEGACY : l'authentification par IP Docker sera supprimée après migration
    complète des applications vers le mode M2M enforced. Les nouvelles apps
    doivent fournir un token service account (Bearer) pour bénéficier du mode
    renforcé et de l'audit complet.

    ⚠️ Accessible UNIQUEMENT depuis les réseaux Docker (IP whitelist).
    ⚠️ SÉCURITÉ : Les apps auto-enregistrées sont en statut "pending" et
       nécessitent une approbation admin via /api/apps/{slug}/approve
    
    L'application envoie son slug (défini dans son .env) et reçoit :
    - Ses credentials M2M (client_id, client_secret) si nouvelle app
    - L'app reste en statut "pending" jusqu'à approbation admin
    
    Flux :
    1. L'app démarre et appelle cet endpoint avec son slug
    2. Le Hub crée l'app en statut "pending" + service account (inactifs)
    3. L'app reçoit ses credentials (valides mais app inactive)
    4. Un admin doit approuver l'app via l'interface ou l'API
    5. Une fois approuvée, l'app et son SA deviennent actifs
    6. Si is_shared=true, l'app sera ajoutée à tous les SA lors de l'approbation
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
    registration_sa = await _service_account_from_m2m_token(token, db) if token else None
    
    replaced = False
    old_app_info = None
    
    if existing_app:
        if registration_sa and existing_app not in registration_sa.applications:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Le service account ne peut pas réinscrire cette application",
            )
        if existing_app.m2m_enforcement_mode == "enforced" and registration_sa is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token M2M requis pour réinscrire cette application",
            )
        # Application existante - vérifier si on doit la remplacer ou mettre à jour
        if settings.force_replace_app:
            logger.info(f"🔄 Remplacement automatique de l'application '{app_data.slug}' (FORCE_REPLACE_APP=true)")
            
            # Sauvegarder les infos pour l'audit
            old_app_info = await delete_app_cascade(db, existing_app, delete_credentials_file=True)
            await db.flush()  # Flush pour libérer le slug
            replaced = True
            
            logger.info(f"✅ Ancienne application '{app_data.slug}' supprimée, recréation en cours...")
        else:
            # Mode sans remplacement - MAIS mettre à jour les URLs et infos si nécessaire
            logger.info(f"⚠️ Application '{app_data.slug}' déjà enregistrée, vérification des mises à jour...")
            
            updated_fields = []
            if registration_sa and existing_app.m2m_enforcement_mode != "enforced":
                existing_app.m2m_enforcement_mode = "enforced"
                updated_fields.append("m2m_enforcement_mode: compat → enforced")
            # Utiliser custom_host si fourni (app distante), sinon le host du HUB
            if app_data.custom_host:
                host = app_data.custom_host
            else:
                host = settings.get_hub_host()
            
            # Mettre à jour les URLs si les ports ont changé
            new_api_url = f"http://{host}:{app_data.api_port}"
            if existing_app.api_url != new_api_url:
                old_value = existing_app.api_url
                existing_app.api_url = new_api_url
                updated_fields.append(f"api_url: {old_value} → {new_api_url}")
                logger.info(f"🔄 Mise à jour api_url: {old_value} → {new_api_url}")
            
            # Mettre à jour frontend URL si port fourni
            frontend_https_port = getattr(app_data, 'frontend_https_port', None)
            if app_data.frontend_port:
                if frontend_https_port:
                    new_frontend_url = f"https://{host}:{frontend_https_port}"
                else:
                    new_frontend_url = f"http://{host}:{app_data.frontend_port}"
            else:
                new_frontend_url = None
            
            if new_frontend_url and existing_app.frontend_url != new_frontend_url:
                old_value = existing_app.frontend_url
                existing_app.frontend_url = new_frontend_url
                existing_app.callback_url = f"{new_frontend_url}/callback"
                updated_fields.append(f"frontend_url: {old_value} → {new_frontend_url}")
                logger.info(f"🔄 Mise à jour frontend_url: {old_value} → {new_frontend_url}")
            
            # Mettre à jour frontend_https_port
            if existing_app.frontend_https_port != frontend_https_port:
                old_https_port = existing_app.frontend_https_port
                existing_app.frontend_https_port = frontend_https_port
                updated_fields.append(f"frontend_https_port: {old_https_port} → {frontend_https_port}")
                logger.info(f"🔄 Mise à jour frontend_https_port: {old_https_port} → {frontend_https_port}")
            
            # Mettre à jour is_shared_service si différent
            if existing_app.is_shared_service != app_data.is_shared:
                old_shared = existing_app.is_shared_service
                existing_app.is_shared_service = app_data.is_shared
                updated_fields.append(f"is_shared: {old_shared} → {app_data.is_shared}")
                logger.info(f"🔄 Mise à jour is_shared_service: {old_shared} → {app_data.is_shared}")
            
            # Mettre à jour is_remote (sur un serveur différent du Hub)
            is_now_remote = app_data.custom_host and app_data.custom_host != settings.get_hub_host()
            if existing_app.is_remote != is_now_remote:
                old_remote = existing_app.is_remote
                existing_app.is_remote = is_now_remote
                updated_fields.append(f"is_remote: {old_remote} → {is_now_remote}")
                logger.info(f"🔄 Mise à jour is_remote: {old_remote} → {is_now_remote}")
            
            # Recalculer is_workshop depuis le slug
            is_now_workshop = app_data.slug.endswith("-ws")
            if existing_app.is_workshop != is_now_workshop:
                old_workshop = existing_app.is_workshop
                existing_app.is_workshop = is_now_workshop
                updated_fields.append(f"is_workshop: {old_workshop} → {is_now_workshop}")
                logger.info(f"🔄 Mise à jour is_workshop: {old_workshop} → {is_now_workshop}")

            # WORKSHOP DEV AUTO-APPROVAL (ré-enregistrement) :
            # Si l'atelier était en pending et que l'app de base est approuvée
            # en prod, l'auto-approuver — pas besoin d'intervention admin en DEV.
            workshop_auto_approved = False
            if is_now_workshop and existing_app.approval_status == ApprovalStatus.pending:
                base_slug_rr = app_data.slug[:-3]  # retirer "-ws"
                if base_slug_rr:
                    result = await db.execute(
                        select(Application).where(
                            Application.slug == base_slug_rr,
                            Application.approval_status == ApprovalStatus.approved
                        )
                    )
                    base_app_rr = result.scalar_one_or_none()
                    if base_app_rr:
                        existing_app.approval_status = ApprovalStatus.approved
                        existing_app.is_active = True
                        existing_app.approved_at = utcnow()
                        existing_app.approved_by_id = None
                        workshop_auto_approved = True
                        updated_fields.append("approval_status: pending → approved (atelier auto-approuvé)")
                        logger.info(
                            f"🚀 Atelier '{app_data.slug}' auto-approuvé au ré-enregistrement "
                            f"(app de base '{base_slug_rr}' déjà approuvée)"
                        )

            # Mettre à jour le nom si fourni et différent
            if app_data.name and existing_app.name != app_data.name:
                old_value = existing_app.name
                existing_app.name = app_data.name
                updated_fields.append(f"name: {old_value} → {app_data.name}")
            
            # Mettre à jour la description si fournie et différente
            if app_data.description and existing_app.description != app_data.description:
                existing_app.description = app_data.description
                updated_fields.append("description mise à jour")
            
            # Mettre à jour la version si fournie et différente
            if app_data.version and existing_app.version != app_data.version:
                old_value = existing_app.version
                existing_app.version = app_data.version
                updated_fields.append(f"version: {old_value} → {app_data.version}")
            
            # Mettre à jour la spec OpenAPI si fournie
            if app_data.api_endpoints:
                filtered_spec, total_ep, filtered_ep = filter_openapi_spec(app_data.api_endpoints)
                existing_app.api_endpoints = filtered_spec
                existing_app.api_endpoints_updated_at = utcnow()
                updated_fields.append(f"api_endpoints: {total_ep - filtered_ep} endpoints métier")
            
            # Commit les changements si nécessaire
            if updated_fields:
                await db.commit()
                await db.refresh(existing_app)
                
                # Logger l'action de mise à jour
                await log_action(
                    db=db,
                    user_id=None,  # Action système (auto-registration)
                    action="update_app_urls",
                    target=app_data.slug,
                    ip_address=client_ip,
                    user_agent=None,
                    details=f"Mise à jour automatique via auto-registration: {', '.join(updated_fields)}"
                )
                await db.commit()
                
                # Notifier les clients SSE de la mise à jour
                notify_event("apps_updated", {
                    "action": "updated", 
                    "app_slug": existing_app.slug, 
                    "app_name": existing_app.name, 
                    "source": "auto-register",
                    "updates": updated_fields
                })
                
                message = f"✅ Application '{app_data.slug}' mise à jour: {', '.join(updated_fields)}"
                logger.info(message)
            else:
                message = f"Application '{app_data.slug}' déjà enregistrée (aucune modification nécessaire). Utilisez /api/apps/me/credentials pour récupérer vos credentials."
                logger.info(f"✅ {message}")

            if registration_sa is None:
                await log_action(
                    db=db,
                    user_id=None,
                    service_account_id=None,
                    action="legacy_reregistration",
                    target=app_data.slug,
                    request=request,
                    response_status=status.HTTP_200_OK,
                    details="Réinscription legacy autorisée en mode compatibilité",
                )
                await db.commit()
            else:
                await log_action(
                    db=db,
                    user_id=None,
                    service_account_id=registration_sa.id,
                    action="m2m_reregistration_enforced",
                    target=app_data.slug,
                    request=request,
                    response_status=status.HTTP_200_OK,
                    details="Réinscription authentifiée par service account",
                )
                await db.commit()
            
            # Trouver le service account associé (rechargement explicite après refresh)
            result_sa = await db.execute(
                select(Application)
                .options(selectinload(Application.service_accounts))
                .where(Application.id == existing_app.id)
            )
            existing_app = result_sa.scalar_one()
            sa = next((sa for sa in existing_app.service_accounts), None)
            
            # Régénérer le secret M2M : l'app qui se ré-enregistre provient du
            # réseau Docker (trusted) et a probablement perdu ses credentials
            # (nouveau conteneur, volume non persistant). Sans ce régénération,
            # l'app est bloquée : register ne retourne pas le secret, et
            # regenerate-secret exige un token M2M qu'elle n'a pas.
            if sa:
                # Activer le SA si l'atelier vient d'être auto-approuvé au ré-enregistrement
                if workshop_auto_approved and not sa.is_active:
                    sa.is_active = True
                    logger.info(f"✅ SA activé pour atelier auto-approuvé '{app_data.slug}'")
                
                client_secret = ServiceAccount.generate_client_secret()
                sa.client_secret_hash = get_password_hash(client_secret)
                await db.commit()
                logger.info(f"🔑 Secret M2M régénéré pour '{app_data.slug}' (ré-enregistrement)")
            else:
                client_secret = None
            
            return AppRegisterResponse(
                message=message,
                application=AppResponse.model_validate(existing_app),
                credentials={"client_id": sa.client_id, "client_secret": client_secret} if sa else None,
                is_new=False
            )
    
    # Nouvelle application (ou remplacement) - créer l'app et le service account
    # Utiliser custom_host si fourni, sinon le host du Hub
    if app_data.custom_host:
        host = app_data.custom_host
    else:
        host = settings.get_hub_host()
    
    # Déterminer si c'est une app distante (sur un serveur différent du Hub)
    is_remote = app_data.custom_host and app_data.custom_host != settings.get_hub_host()
    if is_remote:
        logger.info(f"🌐 Application DISTANTE détectée - host: {host} (IP source: {client_ip})")
    else:
        logger.info(f"📍 Application LOCALE détectée - host: {host} (IP source: {client_ip})")
    
    # Construire l'URL API
    api_url = f"http://{host}:{app_data.api_port}"
    
    # Construire l'URL frontend si port fourni
    frontend_https_port = getattr(app_data, 'frontend_https_port', None)
    if app_data.frontend_port:
        if frontend_https_port:
            frontend_url = f"https://{host}:{frontend_https_port}"
        else:
            frontend_url = f"http://{host}:{app_data.frontend_port}"
    else:
        frontend_url = None
    callback_url = f"{frontend_url}/callback" if frontend_url else None
    
    is_workshop = app_data.slug.endswith("-ws")

    # WORKSHOP DEV AUTO-APPROVAL :
    # En mode DEV (hubctl --dev), les apps atelier (slug-ws) sont des clones
    # d'une app de production déjà validée. Inutile de forcer l'admin à les
    # approuver une par une : si l'app de base (sans "-ws") est déjà approuvée,
    # l'atelier est auto-approuvé immédiatement.
    auto_approve_workshop = False
    base_slug = None
    if is_workshop:
        base_slug = app_data.slug[:-3]  # retirer le suffixe "-ws"
        if base_slug:
            result = await db.execute(
                select(Application).where(
                    Application.slug == base_slug,
                    Application.approval_status == ApprovalStatus.approved
                )
            )
            base_app = result.scalar_one_or_none()
            auto_approve_workshop = base_app is not None
            if auto_approve_workshop:
                logger.info(
                    f"🚀 Atelier '{app_data.slug}' auto-approuvé "
                    f"(app de base '{base_slug}' déjà approuvée en production)"
                )

    # Description enrichie pour les apps distantes et workshops
    description = app_data.description
    if not description:
        if auto_approve_workshop:
            description = f"Atelier de {base_slug} (auto-approuvé — clone DEV)"
        elif is_remote:
            description = f"Application {app_data.slug} (auto-enregistrée depuis serveur distant {client_ip}, en attente d'approbation)"
        else:
            description = f"Application {app_data.slug} (auto-enregistrée, en attente d'approbation)"

    now_ts = utcnow()

    app = Application(
        name=app_data.name or app_data.slug.replace("-", " ").title(),
        slug=app_data.slug,
        description=description,
        api_url=api_url,
        frontend_url=frontend_url,
        callback_url=callback_url,
        frontend_https_port=frontend_https_port,
        requires_auth=True,
        is_shared_service=app_data.is_shared,  # Permet l'accès M2M si True
        is_remote=is_remote,  # Marquer comme app distante si custom_host fourni
        registered_from_ip=client_ip,  # Enregistrer l'IP source
        version=app_data.version,  # Version de l'app, capturée à l'enregistrement
        approval_status=ApprovalStatus.approved if auto_approve_workshop else ApprovalStatus.pending,
        is_active=auto_approve_workshop,  # Actif immédiatement si auto-approuvé
        approved_at=now_ts if auto_approve_workshop else None,
        approved_by_id=None,  # Système (auto-approbation workshop DEV)
        is_workshop=is_workshop
    )
    
    # Stocker la spec OpenAPI si fournie (avec filtrage automatique)
    if app_data.api_endpoints:
        filtered_spec, total_ep, filtered_ep = filter_openapi_spec(app_data.api_endpoints)
        app.api_endpoints = filtered_spec
        app.api_endpoints_updated_at = utcnow()
        logger.info(f"📋 Spec OpenAPI reçue à l'enregistrement de '{app_data.slug}': {total_ep - filtered_ep} endpoints métier, {filtered_ep} techniques exclus")
    
    db.add(app)
    await db.flush()
    
    if auto_approve_workshop:
        logger.info(f"🚀 Application atelier '{app_data.slug}' créée avec AUTO-APPROBATION (base: {base_slug})")
    elif is_remote:
        logger.info(f"🔒🌐 Application DISTANTE '{app_data.slug}' créée en statut PENDING (host: {host}, IP source: {client_ip})")
    else:
        logger.info(f"🔒 Application '{app_data.slug}' créée en statut PENDING (en attente d'approbation admin)")
    
    # Créer le service account automatiquement
    client_id = ServiceAccount.generate_client_id()
    client_secret = ServiceAccount.generate_client_secret()
    
    sa_desc = f"Service account auto-créé pour {app.name}"
    if not auto_approve_workshop:
        sa_desc += " (en attente d'approbation)"
    
    service_account = ServiceAccount(
        name=f"SA - {app.name}",
        description=sa_desc,
        client_id=client_id,
        client_secret_hash=get_password_hash(client_secret),
        is_active=auto_approve_workshop  # Actif immédiatement si auto-approuvé
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
    
    # NOTE: Les services partagés seront ajoutés lors de l'approbation
    # Pour l'instant, on note juste quels services sont disponibles
    shared_slugs = []
    
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
    
    # Réassocier les backups orphelins qui ont le même slug
    # Cela permet de récupérer les sauvegardes d'une app précédemment supprimée
    orphan_count = await reassociate_orphan_backups(db, app)
    if orphan_count > 0:
        logger.info(f"📦 {orphan_count} backup(s) orphelin(s) réassocié(s) à l'application '{app.slug}'")
    
    # Si l'app est un service partagé (is_shared=True), l'ajouter à tous les SA existants
    shared_added_count = 0
    if app_data.is_shared:
        shared_added_count = await add_shared_service_to_all_service_accounts(db, app)
        if shared_added_count > 0:
            await db.commit()
            logger.info(f"🔗 Service partagé '{app.slug}' ajouté à {shared_added_count} Service Account(s) existants")
    
    # Notifier les clients SSE (auto-registration)
    action = "replaced" if replaced else "created"
    notify_event("apps_updated", {
        "action": action, 
        "app_slug": app.slug, 
        "app_name": app.name, 
        "source": "auto-register",
        "is_remote": is_remote,
        "registered_from_ip": client_ip
    })
    notify_stats_update()
    
    # Sauvegarder les credentials dans le fichier (backup)
    all_app_slugs = [app_data.slug] + shared_slugs
    save_service_account_credentials(
        name=app_data.slug,
        client_id=client_id,
        client_secret=client_secret,
        app_slugs=all_app_slugs
    )
    
    # Message adapté selon le statut d'approbation
    if auto_approve_workshop:
        message = f"🚀 Atelier '{app_data.slug}' enregistré et AUTO-APPROUVÉ (base: {base_slug})."
        message += " Credentials M2M actifs."
    elif is_remote:
        message = f"⏳🌐 Application DISTANTE '{app_data.slug}' enregistrée depuis {client_ip}. EN ATTENTE D'APPROBATION ADMIN."
        message += " Credentials M2M générés mais inactifs jusqu'à approbation."
    else:
        message = f"⏳ Application '{app_data.slug}' enregistrée avec succès. EN ATTENTE D'APPROBATION ADMIN."
        message += " Credentials M2M générés mais inactifs jusqu'à approbation."
    
    if app_data.is_shared and not auto_approve_workshop:
        message += " Le mode service partagé sera activé après approbation."
    
    if auto_approve_workshop:
        logger.info(f"📝 Atelier '{app_data.slug}' auto-enregistré depuis {client_ip} (AUTO-APPROVED)")
    else:
        logger.info(f"📝 Application '{app_data.slug}' auto-enregistrée depuis {client_ip} (PENDING, remote={is_remote})")
    
    return AppRegisterResponse(
        message=message,
        application=AppResponse.model_validate(app),
        credentials={
            "client_id": client_id,
            "client_secret": client_secret,
            "hub_host": settings.get_hub_host(),  # Toujours retourner le host du HUB pour la connexion
            "hub_api_port": settings.hub_api_port if hasattr(settings, 'hub_api_port') else 8000
        },
        is_new=True,
        pending_approval=not auto_approve_workshop  # Pas d'attente si auto-approuvé
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
    
    **Si l'application existe déjà** :
    - Met à jour le statut `is_shared_service` si différent
    - Met à jour les URLs si les ports ont changé
    - Retourne les infos existantes (sans régénérer les credentials)
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
        # Application existante - mettre à jour si nécessaire
        logger.info(f"⚠️ Application '{app_data.slug}' déjà enregistrée, vérification des mises à jour...")
        
        updated_fields = []
        host = settings.get_hub_host()
        shared_changed = False
        old_shared_value = existing_app.is_shared_service
        
        # Mettre à jour is_shared_service si différent
        if existing_app.is_shared_service != app_data.is_shared:
            existing_app.is_shared_service = app_data.is_shared
            updated_fields.append(f"is_shared: {old_shared_value} → {app_data.is_shared}")
            shared_changed = True
            logger.info(f"🔄 Mise à jour is_shared_service: {old_shared_value} → {app_data.is_shared}")
        
        # Mettre à jour les URLs si les ports ont changé
        new_api_url = f"http://{host}:{app_data.api_port}"
        if existing_app.api_url != new_api_url:
            old_value = existing_app.api_url
            existing_app.api_url = new_api_url
            updated_fields.append(f"api_url: {old_value} → {new_api_url}")
        
        # Mettre à jour frontend URL si port fourni
        frontend_https_port = getattr(app_data, 'frontend_https_port', None)
        if app_data.frontend_port:
            if frontend_https_port:
                new_frontend_url = f"https://{host}:{frontend_https_port}"
            else:
                new_frontend_url = f"http://{host}:{app_data.frontend_port}"
        else:
            new_frontend_url = None
        
        if new_frontend_url and existing_app.frontend_url != new_frontend_url:
            old_value = existing_app.frontend_url
            existing_app.frontend_url = new_frontend_url
            existing_app.callback_url = f"{new_frontend_url}/callback"
            updated_fields.append(f"frontend_url: {old_value} → {new_frontend_url}")
        
        # Mettre à jour frontend_https_port
        if existing_app.frontend_https_port != frontend_https_port:
            old_https_port = existing_app.frontend_https_port
            existing_app.frontend_https_port = frontend_https_port
            updated_fields.append(f"frontend_https_port: {old_https_port} → {frontend_https_port}")
        
        # Mettre à jour le nom si fourni et différent
        if app_data.name and existing_app.name != app_data.name:
            old_value = existing_app.name
            existing_app.name = app_data.name
            updated_fields.append(f"name: {old_value} → {app_data.name}")
        
        # Mettre à jour la description si fournie et différente
        if app_data.description and existing_app.description != app_data.description:
            existing_app.description = app_data.description
            updated_fields.append("description mise à jour")
        
        # Commit les changements si nécessaire
        if updated_fields:
            await db.commit()
            await db.refresh(existing_app)
            
            # Gérer les permissions M2M si is_shared a changé
            if shared_changed:
                if app_data.is_shared:
                    # Passé à True → Ajouter aux permissions de tous les SA
                    sa_count = await add_shared_service_to_all_service_accounts(db, existing_app)
                    if sa_count > 0:
                        await db.commit()
                        updated_fields.append(f"ajouté à {sa_count} SA")
                else:
                    # Passé à False → Retirer des permissions des SA (sauf propriétaire)
                    sa_count = await remove_shared_service_from_all_service_accounts(db, existing_app)
                    if sa_count > 0:
                        await db.commit()
                        updated_fields.append(f"retiré de {sa_count} SA")
            
            # Notifier les clients SSE de la mise à jour
            notify_event("apps_updated", {
                "action": "updated", 
                "app_slug": existing_app.slug, 
                "app_name": existing_app.name, 
                "source": "auto-register",
                "updates": updated_fields
            })
            
            message = f"Application '{app_data.slug}' mise à jour: {', '.join(updated_fields)}"
            logger.info(f"✅ {message}")
        else:
            message = f"Application '{app_data.slug}' existe déjà (aucune modification nécessaire)."
        
        # Trouver le service account associé
        sa = next((sa for sa in existing_app.service_accounts), None)
        
        return AppRegisterResponse(
            message=message,
            application=AppResponse.model_validate(existing_app),
            credentials={"client_id": sa.client_id, "is_shared": existing_app.is_shared_service} if sa else None,
            is_new=False
        )
    
    # Nouvelle application - créer l'app et le service account
    host = settings.get_hub_host()
    
    # Construire l'URL API
    api_url = f"http://{host}:{app_data.api_port}"
    
    # Construire l'URL frontend si port fourni
    frontend_https_port = getattr(app_data, 'frontend_https_port', None)
    if app_data.frontend_port:
        if frontend_https_port:
            frontend_url = f"https://{host}:{frontend_https_port}"
        else:
            frontend_url = f"http://{host}:{app_data.frontend_port}"
    else:
        frontend_url = None
    callback_url = f"{frontend_url}/callback" if frontend_url else None
    
    app = Application(
        name=app_data.name or app_data.slug.replace("-", " ").title(),
        slug=app_data.slug,
        description=app_data.description or f"Application {app_data.slug} (pré-enregistrée)",
        api_url=api_url,
        frontend_url=frontend_url,
        callback_url=callback_url,
        frontend_https_port=frontend_https_port,
        requires_auth=True,
        is_shared_service=app_data.is_shared  # Permet l'accès M2M si True
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
    
    # Réassocier les backups orphelins qui ont le même slug
    orphan_count = await reassociate_orphan_backups(db, app)
    if orphan_count > 0:
        logger.info(f"📦 {orphan_count} backup(s) orphelin(s) réassocié(s) à l'application '{app.slug}'")
    
    # Si l'app est un service partagé, l'ajouter à tous les SA existants
    shared_added_count = 0
    if app_data.is_shared:
        shared_added_count = await add_shared_service_to_all_service_accounts(db, app)
        if shared_added_count > 0:
            await db.commit()
            logger.info(f"🔗 Service partagé '{app.slug}' ajouté à {shared_added_count} Service Account(s) existants")
    
    # Notifier les clients SSE (admin auto-register)
    notify_event("apps_updated", {"action": "created", "app_slug": app.slug, "app_name": app.name, "source": "admin-register"})
    notify_stats_update()
    
    # Sauvegarder les credentials dans le fichier
    all_app_slugs = [app_data.slug] + shared_slugs
    secrets_file = save_service_account_credentials(
        name=app_data.slug,
        client_id=client_id,
        client_secret=client_secret,
        app_slugs=all_app_slugs
    )
    
    logger.info(f"✅ Application '{app_data.slug}' pré-enregistrée par admin, credentials sauvés dans {secrets_file}")
    
    # Message avec info sur le partage M2M si applicable
    message = f"Application '{app_data.slug}' créée avec succès. Credentials M2M générés et sauvegardés."
    if app_data.is_shared and shared_added_count > 0:
        message += f" Service partagé ajouté à {shared_added_count} SA existant(s)."
    
    return AppRegisterResponse(
        message=message,
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


@router.post("/batch-import", response_model=BatchImportResponse)
async def batch_import_apps(
    data: BatchImportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Import lot d'applications détectées par le scanner Docker.
    
    Crée plusieurs applications avec leurs Service Accounts en une seule opération.
    Utilise la même logique que l'auto-register individuel.
    
    ⚠️ Admin uniquement.
    """
    logger.info(f"📦 Import lot de {len(data.apps)} application(s) par {current_user.username}")
    
    results = []
    created = 0
    updated = 0
    errors = 0
    host = settings.get_hub_host()
    
    for app_item in data.apps:
        try:
            # Vérifier si l'application existe déjà
            result = await db.execute(
                select(Application)
                .options(selectinload(Application.service_accounts))
                .where(Application.slug == app_item.base_slug)
            )
            existing_app = result.scalar_one_or_none()
            
            if existing_app:
                # Application existante - mettre à jour si nécessaire
                updated_fields = []
                shared_changed = False
                old_shared = existing_app.is_shared_service
                
                if existing_app.is_shared_service != app_item.is_shared:
                    existing_app.is_shared_service = app_item.is_shared
                    updated_fields.append(f"is_shared: {old_shared} → {app_item.is_shared}")
                    shared_changed = True
                
                new_api_url = f"http://{host}:{app_item.api_port}"
                if existing_app.api_url != new_api_url:
                    existing_app.api_url = new_api_url
                    updated_fields.append(f"api_url → {new_api_url}")
                
                if app_item.frontend_port:
                    new_frontend_url = f"http://{host}:{app_item.frontend_port}"
                    if existing_app.frontend_url != new_frontend_url:
                        existing_app.frontend_url = new_frontend_url
                        existing_app.callback_url = f"{new_frontend_url}/callback"
                        updated_fields.append(f"frontend_url → {new_frontend_url}")
                
                if app_item.name and existing_app.name != app_item.name:
                    existing_app.name = app_item.name
                    updated_fields.append(f"name → {app_item.name}")
                
                if updated_fields:
                    await db.commit()
                    await db.refresh(existing_app)
                    if shared_changed:
                        if app_item.is_shared:
                            await add_shared_service_to_all_service_accounts(db, existing_app)
                        else:
                            await remove_shared_service_from_all_service_accounts(db, existing_app)
                        await db.commit()
                
                sa = next((sa for sa in existing_app.service_accounts), None)
                results.append(BatchAppResult(
                    slug=app_item.base_slug,
                    name=existing_app.name,
                    success=True,
                    is_new=False,
                    message=f"Mise à jour: {', '.join(updated_fields)}" if updated_fields else "Déjà enregistrée, aucun changement",
                    credentials={"client_id": sa.client_id} if sa else None
                ))
                updated += 1
                continue
            
            # Nouvelle application
            api_url = f"http://{host}:{app_item.api_port}"
            frontend_url = f"http://{host}:{app_item.frontend_port}" if app_item.frontend_port else None
            callback_url = f"{frontend_url}/callback" if frontend_url else None
            
            app_name = app_item.name or app_item.base_slug.replace("-", " ").title()
            app = Application(
                name=app_name,
                slug=app_item.base_slug,
                description=app_item.description or f"Application {app_item.base_slug} (import lot)",
                icon="bi-box-seam",
                color="#0db7ed",
                api_url=api_url,
                frontend_url=frontend_url,
                callback_url=callback_url,
                requires_auth=True,
                is_shared_service=app_item.is_shared
            )
            db.add(app)
            await db.flush()
            
            # Créer le service account
            client_id = ServiceAccount.generate_client_id()
            client_secret = ServiceAccount.generate_client_secret()
            
            service_account = ServiceAccount(
                name=f"SA - {app_name}",
                description=f"Service account auto-créé pour {app_name}",
                client_id=client_id,
                client_secret_hash=get_password_hash(client_secret)
            )
            db.add(service_account)
            await db.flush()
            
            # Associer le SA à l'app
            sa_result = await db.execute(
                select(ServiceAccount)
                .options(selectinload(ServiceAccount.applications))
                .where(ServiceAccount.id == service_account.id)
            )
            service_account = sa_result.scalar_one()
            service_account.applications.append(app)
            
            # Ajouter les services partagés au SA
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
            
            # Réassocier les backups orphelins
            await reassociate_orphan_backups(db, app)
            
            # Si service partagé, l'ajouter à tous les SA
            if app_item.is_shared:
                count = await add_shared_service_to_all_service_accounts(db, app)
                if count > 0:
                    await db.commit()
            
            # Sauvegarder les credentials
            all_slugs = [app_item.base_slug] + shared_slugs
            secrets_file = save_service_account_credentials(
                name=app_item.base_slug,
                client_id=client_id,
                client_secret=client_secret,
                app_slugs=all_slugs
            )
            
            results.append(BatchAppResult(
                slug=app_item.base_slug,
                name=app_name,
                success=True,
                is_new=True,
                message=f"Créée avec succès. Credentials dans {secrets_file}",
                credentials={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "secrets_file": secrets_file
                }
            ))
            created += 1
            
        except Exception as e:
            logger.error(f"❌ Erreur import '{app_item.base_slug}': {e}")
            results.append(BatchAppResult(
                slug=app_item.base_slug,
                name=app_item.name or app_item.base_slug,
                success=False,
                message=str(e)
            ))
            errors += 1
            # Rollback pour cette app uniquement
            await db.rollback()
    
    # Notifier les clients SSE
    if created > 0 or updated > 0:
        notify_event("apps_updated", {
            "action": "batch_import",
            "created": created,
            "updated": updated,
            "source": "batch-import",
            "user": current_user.username
        })
        notify_stats_update()
    
    logger.info(f"✅ Import lot terminé: {created} créées, {updated} mises à jour, {errors} erreur(s)")
    
    return BatchImportResponse(
        total=len(data.apps),
        created=created,
        updated=updated,
        errors=errors,
        results=results
    )


@router.patch("/{app_id}/toggle-active")
async def toggle_app_active(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Bascule l'état actif/inactif d'une application en un clic.
    
    ⚠️ Admin uniquement.
    """
    result = await db.execute(select(Application).where(Application.id == app_id))
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(status_code=404, detail="Application non trouvée")

    if getattr(app, "is_system_app", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'application système Hub ne peut pas être activée/désactivée depuis cette vue"
        )
    
    app.is_active = not app.is_active
    await db.commit()
    await db.refresh(app)
    
    status_label = "activée" if app.is_active else "désactivée"
    logger.info(f"🔄 Application '{app.slug}' {status_label} par {current_user.username}")
    
    # Log audit
    await log_action(
        db=db,
        action="app_toggled",
        user_id=current_user.id,
        details=f"Application '{app.name}' {status_label}",
        target=app.slug
    )
    
    notify_event("apps_updated", {
        "action": "toggled",
        "app_slug": app.slug,
        "app_name": app.name,
        "is_active": app.is_active,
        "user": current_user.username
    })
    
    return {
        "message": f"Application '{app.name}' {status_label}",
        "app_id": app.id,
        "is_active": app.is_active
    }


@router.get("/me/credentials", response_model=AppCredentialsResponse)
async def get_app_credentials(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(optional_oauth2_scheme),
):
    """
    Récupère les credentials M2M d'une application.

    Authentification (par priorité) :
    1. Token M2M valide (recommandé)
    2. IP whitelist + X-Slug/X-Client-ID (déprécié)

    Le client_secret n'est PAS retourné (sécurité).
    """
    client_ip = request.client.host if request.client else None
    client_id = request.headers.get("X-Client-ID")
    app_slug = request.headers.get("X-Slug")

    if token:
        try:
            payload = await verify_token_not_revoked(token, db)
            if payload.get("type") != "service_account":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Seuls les tokens M2M sont acceptés"
                )
            client_id_from_token = payload.get("sub")
            result = await db.execute(
                select(ServiceAccount)
                .options(selectinload(ServiceAccount.applications))
                .where(ServiceAccount.client_id == client_id_from_token)
            )
            sa = result.scalar_one_or_none()
            app = sa.applications[0] if sa and sa.applications else None
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Token M2M invalide pour /me/credentials: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token M2M invalide ou expiré"
            )
    else:
        if not is_docker_network_ip(client_ip):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Accès autorisé uniquement depuis le réseau Docker ou avec un token M2M (IP: {client_ip})"
            )

        if not client_id and not app_slug:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Header X-Client-ID ou X-Slug requis"
            )

        logger.warning(
            "⏳ DEPRECATION: /me/credentials appelé sans token M2M (IP=%s, X-Slug=%s, X-Client-ID=%s). "
            "Cette application doit migrer vers l'authentification par token M2M.",
            client_ip, app_slug or "N/A", client_id or "N/A"
        )

        if client_id:
            result = await db.execute(
                select(ServiceAccount)
                .options(selectinload(ServiceAccount.applications))
                .where(ServiceAccount.client_id == client_id)
            )
            sa = result.scalar_one_or_none()
            app = sa.applications[0] if sa and sa.applications else None
        else:
            result = await db.execute(
                select(Application)
                .options(
                    selectinload(Application.service_accounts)
                    .selectinload(ServiceAccount.applications)
                )
                .where(Application.slug == app_slug)
            )
            app = result.scalar_one_or_none()
            if not app:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Application '{app_slug}' non trouvée"
                )
            sa = _get_owner_service_account_for_app(app)

        if not sa:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Service account non trouvé"
            )

        if not app:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Aucune application associée à ce service account"
            )

        if client_id and not sa.applications:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Aucune application associée à ce service account"
            )

    if not sa or not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application ou service account non trouvé"
        )

    host = settings.get_hub_host()

    return AppCredentialsResponse(
        app_slug=app.slug,
        app_name=app.name,
        client_id=sa.client_id,
        client_secret=None,
        hub_host=host,
        hub_api_port=8000,
        message="Credentials récupérés. Le client_secret n'est pas retourné pour des raisons de sécurité."
    )


@router.post("/me/regenerate-secret", response_model=AppCredentialsResponse)
async def regenerate_app_secret(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(optional_oauth2_scheme),
):
    """
    Regénère le client_secret d'une application.

    Authentification : Token M2M valide obligatoire — header Authorization: Bearer <token>

    ⚠️ L'ancien secret sera IMMÉDIATEMENT invalidé !

    Le nouveau secret est retourné UNE SEULE FOIS dans cette réponse.
    """
    client_ip = request.client.host if request.client else None
    client_id = request.headers.get("X-Client-ID")
    app_slug = request.headers.get("X-Slug")

    if token:
        try:
            payload = await verify_token_not_revoked(token, db)
            if payload.get("type") != "service_account":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Seuls les tokens M2M (service accounts) sont acceptés"
                )
            client_id_from_token = payload.get("sub")

            result = await db.execute(
                select(ServiceAccount)
                .options(selectinload(ServiceAccount.applications))
                .where(ServiceAccount.client_id == client_id_from_token)
            )
            sa = result.scalar_one_or_none()
            if not sa:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Service account non trouvé"
                )
            app = sa.applications[0] if sa.applications else None
            if not app:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Aucune application associée à ce service account"
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Token M2M invalide pour regenerate-secret: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token M2M invalide ou expiré"
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token M2M (Authorization: Bearer) requis pour régénérer le secret. "
                   "L'authentification par IP+header est supprimée."
        )

    new_secret = ServiceAccount.generate_client_secret()
    sa.client_secret_hash = get_password_hash(new_secret)

    shared_result = await db.execute(
        select(Application).where(Application.is_shared_service == True)
    )
    shared_apps = shared_result.scalars().all()
    synced_slugs = []
    for shared_app in shared_apps:
        if shared_app not in sa.applications:
            sa.applications.append(shared_app)
            synced_slugs.append(shared_app.slug)

    await db.commit()

    all_app_slugs = [a.slug for a in sa.applications]
    save_service_account_credentials(
        name=app.slug,
        client_id=sa.client_id,
        client_secret=new_secret,
        app_slugs=all_app_slugs
    )

    host = settings.get_hub_host()
    auth_method = "token M2M" if token else "IP whitelist (déprécié)"
    if synced_slugs:
        logger.info(f"Secret régénéré pour '{app.slug}' ({auth_method}) - Apps partagées resync: {synced_slugs}")
    else:
        logger.info(f"Secret régénéré pour '{app.slug}' ({auth_method})")

    return AppCredentialsResponse(
        app_slug=app.slug,
        app_name=app.name,
        client_id=sa.client_id,
        client_secret=new_secret,
        hub_host=host,
        hub_api_port=8000,
        message="NOUVEAU SECRET GÉNÉRÉ ! L'ancien secret est maintenant invalide. Stockez ce secret en mémoire, il ne sera plus affiché."
    )



@router.put("/me/shared")
async def update_app_shared(
    request: Request,
    is_shared: bool,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(optional_oauth2_scheme),
):
    """
    Met à jour le flag is_shared_service d'une application.

    Authentification (par priorité) :
    1. Token M2M valide (recommandé)
    2. IP whitelist + X-Slug (déprécié)

    Si is_shared=True, l'application sera automatiquement accessible
    par tous les nouveaux service accounts créés.
    """
    client_ip = request.client.host if request.client else None
    app_slug = request.headers.get("X-Slug")

    if token:
        try:
            payload = await verify_token_not_revoked(token, db)
            if payload.get("type") != "service_account":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Seuls les tokens M2M sont acceptés"
                )
            client_id_from_token = payload.get("sub")
            result = await db.execute(
                select(ServiceAccount)
                .options(selectinload(ServiceAccount.applications))
                .where(ServiceAccount.client_id == client_id_from_token)
            )
            sa = result.scalar_one_or_none()
            if not sa or not sa.applications:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Service account ou application non trouvé"
                )
            from app.m2m_authorization import authorize_service_account_slug
            app_slug = await authorize_service_account_slug(
                request=request,
                service_account=sa,
                requested_slug=app_slug,
                db=db,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Token M2M invalide pour /me/shared: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token M2M invalide ou expiré"
            )
    else:
        if not is_docker_network_ip(client_ip):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Accès autorisé uniquement depuis le réseau Docker ou avec un token M2M (IP: {client_ip})"
            )

        if not app_slug:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Header X-Slug requis"
            )

        logger.warning(
            "⏳ DEPRECATION: /me/shared appelé sans token M2M (IP=%s, X-Slug=%s). "
            "Cette application doit migrer vers l'authentification par token M2M.",
            client_ip, app_slug
        )

    if not app_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header X-Slug requis"
        )

    result = await db.execute(
        select(Application).where(Application.slug == app_slug)
    )
    app = result.scalar_one_or_none()

    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_slug}' non trouvée"
        )

    old_shared = app.is_shared_service
    app.is_shared_service = is_shared

    auto_approved = False
    if is_shared and not old_shared and app.approval_status == ApprovalStatus.pending:
        app.approval_status = ApprovalStatus.approved
        app.approved_at = utcnow()
        app.is_active = True
        if app.description:
            app.description = app.description.replace(" (en attente d'approbation)", "")

        sa_result_inner = await db.execute(
            select(ServiceAccount)
            .join(service_account_app_access)
            .where(service_account_app_access.c.app_id == app.id)
        )
        for sa_inner in sa_result_inner.scalars().all():
            sa_inner.is_active = True
            if sa_inner.description:
                sa_inner.description = sa_inner.description.replace(" (en attente d'approbation)", "")

        auto_approved = True
        logger.info(f"Application '{app_slug}' auto-approuvée car devenue service partagé (M2M)")

    db.add(app)
    await db.commit()

    shared_added_count = 0
    if is_shared and app.approval_status == ApprovalStatus.approved:
        shared_added_count = await add_shared_service_to_all_service_accounts(db, app)
        if shared_added_count > 0:
            await db.commit()

    auth_method = "token M2M" if token else "IP whitelist (déprécié)"
    logger.info(f"Application '{app_slug}' is_shared_service={is_shared} ({auth_method})")

    message = f"Application '{app_slug}' mise à jour"
    if auto_approved:
        message += " et auto-approuvée (service partagé M2M)"
    if shared_added_count > 0:
        message += f". Ajoutée à {shared_added_count} service account(s)."

    notify_event("apps_updated", {
        "action": "updated" if not auto_approved else "auto_approved",
        "app_slug": app_slug,
        "is_shared_service": is_shared,
        "auto_approved": auto_approved
    })
    if auto_approved:
        notify_stats_update()

    return {
        "message": message,
        "slug": app_slug,
        "is_shared_service": is_shared,
        "auto_approved": auto_approved,
        "shared_added_to_sa_count": shared_added_count
    }


# ===== Filtrage des endpoints techniques =====

# Patterns d'endpoints techniques à exclure automatiquement
_FILTERED_PATH_PATTERNS = [
    r"^/health$",
    r"^/healthz$",
    r"^/ready$",
    r"^/readyz$",
    r"^/live$",
    r"^/livez$",
    r"^/ping$",
    r"^/status$",
    r"^/metrics$",
    r"^/openapi\.json$",
    r"^/docs$",
    r"^/docs/",
    r"^/redoc$",
    r"^/redoc/",
    r"^/swagger",
    r"^/api/health$",
    r"^/api/ping$",
    r"^/api/status$",
    r"^/api/docs",
    r"^/api/openapi\.json$",
    r"^/api/redoc",
    r".*/_debug",
    r".*/__test",
]

# Tags OpenAPI à exclure
_FILTERED_TAGS = {"health", "healthcheck", "debug", "internal", "test", "monitoring", "default"}


def filter_openapi_spec(spec: dict) -> tuple:
    """
    Filtre une spec OpenAPI pour ne garder que les endpoints métier.
    
    Exclut :
    - Les paths correspondant aux patterns techniques (health, docs, openapi, etc.)
    - Les endpoints tagués avec des tags techniques (health, debug, internal, etc.)
    
    Returns:
        (filtered_spec, total_endpoints, filtered_count)
    """
    if not spec or "paths" not in spec:
        return spec, 0, 0

    filtered_paths = {}
    total_endpoints = 0
    filtered_count = 0

    for path, methods in spec.get("paths", {}).items():
        # Vérifier si le path match un pattern technique
        is_technical_path = any(
            re.match(pattern, path, re.IGNORECASE)
            for pattern in _FILTERED_PATH_PATTERNS
        )

        if is_technical_path:
            # Compter les endpoints dans ce path
            for method in methods:
                if method.lower() in ("get", "post", "put", "patch", "delete", "head", "options"):
                    total_endpoints += 1
                    filtered_count += 1
            continue

        # Filtrer par tag au niveau de chaque méthode
        filtered_methods = {}
        for method, details in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete", "head", "options"):
                filtered_methods[method] = details  # Conserver parameters, etc.
                continue

            total_endpoints += 1
            tags = {t.lower() for t in (details.get("tags") or [])}
            if tags & _FILTERED_TAGS:
                filtered_count += 1
                continue

            filtered_methods[method] = details

        if filtered_methods:
            filtered_paths[path] = filtered_methods

    filtered_spec = {**spec, "paths": filtered_paths}
    return filtered_spec, total_endpoints, filtered_count


# ===== Push spec OpenAPI =====

@router.post("/me/openapi")
async def push_openapi_spec(
    request: Request,
    body: AppOpenApiPush,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(optional_oauth2_scheme),
):
    """
    Permet à une application de pousser sa spécification OpenAPI au Hub.

    Authentification (par priorité) :
    1. Token M2M valide (recommandé)
    2. IP whitelist + X-Slug (déprécié)

    La spec est automatiquement filtrée pour ne conserver que les
    endpoints métier (les endpoints techniques comme /health, /docs,
    /openapi.json sont exclus).
    """
    client_ip = request.client.host if request.client else None
    app_slug = request.headers.get("X-Slug")
    app = None

    if token:
        try:
            payload = await verify_token_not_revoked(token, db)
            if payload.get("type") != "service_account":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Seuls les tokens M2M sont acceptés"
                )
            client_id_from_token = payload.get("sub")
            result = await db.execute(
                select(ServiceAccount)
                .options(selectinload(ServiceAccount.applications))
                .where(ServiceAccount.client_id == client_id_from_token)
            )
            sa = result.scalar_one_or_none()
            if not sa or not sa.applications:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Service account ou application non trouvé"
                )
            from app.m2m_authorization import authorize_service_account_slug
            app_slug = await authorize_service_account_slug(
                request=request,
                service_account=sa,
                requested_slug=app_slug,
                db=db,
            )
            # Ne pas présélectionner app ici : le bloc commun plus bas recharge
            # l'application correspondant au slug résolu/validé par le garde.
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Token M2M invalide pour /me/openapi: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token M2M invalide ou expiré"
            )
    else:
        if not is_docker_network_ip(client_ip):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Accès autorisé uniquement depuis le réseau Docker ou avec un token M2M (IP: {client_ip})"
            )

        if not app_slug:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Header X-Slug requis"
            )

        logger.warning(
            "⏳ DEPRECATION: /me/openapi appelé sans token M2M (IP=%s, X-Slug=%s). "
            "Cette application doit migrer vers l'authentification par token M2M.",
            client_ip, app_slug
        )

    if not app_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header X-Slug requis"
        )

    if app is None:
        result = await db.execute(
            select(Application).where(Application.slug == app_slug)
        )
        app = result.scalar_one_or_none()

    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_slug}' non trouvée"
        )

    filtered_spec, total_endpoints, filtered_count = filter_openapi_spec(body.openapi_spec)
    business_count = total_endpoints - filtered_count

    app.api_endpoints = filtered_spec
    app.api_endpoints_updated_at = utcnow()

    await db.commit()

    auth_method = "token M2M" if token else "IP whitelist (déprécié)"
    logger.info(
        f"Spec OpenAPI reçue pour '{app_slug}' ({auth_method}): "
        f"{business_count} endpoints métier, {filtered_count} techniques exclus"
    )

    await log_action(
        db=db,
        user_id=None,
        action="push_openapi",
        target=app_slug,
        ip_address=client_ip,
        user_agent=None,
        details=f"Spec OpenAPI poussée ({auth_method}): {business_count} endpoints métier, {filtered_count} exclus"
    )
    await db.commit()

    notify_event("apps_updated", {
        "action": "openapi_updated",
        "app_slug": app_slug,
        "endpoints_count": business_count,
    })

    return AppOpenApiResponse(
        message=f"Spécification OpenAPI enregistrée pour '{app_slug}'",
        app_slug=app_slug,
        endpoints_count=business_count,
        filtered_count=filtered_count,
        updated_at=app.api_endpoints_updated_at,
    )


# ===== Endpoints d'Approbation Admin =====
# NOTE: Ces routes DOIVENT être avant /{app_id} pour éviter les conflits de routage

@router.get("/pending", response_model=PendingAppsResponse)
async def list_pending_apps(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """
    🔒 Liste les applications en attente d'approbation (admin uniquement).
    
    Retourne toutes les applications avec approval_status='pending'.
    """
    result = await db.execute(
        select(Application)
        .where(Application.approval_status == ApprovalStatus.pending)
        .order_by(Application.created_at.desc())
    )
    pending_apps = result.scalars().all()
    
    return PendingAppsResponse(
        pending_apps=[AppResponse.model_validate(app) for app in pending_apps],
        total=len(pending_apps)
    )


@router.post("/{slug}/approve", response_model=AppApprovalResponse)
async def approve_or_reject_app(
    slug: str,
    approval: AppApprovalRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """
    🔒 Approuve ou rejette une application en attente (admin uniquement).
    
    - **approve=true** : Active l'application et son Service Account
      - Si is_shared_service=true, ajoute l'app à tous les SA existants
      - Ajoute les services partagés existants au SA de l'app
    - **approve=false** : Rejette l'application (reste inactive)
      - Nécessite une raison de rejet
    """
    from datetime import datetime, timezone
    
    # Récupérer l'application avec ses relations
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.service_accounts))
        .where(Application.slug == slug)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{slug}' non trouvée"
        )
    
    if app.approval_status != ApprovalStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"L'application '{slug}' n'est pas en attente d'approbation (statut: {app.approval_status.value})"
        )
    
    client_ip = request.client.host if request.client else None
    
    if approval.approve:
        # APPROBATION
        app.approval_status = ApprovalStatus.approved
        app.approved_by_id = admin.id
        app.approved_at = utcnow()
        app.is_active = True
        app.description = app.description.replace(" (en attente d'approbation)", "") if app.description else None
        
        # Activer le Service Account associé
        for sa in app.service_accounts:
            sa.is_active = True
            sa.description = sa.description.replace(" (en attente d'approbation)", "") if sa.description else None
            
            # Ajouter les services partagés existants au SA de cette app
            shared_result = await db.execute(
                select(Application)
                .where(Application.is_shared_service == True)
                .where(Application.approval_status == ApprovalStatus.approved)
                .where(Application.id != app.id)
            )
            shared_apps = shared_result.scalars().all()
            
            # Charger les applications du SA
            sa_result = await db.execute(
                select(ServiceAccount)
                .options(selectinload(ServiceAccount.applications))
                .where(ServiceAccount.id == sa.id)
            )
            sa_loaded = sa_result.scalar_one()
            
            for shared_app in shared_apps:
                if shared_app not in sa_loaded.applications:
                    sa_loaded.applications.append(shared_app)
                    logger.info(f"🔗 Service partagé '{shared_app.slug}' ajouté au SA de '{app.slug}'")
        
        # Si l'app est un service partagé, l'ajouter à tous les SA existants
        shared_added_count = 0
        if app.is_shared_service:
            shared_added_count = await add_shared_service_to_all_service_accounts(db, app)
        
        # Stocker les infos avant commit (évite le lazy loading après refresh)
        is_shared = app.is_shared_service
        sa_count = len(app.service_accounts)
        
        await db.commit()
        await db.refresh(app)
        
        # Log audit
        await log_action(
            db=db,
            user_id=admin.id,
            action="approve_app",
            target=slug,
            ip_address=client_ip,
            details=f"Application '{slug}' approuvée par {admin.username}. Shared: {is_shared}, SA activés: {sa_count}"
        )
        
        # Notifier SSE
        notify_event("apps_updated", {"action": "approved", "app_slug": slug, "approved_by": admin.username})
        notify_stats_update()
        
        # Notifier les utilisateurs ayant déjà des rôles sur cette app
        # (rôles assignés avant l'approbation → ils doivent voir l'app apparaître)
        affected_users = await db.execute(
            select(UserAppRole.user_id, User.username)
            .join(User, User.id == UserAppRole.user_id)
            .where(UserAppRole.app_id == app.id)
        )
        for uid, uname in affected_users.all():
            notify_event("user_permissions_changed", {
                "user_id": uid,
                "username": uname,
                "app_slug": slug,
                "action": "app_approved"
            })
        
        message = f"✅ Application '{slug}' approuvée et activée."
        if app.is_shared_service and shared_added_count > 0:
            message += f" Service partagé ajouté à {shared_added_count} SA existant(s)."
        
        logger.info(f"✅ Application '{slug}' approuvée par {admin.username}")
        
    else:
        # REJET
        if not approval.reason:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Une raison de rejet est obligatoire"
            )
        
        app.approval_status = ApprovalStatus.rejected
        app.approved_by_id = admin.id
        app.approved_at = utcnow()
        app.rejection_reason = approval.reason
        app.is_active = False
        
        await db.commit()
        await db.refresh(app)
        
        # Log audit
        await log_action(
            db=db,
            user_id=admin.id,
            action="reject_app",
            target=slug,
            ip_address=client_ip,
            details=f"Application '{slug}' rejetée par {admin.username}. Raison: {approval.reason}"
        )
        
        # Notifier SSE
        notify_event("apps_updated", {"action": "rejected", "app_slug": slug, "rejected_by": admin.username})
        
        message = f"❌ Application '{slug}' rejetée. Raison: {approval.reason}"
        logger.info(f"❌ Application '{slug}' rejetée par {admin.username}: {approval.reason}")
    
    return AppApprovalResponse(
        message=message,
        application=AppResponse.model_validate(app),
        approved_by=admin.username,
        approved_at=app.approved_at
    )


@router.delete("/{slug}/pending")
async def delete_pending_app(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """
    🔒 Supprime une application en attente ou rejetée (admin uniquement).
    
    Permet de nettoyer les applications non approuvées.
    Les applications approuvées ne peuvent pas être supprimées via cet endpoint.
    """
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.service_accounts))
        .where(Application.slug == slug)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{slug}' non trouvée"
        )
    
    if app.approval_status == ApprovalStatus.approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Impossible de supprimer une application approuvée via cet endpoint. Utilisez DELETE /api/apps/{slug}"
        )

    if getattr(app, "is_system_app", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'application système Hub ne peut pas être supprimée"
        )
    
    client_ip = request.client.host if request.client else None
    
    # Supprimer l'application et ses dépendances
    app_info = await delete_app_cascade(db, app, delete_credentials_file=True)
    await db.commit()
    
    # Log audit
    await log_action(
        db=db,
        user_id=admin.id,
        action="delete_pending_app",
        target=slug,
        ip_address=client_ip,
        details=f"Application en attente '{slug}' supprimée par {admin.username}"
    )
    
    notify_event("apps_updated", {"action": "deleted", "app_slug": slug})
    notify_stats_update()
    
    logger.info(f"🗑️ Application en attente '{slug}' supprimée par {admin.username}")
    
    return {"message": f"Application '{slug}' supprimée", "details": app_info}


# ===== Endpoints avec {app_id} =====

@router.get("/dependencies", response_model=DependenciesGraph)
async def get_app_dependencies(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Retourne le graphe complet des dépendances inter-apps (admin)."""
    rows = (await db.execute(select(AppDependency))).scalars().all()
    return DependenciesGraph(dependencies=[
        DependencyOut(
            source=r.source_slug,
            target=r.target_slug,
            note=r.note,
            declared_at=r.declared_at.isoformat() if r.declared_at else None,
            last_sync_at=r.last_sync_at.isoformat() if r.last_sync_at else None,
        )
        for r in rows
    ])


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

    if getattr(app, "is_system_app", False):
        # Pour les apps systeme (HUB), autoriser UNIQUEMENT la modification
        # du git_repo_url (utile pour l'update-check et le changelog).
        update_data = app_data.model_dump(exclude_unset=True)
        allowed_system_fields = {"git_repo_url"}
        system_updates = {k: v for k, v in update_data.items() if k in allowed_system_fields}
        if not system_updates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="L'application système Hub n'est pas modifiable via cette vue"
            )
        for field, value in system_updates.items():
            setattr(app, field, value)
        await db.commit()
        await db.refresh(app)
        notify_event("apps_updated", {"action": "updated", "app_slug": app.slug, "app_name": app.name})
        return app
    
    update_data = app_data.model_dump(exclude_unset=True)

    # Valider le groupe visuel si fourni (404 s'il n'existe pas)
    if update_data.get("group_id") is not None:
        grp = await db.execute(select(AppGroup).where(AppGroup.id == update_data["group_id"]))
        if not grp.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Groupe introuvable"
            )

    # Normaliser les URLs frontend en HTTP
    # (les apps gèrent elles-mêmes la redirection HTTPS)
    if "frontend_url" in update_data and update_data["frontend_url"]:
        update_data["frontend_url"] = normalize_frontend_url(update_data["frontend_url"])
    
    # Vérifier si is_shared_service change
    old_shared = app.is_shared_service
    new_shared = update_data.get("is_shared_service")
    shared_changed = new_shared is not None and old_shared != new_shared
    
    for field, value in update_data.items():
        setattr(app, field, value)
    
    await db.commit()
    await db.refresh(app)
    
    # Gérer les permissions M2M si is_shared_service a changé
    sa_updated_count = 0
    if shared_changed:
        if new_shared:
            # Passé à True → Ajouter aux permissions de tous les SA
            sa_updated_count = await add_shared_service_to_all_service_accounts(db, app)
            logger.info(f"🔗 Service partagé '{app.slug}' activé - ajouté à {sa_updated_count} SA")
        else:
            # Passé à False → Retirer des permissions des SA (sauf propriétaire)
            sa_updated_count = await remove_shared_service_from_all_service_accounts(db, app)
            logger.info(f"🔓 Service partagé '{app.slug}' désactivé - retiré de {sa_updated_count} SA")
        
        if sa_updated_count > 0:
            await db.commit()
    
    # Notifier les clients SSE de la modification
    notify_event("apps_updated", {"action": "updated", "app_slug": app.slug, "app_name": app.name})
    
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

    if getattr(app, "is_system_app", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'application système Hub ne peut pas être supprimée"
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
    
    # Notifier les clients SSE de la suppression
    notify_event("apps_updated", {"action": "deleted", "app_slug": app_slug, "app_name": app_name})
    notify_stats_update()
    
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
        loop = asyncio.get_running_loop()
        docker_info = await loop.run_in_executor(None, docker_scanner.get_docker_info)
        
        if not docker_info.get("available"):
            return DockerScanResponse(
                available=False,
                error=docker_info.get("error", "Docker non accessible")
            )
        
        containers = await loop.run_in_executor(None, lambda: docker_scanner.scan_containers(include_stopped=include_stopped))

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
                is_registered=getattr(container, 'is_registered', False),
                # Labels Hub spécifiques pour pré-remplir le formulaire
                hub_name=container.hub_name,
                hub_description=container.hub_description,
                hub_shared=container.hub_shared,
                # Auto-pairing
                container_type=container.container_type,
                base_slug=container.base_slug,
            ))
        
        # === Regroupement par base_slug ===
        from collections import defaultdict
        groups_map = defaultdict(lambda: {"api": None, "frontend": None, "ungrouped": [], "is_registered": False, "description": None, "is_shared": False, "suggested_name": None})
        
        for ci in discovered:
            base = ci.base_slug or ci.suggested_slug
            group = groups_map[base]
            
            # Agréger les informations
            if ci.is_registered:
                group["is_registered"] = True
            if ci.hub_description and not group["description"]:
                group["description"] = ci.hub_description
            if ci.hub_shared:
                group["is_shared"] = True
            
            # Placer le conteneur dans le bon slot
            if ci.container_type == 'api':
                if group["api"] is None:
                    group["api"] = ci
                else:
                    group["ungrouped"].append(ci)
            elif ci.container_type == 'frontend':
                if group["frontend"] is None:
                    group["frontend"] = ci
                else:
                    group["ungrouped"].append(ci)
            else:
                group["ungrouped"].append(ci)
            
            # Nom suggéré : priorité au hub_name API, puis au nom formaté du base_slug
            if not group["suggested_name"]:
                if ci.hub_name:
                    group["suggested_name"] = ci.hub_name
                else:
                    group["suggested_name"] = base.replace("-", " ").title()
        
        # Construire la liste de groupes
        groups = []
        for base, g in groups_map.items():
            # Déterminer le port API
            api_port = None
            if g["api"]:
                api_port = next((p.host_port for p in g["api"].ports if p.host_port), None)
            
            # Déterminer le port frontend
            frontend_port = None
            if g["frontend"]:
                frontend_port = next((p.host_port for p in g["frontend"].ports if p.host_port), None)
            
            # Vérifier si le base_slug est déjà pris
            final_base = base
            if final_base in existing_slugs:
                i = 2
                while f"{final_base}-{i}" in existing_slugs:
                    i += 1
                final_base = f"{final_base}-{i}"
            
            groups.append(DockerAppGroup(
                base_slug=final_base,
                suggested_name=g["suggested_name"] or final_base.replace("-", " ").title(),
                description=g["description"],
                is_shared=g["is_shared"],
                is_registered=g["is_registered"],
                api_container=g["api"],
                frontend_container=g["frontend"],
                api_port=api_port,
                frontend_port=frontend_port,
                ungrouped_containers=g["ungrouped"],
            ))
        
        # Trier : non-enregistrés d'abord, puis par nom
        groups.sort(key=lambda g: (g.is_registered, g.suggested_name.lower()))
        
        logger.info(f"Scan Docker: {len(discovered)} conteneurs, {len(groups)} groupe(s) par {current_user.username} (show_all={show_all})")
        
        return DockerScanResponse(
            available=True,
            docker_version=docker_info.get("version"),
            containers_running=docker_info.get("containers_running", 0),
            containers_total=docker_info.get("containers_total", 0),
            discovered=discovered,
            groups=groups,
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
            loop = asyncio.get_running_loop()
            docker_info = await loop.run_in_executor(None, docker_scanner.get_docker_info)
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


@router.get("/{slug}/docker-info", response_model=DockerInfoResponse)
async def get_app_docker_info(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Récupère les informations Docker détaillées d'une application.
    
    Recherche le conteneur Docker correspondant à l'application via :
    1. Le slug dans les variables d'environnement APP_SLUG
    2. Le slug dans les labels hub.slug
    3. Les ports correspondant aux URLs de l'application
    
    ⚠️ Admin uniquement - Utile pour les développeurs cherchant à intégrer l'API.
    
    Retourne :
    - Informations du conteneur (ID, image, status, created_at)
    - Ports exposés et mappings
    - Labels hub.* configurés
    - Variables d'environnement APP_*
    - Réseaux Docker et adresses IP
    - Infos pour développeurs (comment joindre l'API)
    """
    # Récupérer l'application
    result = await db.execute(select(Application).where(Application.slug == slug))
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    # Préparer la réponse de base
    response = DockerInfoResponse(
        app_slug=app.slug,
        app_name=app.name,
        app_api_url=app.api_url,
        app_frontend_url=app.frontend_url,
        container_found=False
    )
    
    # Vérifier si Docker est disponible
    if not docker_scanner.is_available:
        response.docker_available = False
        response.developer_info = {
            "warning": "Scan Docker désactivé sur ce serveur",
            "api_url": app.api_url,
            "tip": "Comportement normal en production durcie : le socket Docker n'est pas monté dans le Hub. Utilisez l'URL API directement pour joindre ce service."
        }
        return response
    
    # Extraire le port de l'URL API pour identifier le conteneur
    import re
    from urllib.parse import urlparse
    
    target_ports = set()
    for url in [app.api_url, app.frontend_url]:
        if url:
            try:
                parsed = urlparse(url)
                if parsed.port:
                    target_ports.add(parsed.port)
            except Exception:
                pass
    
    # Scanner tous les conteneurs (y compris arrêtés)
    try:
        client = docker_scanner.client
        if not client:
            response.docker_available = False
            response.developer_info = {
                "warning": "Client Docker non initialisé",
                "api_url": app.api_url,
                "tip": "Comportement normal en production durcie : le socket Docker n'est pas monté dans le Hub. Utilisez l'URL API directement pour joindre ce service."
            }
            return response
        
        loop = asyncio.get_running_loop()
        containers = await loop.run_in_executor(None, lambda: client.containers.list(all=True))
        matching_container = None
        
        for container in containers:
            # Méthode 1: Chercher par APP_SLUG dans les variables d'environnement
            env_vars = {}
            try:
                env_list = container.attrs.get('Config', {}).get('Env', [])
                for env in env_list:
                    if '=' in env:
                        key, value = env.split('=', 1)
                        if key.startswith('APP_'):
                            env_vars[key] = value
            except Exception:
                pass
            
            if env_vars.get('APP_SLUG') == slug:
                matching_container = container
                break
            
            # Méthode 2: Chercher par label hub.slug
            labels = container.labels or {}
            if labels.get('hub.slug') == slug:
                matching_container = container
                break
            
            # Méthode 3: Chercher par ports correspondants
            container_ports = set()
            try:
                for port_str, bindings in (container.ports or {}).items():
                    if bindings:
                        for binding in bindings:
                            host_port = binding.get('HostPort')
                            if host_port:
                                container_ports.add(int(host_port))
            except Exception:
                pass
            
            if target_ports and container_ports and target_ports & container_ports:
                matching_container = container
                break
        
        if not matching_container:
            response.developer_info = {
                "warning": "Conteneur Docker non trouvé pour cette application",
                "api_url": app.api_url,
                "tip": f"Le conteneur pourrait être arrêté ou ne pas avoir APP_SLUG={slug}",
                "searched_ports": list(target_ports)
            }
            return response
        
        # Conteneur trouvé - extraire toutes les infos
        response.container_found = True
        response.container_id = matching_container.short_id
        response.container_name = matching_container.name
        response.container_status = matching_container.status
        
        # Image
        try:
            response.container_image = matching_container.image.tags[0] if matching_container.image.tags else "unknown"
        except Exception:
            response.container_image = matching_container.attrs.get('Config', {}).get('Image', 'unknown')
        
        # Dates
        attrs = matching_container.attrs
        response.container_created = attrs.get('Created', '')[:19]  # Format ISO sans timezone
        state = attrs.get('State', {})
        if state.get('StartedAt'):
            response.container_started_at = state['StartedAt'][:19]
        
        # Ports
        ports = []
        try:
            for port_str, bindings in (matching_container.ports or {}).items():
                parts = port_str.split('/')
                container_port = int(parts[0])
                protocol = parts[1] if len(parts) > 1 else "tcp"
                host_port = None
                if bindings:
                    for binding in bindings:
                        if binding.get('HostPort'):
                            host_port = int(binding['HostPort'])
                            break
                ports.append(DockerPortInfo(
                    container_port=container_port,
                    host_port=host_port,
                    protocol=protocol
                ))
        except Exception as e:
            logger.warning(f"Erreur extraction ports: {e}")
        response.ports = ports
        
        # Labels hub.*
        labels = matching_container.labels or {}
        hub_labels = {k: v for k, v in labels.items() if k.startswith('hub.')}
        response.hub_labels = hub_labels
        
        # Variables APP_*
        env_vars = {}
        try:
            env_list = attrs.get('Config', {}).get('Env', [])
            for env in env_list:
                if '=' in env and env.startswith('APP_'):
                    key, value = env.split('=', 1)
                    env_vars[key] = value
        except Exception:
            pass
        response.app_env_vars = env_vars
        
        # Réseaux
        networks = []
        ip_addresses = {}
        try:
            network_settings = attrs.get('NetworkSettings', {}).get('Networks', {})
            for net_name, net_config in network_settings.items():
                networks.append(net_name)
                if net_config.get('IPAddress'):
                    ip_addresses[net_name] = net_config['IPAddress']
        except Exception:
            pass
        response.networks = networks
        response.ip_addresses = ip_addresses
        
        # Informations pour développeurs
        host_ports = [p.host_port for p in ports if p.host_port]
        response.developer_info = {
            "api_url": app.api_url,
            "status": "running" if matching_container.status == "running" else "stopped",
            "how_to_connect": f"Utilisez {app.api_url} pour joindre l'API",
            "internal_ips": ip_addresses,
            "exposed_ports": host_ports,
            "container_command": f"docker exec -it {matching_container.name} /bin/bash",
            "logs_command": f"docker logs {matching_container.name} --tail 100 -f",
            "tip": "Utilisez le header X-Slug: " + slug + " pour vous identifier auprès du Hub"
        }
        
        if app.frontend_url:
            response.developer_info["frontend_url"] = app.frontend_url
        
        # Récupérer les stats CPU/RAM si le conteneur est running
        if matching_container.status == 'running':
            try:
                from datetime import datetime, timezone
                raw_stats = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, lambda c=matching_container: c.stats(stream=False)
                    ),
                    timeout=5.0
                )
                
                # Calcul CPU %
                cpu_stats = raw_stats.get('cpu_stats', {})
                precpu_stats = raw_stats.get('precpu_stats', {})
                cpu_usage = cpu_stats.get('cpu_usage', {})
                precpu_usage = precpu_stats.get('cpu_usage', {})
                
                cpu_delta = cpu_usage.get('total_usage', 0) - precpu_usage.get('total_usage', 0)
                system_delta = cpu_stats.get('system_cpu_usage', 0) - precpu_stats.get('system_cpu_usage', 0)
                num_cpus = cpu_stats.get('online_cpus', 1)
                cpu_percent = 0.0
                if system_delta > 0:
                    cpu_percent = round((cpu_delta / system_delta) * num_cpus * 100, 1)
                
                # RAM
                mem_stats = raw_stats.get('memory_stats', {})
                mem_usage = mem_stats.get('usage', 0) - mem_stats.get('stats', {}).get('cache', 0)
                mem_limit = mem_stats.get('limit', 0)
                
                # Uptime
                uptime_seconds = None
                try:
                    started_at = matching_container.attrs.get('State', {}).get('StartedAt', '')
                    if started_at:
                        clean_ts = started_at.split('.')[0]
                        if clean_ts.endswith('Z'):
                            clean_ts = clean_ts[:-1]
                        start_time = datetime.fromisoformat(clean_ts).replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        uptime_seconds = int((now - start_time).total_seconds())
                except Exception:
                    pass
                
                response.container_stats = {
                    "cpu_percent": cpu_percent,
                    "memory_usage_mb": round(mem_usage / (1024 * 1024), 1),
                    "memory_limit_mb": round(mem_limit / (1024 * 1024), 1),
                    "uptime_seconds": uptime_seconds
                }
            except asyncio.TimeoutError:
                logger.debug(f"Timeout stats pour {matching_container.name}")
            except Exception as e:
                logger.debug(f"Stats indisponibles pour {matching_container.name}: {e}")
        
        logger.info(f"🔍 Docker info pour '{slug}' consulté par {current_user.username}")
        return response
        
    except Exception as e:
        logger.error(f"Erreur récupération info Docker pour {slug}: {e}")
        response.developer_info = {
            "error": str(e),
            "api_url": app.api_url
        }
        return response


@router.put("/{app_id}/shared")
async def set_app_shared(
    app_id: int,
    is_shared: bool,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Marque une application comme service partagé (admin uniquement).
    
    Si is_shared=True :
    - Cette application sera automatiquement accessible par tous les nouveaux service accounts créés
    - L'application est ajoutée aux permissions de tous les service accounts EXISTANTS
    
    Si is_shared=False :
    - L'application est retirée des permissions de tous les service accounts (sauf son SA propriétaire)
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
    
    # Vérifier si le statut change vraiment
    old_shared = app.is_shared_service
    if old_shared == is_shared:
        return {
            "message": f"Application '{app.slug}' déjà en mode is_shared_service={is_shared}",
            "app_id": app.id,
            "slug": app.slug,
            "is_shared_service": is_shared,
            "service_accounts_updated": 0
        }
    
    app.is_shared_service = is_shared
    db.add(app)
    await db.commit()
    await db.refresh(app)
    
    # Gérer les permissions M2M automatiquement
    sa_updated_count = 0
    if is_shared:
        # Passé à True → Ajouter aux permissions de tous les SA
        sa_updated_count = await add_shared_service_to_all_service_accounts(db, app)
    else:
        # Passé à False → Retirer des permissions des SA (sauf propriétaire)
        sa_updated_count = await remove_shared_service_from_all_service_accounts(db, app)
    
    if sa_updated_count > 0:
        await db.commit()
    
    action = "ajoutée à" if is_shared else "retirée de"
    logger.info(f"🔧 Application '{app.slug}' is_shared_service={is_shared} par {current_user.username} - {action} {sa_updated_count} SA")
    
    return {
        "message": f"Application '{app.slug}' mise à jour - {action} {sa_updated_count} Service Account(s)",
        "app_id": app.id,
        "slug": app.slug,
        "is_shared_service": is_shared,
        "service_accounts_updated": sa_updated_count
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
        .where(Application.is_unmanaged == False)
    )
    apps = result.scalars().all()
    
    if not apps:
        return {
            "message": "Aucune application à synchroniser",
            "activated": [],
            "deactivated": [],
            "unchanged": []
        }
    
    # Scanner tous les conteneurs Docker (y compris arrêtés) — appel bloquant → executor
    loop = asyncio.get_running_loop()
    containers = await loop.run_in_executor(None, lambda: docker_scanner.scan_containers(include_stopped=True))
    
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
        "docker_info": await loop.run_in_executor(None, docker_scanner.get_docker_info)
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
    result = await db.execute(
        select(Application)
        .where(Application.is_system_app == False)
        .where(Application.is_unmanaged == False)
    )
    apps = result.scalars().all()
    
    if not apps:
        return {"message": "Aucune application", "changes": 0}
    
    # Scanner tous les conteneurs Docker — appel bloquant → executor
    loop = asyncio.get_running_loop()
    containers = await loop.run_in_executor(None, lambda: docker_scanner.scan_containers(include_stopped=True))
    
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


# ===== Favoris =====

@router.post("/{app_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
async def add_favorite(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Ajoute une application aux favoris de l'utilisateur courant"""
    result = await db.execute(select(Application).where(Application.id == app_id))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application non trouvée")

    existing = await db.execute(
        select(UserFavorite).where(
            UserFavorite.user_id == current_user.id,
            UserFavorite.app_id == app_id
        )
    )
    if existing.scalar_one_or_none():
        return  # Déjà en favori

    db.add(UserFavorite(user_id=current_user.id, app_id=app_id))
    await db.commit()


@router.delete("/{app_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retire une application des favoris de l'utilisateur courant"""
    result = await db.execute(
        select(UserFavorite).where(
            UserFavorite.user_id == current_user.id,
            UserFavorite.app_id == app_id
        )
    )
    fav = result.scalar_one_or_none()
    if not fav:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Favori non trouvé")

    await db.delete(fav)
    await db.commit()


@router.post("/dependencies", response_model=DependenciesPushResponse)
async def push_app_dependencies(
    body: DependenciesPush,
    request: Request,
    db: AsyncSession = Depends(get_db),
    entity: Union[User, ServiceAccount] = Depends(get_current_entity),
):
    """Une app pousse son graphe de dépendances (contenu de son depends.yaml).

    Remplace toutes les lignes ``source_slug = <caller>`` par les nouvelles.
    Préserve ``last_sync_at`` pour les paires (source, target) qui existaient déjà.
    """
    if not isinstance(entity, ServiceAccount):
        raise HTTPException(status_code=403, detail="Cet endpoint est réservé aux service accounts (M2M)")

    from app.m2m_authorization import authorize_service_account_slug
    source_slug = await authorize_service_account_slug(
        request=request,
        service_account=entity,
        requested_slug=request.headers.get("x-slug"),
        db=db,
    )
    if not source_slug:
        raise HTTPException(status_code=400, detail="X-Slug header requis (ou app associée au SA)")

    # Capturer les last_sync_at existants pour préservation
    existing = (await db.execute(
        select(AppDependency).where(AppDependency.source_slug == source_slug)
    )).scalars().all()
    sync_map = {row.target_slug: row.last_sync_at for row in existing}

    # Supprimer les anciennes lignes
    for row in existing:
        await db.delete(row)
    await db.flush()

    # Insérer les nouvelles (préserver last_sync_at si la paire existait)
    now = utcnow()
    for entry in body.depends_on:
        if entry.slug == source_slug:
            continue  # pas d'auto-dépendance
        dep = AppDependency(
            source_slug=source_slug,
            target_slug=entry.slug,
            note=entry.note,
            declared_at=now,
            last_sync_at=sync_map.get(entry.slug),  # None si nouvelle
        )
        db.add(dep)

    await db.commit()
    return DependenciesPushResponse(stored=len(body.depends_on), source_slug=source_slug)


@router.post("/sync-report")
async def report_app_sync(
    body: SyncReport,
    request: Request,
    db: AsyncSession = Depends(get_db),
    entity: Union[User, ServiceAccount] = Depends(get_current_entity),
):
    """Une app signale qu'elle vient de synchro avec ``target``.

    Met à jour ``last_sync_at``. Si la paire (source, target) n'existe pas,
    crée une ligne orpheline (cas où le depends.yaml n'a pas encore été reçu).
    Best-effort : ne lève jamais côté appelant (cf. hub_client.report_sync).
    """
    if not isinstance(entity, ServiceAccount):
        raise HTTPException(status_code=403, detail="Cet endpoint est réservé aux service accounts (M2M)")

    from app.m2m_authorization import authorize_service_account_slug
    source_slug = await authorize_service_account_slug(
        request=request,
        service_account=entity,
        requested_slug=request.headers.get("x-slug"),
        db=db,
    )
    if not source_slug:
        raise HTTPException(status_code=400, detail="X-Slug header requis")

    existing = (await db.execute(
        select(AppDependency).where(
            AppDependency.source_slug == source_slug,
            AppDependency.target_slug == body.target,
        )
    )).scalar_one_or_none()

    now = utcnow()
    if existing:
        existing.last_sync_at = now
    else:
        db.add(AppDependency(
            source_slug=source_slug,
            target_slug=body.target,
            last_sync_at=now,
        ))
    await db.commit()
    return {"ok": True, "source": source_slug, "target": body.target, "last_sync_at": now.isoformat()}
