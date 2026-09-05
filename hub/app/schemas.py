from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

from app.models import highest_role


class GrantType(str, Enum):
    """Types de grant OAuth2 supportés"""
    password = "password"
    refresh_token = "refresh_token"
    client_credentials = "client_credentials"


class ApprovalStatusEnum(str, Enum):
    """Statut d'approbation des applications auto-enregistrées"""
    pending = "pending"      # En attente de validation admin
    approved = "approved"    # Approuvée par un admin
    rejected = "rejected"    # Rejetée par un admin


# ===== User Schemas =====
class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[EmailStr] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    is_admin: bool = False
    must_change_password: bool = False
    can_change_password: bool = True  # Autoriser le changement de mdp (False pour comptes partagés)


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=8)
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
    must_change_password: Optional[bool] = None
    can_change_password: Optional[bool] = None
    profile_ids: Optional[List[int]] = None  # Liste des IDs de profils à assigner
    prenom: Optional[str] = Field(None, max_length=100)
    nom: Optional[str] = Field(None, max_length=100)


class UserRenameRequest(BaseModel):
    """Renommage exceptionnel du login (opération rare, tracée)."""
    username: str = Field(..., min_length=3, max_length=50)


class PasswordChangeRequest(BaseModel):
    """Requête de changement de mot de passe par l'utilisateur"""
    current_password: str = Field(..., description="Mot de passe actuel")
    new_password: str = Field(..., min_length=8, description="Nouveau mot de passe (min 8 caractères)")


# ===== TOTP / Récupération de mot de passe =====
class TotpSetupResponse(BaseModel):
    """Réponse d'initialisation TOTP (secret en attente de confirmation)."""
    otpauth_uri: str
    secret: str
    qr_data_uri: str


class TotpConfirmRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=8, description="Code TOTP à 6 chiffres")


class TotpDisableRequest(BaseModel):
    current_password: str = Field(..., description="Mot de passe actuel")


class RecoverVerifyRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    totp_code: str = Field(..., min_length=6, max_length=8)


class RecoverVerifyResponse(BaseModel):
    reset_token: str


class RecoverResetRequest(BaseModel):
    reset_token: str
    new_password: str = Field(..., min_length=8)


# ===== Profile Schemas =====
class ProfileAppRoleRequest(BaseModel):
    """App + rôle pour un profil"""
    app_id: int
    role: str = "user"


class ProfileCreate(BaseModel):
    """Création d'un profil"""
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    color: str = Field(default="#6c757d", max_length=7)
    app_roles: List[ProfileAppRoleRequest] = []


class ProfileUpdate(BaseModel):
    """Mise à jour d'un profil"""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = Field(None, max_length=7)
    app_roles: Optional[List[ProfileAppRoleRequest]] = None


class ProfileAppRoleResponse(BaseModel):
    """App + rôle d'un profil (réponse)"""
    app_id: int
    app_name: str
    app_slug: str
    role: str

    model_config = ConfigDict(from_attributes=True)


class ProfileBriefResponse(BaseModel):
    """Résumé d'un profil (pour affichage inline dans la liste users)"""
    id: int
    name: str
    color: str

    model_config = ConfigDict(from_attributes=True)


class ProfileResponse(BaseModel):
    """Réponse complète d'un profil"""
    id: int
    name: str
    description: Optional[str] = None
    color: str = "#6c757d"
    app_roles: List[ProfileAppRoleResponse] = []
    user_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProfileApplyRequest(BaseModel):
    """Appliquer un profil à un ou plusieurs utilisateurs (additif)"""
    user_ids: List[int] = Field(..., min_length=1)
    sync_roles: bool = Field(default=True, description="Synchroniser les user_app_roles avec ceux du profil")


class ProfileRemoveUsersRequest(BaseModel):
    """Retirer un profil d'un ou plusieurs utilisateurs"""
    user_ids: List[int] = Field(..., min_length=1)
    sync_roles: bool = Field(default=True, description="Recalculer les rôles après retrait")


# ===== User App Role Schema =====
class UserAppRoleResponse(BaseModel):
    app_id: int
    app_name: str
    app_slug: str
    role: str
    source: str = "manual"
    profile_role: Optional[str] = None  # Rôle attendu par les profils (None si non couverte)
    overridden: bool = False            # True si source = 'override:<pid>'

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm_model(cls, uar, profile_roles_by_app: Optional[Dict[int, str]] = None):
        """Convertit un UserAppRole ORM en réponse"""
        return cls(
            app_id=uar.app_id,
            app_name=uar.application.name,
            app_slug=uar.application.slug,
            role=uar.role,
            source=uar.source or "manual",
            profile_role=(profile_roles_by_app or {}).get(uar.app_id),
            overridden=(uar.source or "").startswith("override:"),
        )


class UserResponse(UserBase):
    id: int
    uuid: Optional[str] = None
    prenom: Optional[str] = None
    nom: Optional[str] = None
    is_active: bool
    is_admin: bool
    must_change_password: bool = False
    can_change_password: bool = True
    totp_enabled: bool = False
    created_at: datetime
    app_roles: List[UserAppRoleResponse] = []
    profiles: List[ProfileBriefResponse] = []
    has_role_overrides: bool = False
    
    model_config = ConfigDict(from_attributes=True)
    
    @classmethod
    def from_orm_model(cls, user) -> "UserResponse":
        """Crée une réponse utilisateur avec les rôles par application"""
        # Rôles attendus par les profils (rôle max par app, tous profils confondus)
        profile_roles_by_app: Dict[int, str] = {}
        try:
            for profile in user.profiles:
                for par in profile.app_roles:
                    existing = profile_roles_by_app.get(par.app_id)
                    if existing is None:
                        profile_roles_by_app[par.app_id] = par.role
                    else:
                        profile_roles_by_app[par.app_id] = highest_role([existing, par.role])
        except Exception:
            profile_roles_by_app = {}  # profiles/app_roles non chargés

        app_roles = [
            UserAppRoleResponse.from_orm_model(uar, profile_roles_by_app)
            for uar in user.app_roles
        ]
        # Profils (multi-profils)
        profiles_brief = []
        try:
            for profile in user.profiles:
                profiles_brief.append(ProfileBriefResponse(
                    id=profile.id,
                    name=profile.name,
                    color=profile.color or "#6c757d"
                ))
        except Exception:
            pass  # profiles non chargés
        return cls(
            id=user.id,
            uuid=getattr(user, "uuid", None),
            prenom=getattr(user, "prenom", None),
            nom=getattr(user, "nom", None),
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_admin=user.is_admin,
            must_change_password=user.must_change_password or False,
            can_change_password=user.can_change_password if user.can_change_password is not None else True,
            totp_enabled=user.totp_enabled or False,
            created_at=user.created_at,
            app_roles=app_roles,
            profiles=profiles_brief,
            has_role_overrides=any(
                (uar.source or "").startswith("override:") for uar in user.app_roles
            )
        )


class UserBrief(BaseModel):
    id: int
    username: str
    is_active: bool
    
    model_config = ConfigDict(from_attributes=True)


class PaginatedUsersResponse(BaseModel):
    """Réponse paginée des utilisateurs"""
    users: List[UserResponse] = []
    total: int = 0
    page: int = 1
    per_page: int = 50
    pages: int = 1


class UserBriefWithRole(BaseModel):
    """Utilisateur avec son rôle pour une application"""
    id: int
    username: str
    is_active: bool
    role: str
    
    model_config = ConfigDict(from_attributes=True)


# ===== Application Schemas =====
class AppBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z0-9-]+$')
    description: Optional[str] = None
    icon: str = "bi-app"
    color: str = "#0d6efd"
    api_url: str
    frontend_url: Optional[str] = None
    callback_url: Optional[str] = None
    requires_auth: bool = True

    is_shared_service: bool = Field(default=False, description="Si True, cette app est accessible par tous les service accounts")


class AppCreateSimplified(BaseModel):
    """Schéma simplifié pour créer une application (ports au lieu d'URLs)"""
    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z0-9-]+$')
    category: Optional[str] = Field(None, max_length=100)
    group_id: Optional[int] = None  # Groupe visuel (null = hors groupe)
    description: Optional[str] = None
    icon: str = "bi-app"
    color: str = "#0d6efd"
    api_port: int = Field(..., ge=1, le=65535, description="Port de l'API backend")
    frontend_port: Optional[int] = Field(None, ge=1, le=65535, description="Port du frontend")
    frontend_https_port: Optional[int] = Field(None, ge=1, le=65535, description="Port HTTPS du frontend (si HTTPS activé)")
    custom_host: Optional[str] = Field(None, description="Host personnalisé si différent du Hub")
    requires_auth: bool = True
    create_service_account: bool = Field(False, description="Créer un service account M2M pour cette application")
    is_shared_service: bool = Field(False, description="Si True, cette app est accessible par tous les service accounts (M2M)")
    is_unmanaged: bool = Field(False, description="App non gérée (launcher-only) : présente/lancable mais exclue du health-check, backup et sync Docker")
    git_repo_url: Optional[str] = Field(None, max_length=512, description="URL du dépôt Git (GitHub) pour le changelog et la détection de mises à jour")


class AppCreate(AppBase):
    pass


class AppUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    category: Optional[str] = Field(None, max_length=100)
    group_id: Optional[int] = None  # Groupe visuel (null = hors groupe)
    api_url: Optional[str] = None
    frontend_url: Optional[str] = None
    callback_url: Optional[str] = None
    is_active: Optional[bool] = None
    requires_auth: Optional[bool] = None
    is_shared_service: Optional[bool] = None
    m2m_enforcement_mode: Optional[str] = None  # 'compat' ou 'enforced'
    is_unmanaged: Optional[bool] = None  # True = launcher-only (non gérée)
    git_repo_url: Optional[str] = Field(None, max_length=512, description="URL du dépôt Git (GitHub) pour le changelog et la détection de mises à jour")


class AppGroupBrief(BaseModel):
    """Info minimale d'un groupe embarquée avec chaque app du dashboard"""
    id: int
    name: str
    icon: str = "bi-collection"
    color: str = "#0d6efd"


class AppGroupCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    icon: str = "bi-collection"
    color: str = "#0d6efd"


class AppGroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    icon: Optional[str] = None
    color: Optional[str] = None


class AppGroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    icon: str = "bi-collection"
    color: str = "#0d6efd"
    created_at: datetime
    app_count: int = 0

    model_config = ConfigDict(from_attributes=True)




class AppResponse(AppBase):
    id: int
    is_active: bool
    is_shared_service: bool
    is_system_app: bool = False
    is_remote: bool = False  # True si hébergée sur un serveur distant
    m2m_enforcement_mode: str = "compat"  # 'compat' ou 'enforced'
    registered_from_ip: Optional[str] = None  # IP source lors de l'auto-enregistrement
    frontend_https_port: Optional[int] = None  # Port HTTPS du frontend (si HTTPS activé)
    approval_status: ApprovalStatusEnum = ApprovalStatusEnum.approved
    created_at: datetime
    is_workshop: bool = False
    is_unmanaged: bool = False  # True si app non gérée (launcher-only)
    git_repo_url: Optional[str] = None
    git_sync: Optional[str] = None  # PATCH update_app uniquement : "ok" | "warning: …" | None (sync remote best-effort)
    
    @field_validator('is_remote', mode='before')
    @classmethod
    def convert_none_to_false(cls, v):
        """Convertit None en False pour is_remote (anciennes apps en BDD)"""
        return v if v is not None else False
    
    model_config = ConfigDict(from_attributes=True)


class AppResponseWithMyRole(AppResponse):
    """Application avec le rôle de l'utilisateur courant (pour le dashboard)"""
    my_role: Optional[str] = None  # Rôle de l'utilisateur courant sur cette app (null si pas de rôle)
    app_version: Optional[str] = None  # Version de l'app (capturée à l'enregistrement)
    is_favorite: bool = False  # Si l'utilisateur a mis cette app en favori
    category: Optional[str] = None  # Catégorie de l'application
    group: Optional[AppGroupBrief] = None  # Groupe visuel (null si non groupée)
    last_deployment: Optional[dict] = None  # Dernier déploiement auto (success/failed) — null si jamais déployée

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_app_with_role(
        cls,
        app,
        my_role: Optional[str] = None,
        app_version: Optional[str] = None,
        is_favorite: bool = False,
        group: Optional[AppGroupBrief] = None,
        last_deployment: Optional[dict] = None
    ) -> "AppResponseWithMyRole":
        """Crée une réponse avec le rôle de l'utilisateur courant"""
        # Gérer l'enum ApprovalStatus
        approval_status = ApprovalStatusEnum(app.approval_status.value) if app.approval_status else ApprovalStatusEnum.approved
        return cls(
            id=app.id,
            name=app.name,
            slug=app.slug,
            description=app.description,
            icon=app.icon,
            color=app.color,
            api_url=app.api_url,
            frontend_url=app.frontend_url,
            callback_url=app.callback_url,
            is_active=app.is_active,
            requires_auth=app.requires_auth,
            is_shared_service=app.is_shared_service,
            is_system_app=bool(getattr(app, 'is_system_app', False)),
            is_remote=getattr(app, 'is_remote', False) or False,
            is_unmanaged=bool(getattr(app, 'is_unmanaged', False)),
            m2m_enforcement_mode=getattr(app, 'm2m_enforcement_mode', 'compat') or 'compat',
            registered_from_ip=getattr(app, 'registered_from_ip', None),
            frontend_https_port=getattr(app, 'frontend_https_port', None),
            approval_status=approval_status,
            created_at=app.created_at,
            is_workshop=bool(getattr(app, 'is_workshop', False)),
            my_role=my_role,
            app_version=app_version,
            is_favorite=is_favorite,
            category=getattr(app, 'category', None),
            git_repo_url=getattr(app, 'git_repo_url', None),
            group=group,
            last_deployment=last_deployment,
        )


class AppBrief(BaseModel):
    id: int
    name: str
    slug: str
    icon: str
    color: str
    
    model_config = ConfigDict(from_attributes=True)


class AppWithUsers(AppResponse):
    users: List[UserBriefWithRole] = []
    
    @classmethod
    def from_orm_model(cls, app) -> "AppWithUsers":
        """Convertit une Application ORM avec user_roles en AppWithUsers"""
        users = [
            UserBriefWithRole(
                id=uar.user.id,
                username=uar.user.username,
                is_active=uar.user.is_active,
                role=uar.role
            )
            for uar in app.user_roles
        ]
        # Gérer l'enum ApprovalStatus
        approval_status = ApprovalStatusEnum(app.approval_status.value) if app.approval_status else ApprovalStatusEnum.approved
        return cls(
            id=app.id,
            name=app.name,
            slug=app.slug,
            description=app.description,
            icon=app.icon,
            color=app.color,
            api_url=app.api_url,
            frontend_url=app.frontend_url,
            callback_url=app.callback_url,
            is_active=app.is_active,
            requires_auth=app.requires_auth,
            is_shared_service=app.is_shared_service,
            is_system_app=bool(getattr(app, 'is_system_app', False)),
            is_remote=getattr(app, 'is_remote', False) or False,
            registered_from_ip=getattr(app, 'registered_from_ip', None),
            frontend_https_port=getattr(app, 'frontend_https_port', None),
            approval_status=approval_status,
            created_at=app.created_at,
            users=users
        )


class AppWithUsersAndDocker(AppWithUsers):
    """Application avec utilisateurs pour l'admin"""
    docker_status: Optional[str] = None  # Conservé pour compatibilité, toujours None
    health_status: Optional[str] = None  # Conservé pour compatibilité, toujours None
    category: Optional[str] = None  # Catégorie (badge/filtre dashboard)
    group_id: Optional[int] = None  # Groupe visuel (null = hors groupe)
    git_repo_url: Optional[str] = None  # URL du dépôt Git (déploiement auto, update-check)
    last_deployment: Optional[dict] = None  # Dernier déploiement auto (success/failed) — null si jamais déployée

    @classmethod
    def from_orm_model_with_docker(cls, app, last_deployment: Optional[dict] = None) -> "AppWithUsersAndDocker":
        """Convertit une Application ORM avec user_roles en AppWithUsersAndDocker"""
        users = [
            UserBriefWithRole(
                id=uar.user.id,
                username=uar.user.username,
                is_active=uar.user.is_active,
                role=uar.role
            )
            for uar in app.user_roles
        ]
        # Gérer l'enum ApprovalStatus
        approval_status = ApprovalStatusEnum(app.approval_status.value) if app.approval_status else ApprovalStatusEnum.approved
        return cls(
            id=app.id,
            name=app.name,
            slug=app.slug,
            description=app.description,
            icon=app.icon,
            color=app.color,
            category=getattr(app, 'category', None),
            group_id=getattr(app, 'group_id', None),
            api_url=app.api_url,
            frontend_url=app.frontend_url,
            callback_url=app.callback_url,
            is_active=app.is_active,
            requires_auth=app.requires_auth,
            is_shared_service=app.is_shared_service,
            is_system_app=bool(getattr(app, 'is_system_app', False)),
            is_remote=getattr(app, 'is_remote', False) or False,
            registered_from_ip=getattr(app, 'registered_from_ip', None),
            frontend_https_port=getattr(app, 'frontend_https_port', None),
            approval_status=approval_status,
            created_at=app.created_at,
            users=users,
            git_repo_url=getattr(app, 'git_repo_url', None),
            last_deployment=last_deployment,
        )


class DockerInfoResponse(BaseModel):
    """Informations détaillées Docker d'une application pour développeurs"""
    # Info application Hub
    app_slug: str
    app_name: str
    app_api_url: str
    app_frontend_url: Optional[str] = None
    
    # Accès au daemon Docker depuis le Hub (False = socket non monté / scan désactivé,
    # comportement normal en production durcie)
    docker_available: bool = True

    # Info conteneur Docker
    container_found: bool = False
    container_id: Optional[str] = None
    container_name: Optional[str] = None
    container_status: Optional[str] = None  # 'running', 'exited', etc.
    container_image: Optional[str] = None
    container_created: Optional[str] = None
    container_started_at: Optional[str] = None
    
    # Ports exposés
    ports: List["DockerPortInfo"] = []
    
    # Labels Hub (utiles pour config)
    hub_labels: dict = {}  # Labels hub.* du conteneur
    
    # Variables d'environnement APP_*
    app_env_vars: dict = {}  # Variables APP_* du conteneur
    
    # Informations réseau
    networks: List[str] = []  # Noms des réseaux Docker
    ip_addresses: dict = {}  # network_name -> ip_address
    
    # Message d'aide pour développeur
    developer_info: dict = {}
    
    # Stats CPU/RAM du conteneur (disponibles si le conteneur est running)
    container_stats: Optional[dict] = None  # cpu_percent, memory_usage_mb, memory_limit_mb, uptime_seconds


# ===== Docker Stats Schema =====
class DockerContainerStats(BaseModel):
    """Stats ressources d'un conteneur Docker"""
    cpu_percent: float = 0.0
    memory_usage_mb: float = 0.0
    memory_limit_mb: float = 0.0
    uptime_seconds: Optional[int] = None


class DockerStatsResponse(BaseModel):
    """Réponse des stats Docker par port"""
    stats: dict = {}  # port (str) -> DockerContainerStats
    docker_available: bool = False
    timestamp: Optional[str] = None


# ===== Découverte M2M Schemas =====
class AppDiscoveryItem(BaseModel):
    """Informations d'une application pour la découverte M2M"""
    slug: str = Field(..., description="Identifiant unique de l'application")
    name: str = Field(..., description="Nom de l'application")
    description: Optional[str] = Field(None, description="Description de l'application")
    api_url: str = Field(..., description="URL de l'API de l'application")
    is_shared_service: bool = Field(..., description="Si True, API partagée accessible à tous les SA")
    
    model_config = ConfigDict(from_attributes=True)


class DiscoverResponse(BaseModel):
    """Réponse de l'endpoint de découverte M2M"""
    shared_services: List[AppDiscoveryItem] = Field(
        ..., 
        description="Liste des APIs partagées (is_shared_service=True)"
    )
    total_shared: int = Field(..., description="Nombre total d'APIs partagées")


# ===== Auth Schemas =====
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    refresh_token: Optional[str] = None  # Inclus seulement pour les logins utilisateur
    must_change_password: bool = False  # Si True, l'utilisateur doit changer son mot de passe
    beacon_token: Optional[str] = None  # Token HMAC éphémère pour sécuriser beacon-logout
    remote_logout: Optional[list[dict]] = None  # SLO poste partagé (cf. slo_helpers)


class AppLaunchResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    launch_url: str
    app_slug: str
    role: str


class TokenPayload(BaseModel):
    sub: str
    roles: List[str]
    apps: Dict[str, str]  # {app_slug: role} — correspond au payload JWT réel
    exp: datetime
    jti: Optional[str] = None  # JWT ID pour révocation


class LoginRequest(BaseModel):
    username: str
    password: str
    redirect_to: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    """Requête de déconnexion avec refresh token optionnel"""
    refresh_token: Optional[str] = None


class ClientCredentialsRequest(BaseModel):
    """Requête OAuth2 Client Credentials pour M2M"""
    grant_type: str = Field(..., pattern="^client_credentials$")
    client_id: str
    client_secret: str


class TokenIntrospection(BaseModel):
    """Réponse d'introspection de token (RFC 7662)"""
    active: bool
    sub: Optional[str] = None
    client_id: Optional[str] = None
    apps: Optional[dict] = None  # Dict avec {app_slug: role}
    exp: Optional[int] = None
    iat: Optional[int] = None
    jti: Optional[str] = None
    token_type: Optional[str] = None


# ===== Service Account Schemas =====
class ServiceAccountBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None


class ServiceAccountCreate(ServiceAccountBase):
    app_ids: List[int] = []  # Applications autorisées


class ServiceAccountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ServiceAccountResponse(ServiceAccountBase):
    id: int
    client_id: str
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    applications: List["AppBrief"] = []
    
    model_config = ConfigDict(from_attributes=True)


class ServiceAccountCreated(ServiceAccountResponse):
    """Réponse à la création - inclut le secret (affiché une seule fois)"""
    client_secret: str  # Ne sera plus jamais affiché après création
    secrets_file: Optional[str] = None  # Chemin du fichier où les credentials sont sauvegardés


# ===== User Access Schemas =====
class UserAppAccess(BaseModel):
    user_id: int
    app_ids: List[int]


# ===== Backup/Export/Import Schemas =====
class ImportMode(str, Enum):
    """Mode d'import pour gérer les conflits"""
    skip = "skip"        # Ignorer les entités existantes
    update = "update"    # Mettre à jour les entités existantes
    error = "error"      # Échouer si une entité existe


class AppRoleExport(BaseModel):
    """Rôle d'application pour l'export"""
    name: str
    description: Optional[str] = None


class UserExport(BaseModel):
    """Utilisateur pour l'export (avec hash du mot de passe)"""
    username: str
    email: Optional[str] = None
    hashed_password: str
    is_active: bool = True
    is_admin: bool = False
    must_change_password: bool = False
    can_change_password: bool = True
    created_at: Optional[datetime] = None
    uuid: Optional[str] = None  # Identifiant stable inter-apps (exemple-app-16 user_uuid)
    oidc_sub: Optional[str] = None  # Identifiant OIDC immuable
    prenom: Optional[str] = None
    nom: Optional[str] = None


class AppExport(BaseModel):
    """Application pour l'export"""
    name: str
    slug: str
    description: Optional[str] = None
    icon: str = "bi-app"
    color: str = "#0d6efd"
    api_url: str
    frontend_url: Optional[str] = None
    callback_url: Optional[str] = None
    is_active: bool = True
    requires_auth: bool = True
    is_shared_service: bool = False
    approval_status: str = "approved"  # pending, approved, rejected
    approved_by_username: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: Optional[datetime] = None
    available_roles: List[AppRoleExport] = []


class UserAppRoleExport(BaseModel):
    """Association user-app-role pour l'export"""
    username: str
    app_slug: str
    role: str
    source: str = "manual"


class ServiceAccountExport(BaseModel):
    """Service Account pour l'export"""
    name: str
    client_id: str
    client_secret_hash: str
    client_secret_plain: Optional[str] = None  # Secret en clair si disponible
    description: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    app_slugs: List[str] = []


class AppBackupExport(BaseModel):
    """Metadata d'un backup d'application pour l'export/import environnement"""
    backup_id: str
    app_slug: str
    app_version: Optional[str] = None
    format_version: Optional[str] = None
    total_items: int = 0
    total_preferences: int = 0
    total_files: int = 0
    total_tables: int = 0
    total_rows: int = 0
    total_size_bytes: int = 0
    checksum: Optional[str] = None
    includes_vaultwarden_snapshot: bool = False
    includes_secret_bundle: bool = False
    restore_requires_offline: bool = False
    storage_filename: str  # Nom du fichier dans l'archive ZIP (ex: backups/mon-app/backup_uuid.zip)
    created_by: Optional[str] = None
    created_by_type: Optional[str] = None
    created_at: Optional[datetime] = None
    description: Optional[str] = None


class BackupExport(BaseModel):
    """Structure complète d'export de l'environnement"""
    version: str = "1.0"
    exported_at: datetime
    exported_by: str
    users: List[UserExport] = []
    applications: List[AppExport] = []
    user_app_roles: List[UserAppRoleExport] = []
    service_accounts: List[ServiceAccountExport] = []
    app_backups: List[AppBackupExport] = []  # Backups d'applications (max 5 par app)
    jwt_private_key_b64: Optional[str] = None  # Clé privée encodée base64
    jwt_public_key_b64: Optional[str] = None   # Clé publique encodée base64


class BackupImport(BaseModel):
    """Structure d'import avec options"""
    import_mode: ImportMode = ImportMode.skip
    restore_jwt_keys: bool = False
    # Flags de sélection : choisir quelles catégories importer
    include_users: bool = True
    include_applications: bool = True
    include_user_app_roles: bool = True
    include_service_accounts: bool = True
    include_app_backups: bool = True
    # Données
    users: List[UserExport] = []
    applications: List[AppExport] = []
    user_app_roles: List[UserAppRoleExport] = []
    service_accounts: List[ServiceAccountExport] = []
    app_backups: List[AppBackupExport] = []  # Backups d'applications à restaurer
    jwt_private_key_b64: Optional[str] = None
    jwt_public_key_b64: Optional[str] = None


class ImportResult(BaseModel):
    """Résultat de l'import"""
    success: bool
    users_created: int = 0
    users_updated: int = 0
    users_skipped: int = 0
    apps_created: int = 0
    apps_updated: int = 0
    apps_skipped: int = 0
    service_accounts_created: int = 0
    service_accounts_updated: int = 0
    service_accounts_skipped: int = 0
    user_app_roles_created: int = 0
    user_app_roles_updated: int = 0
    app_backups_created: int = 0
    app_backups_skipped: int = 0
    app_backups_errors: int = 0
    errors: List[str] = []
    warnings: List[str] = []


# ===== Docker Scanner Schemas =====
class DockerPortInfo(BaseModel):
    """Information sur un port exposé par un conteneur Docker"""
    container_port: int
    host_port: Optional[int] = None
    protocol: str = "tcp"


class DockerContainerInfo(BaseModel):
    """Information sur un conteneur Docker découvert"""
    container_id: str
    name: str
    image: str
    status: str
    ports: List[DockerPortInfo]
    suggested_slug: str
    suggested_name: str
    labels: dict = {}
    is_registered: bool = False  # True si le conteneur est déjà enregistré comme app
    # Labels Hub spécifiques (extraits des labels Docker hub.*)
    hub_name: Optional[str] = None           # hub.name - Nom personnalisé pour l'app
    hub_description: Optional[str] = None    # hub.description - Description de l'app
    hub_shared: bool = False                  # hub.shared - Marquer comme service partagé M2M
    # Auto-pairing
    container_type: str = "unknown"          # 'api', 'frontend', 'unknown'
    base_slug: str = ""                      # Slug sans suffixe de type (pour regroupement)


class DockerAppGroup(BaseModel):
    """Groupe de conteneurs formant une application (API + Frontend optionnel)"""
    base_slug: str                           # Slug applicatif commun
    suggested_name: str                      # Nom suggéré pour l'application
    description: Optional[str] = None
    is_shared: bool = False
    is_registered: bool = False              # True si au moins un conteneur est déjà enregistré
    api_container: Optional[DockerContainerInfo] = None
    frontend_container: Optional[DockerContainerInfo] = None
    api_port: Optional[int] = None
    frontend_port: Optional[int] = None
    ungrouped_containers: List[DockerContainerInfo] = []  # Conteneurs non appairés dans ce groupe


class DockerScanResponse(BaseModel):
    """Réponse du scan Docker"""
    available: bool
    docker_version: Optional[str] = None
    containers_running: int = 0
    containers_total: int = 0
    discovered: List[DockerContainerInfo] = []
    groups: List[DockerAppGroup] = []        # Conteneurs groupés par application
    error: Optional[str] = None


# ===== Batch Import Schemas =====
class BatchAppItem(BaseModel):
    """Un élément d'import lot"""
    base_slug: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z0-9-]+$')
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    api_port: int = Field(..., ge=1, le=65535)
    frontend_port: Optional[int] = Field(None, ge=1, le=65535)
    is_shared: bool = False


class BatchImportRequest(BaseModel):
    """Requête d'import lot d'applications"""
    apps: List[BatchAppItem] = Field(..., min_length=1, max_length=50)


class BatchAppResult(BaseModel):
    """Résultat d'import d'une application"""
    slug: str
    name: str
    success: bool
    is_new: bool = False
    message: str
    credentials: Optional[dict] = None


class BatchImportResponse(BaseModel):
    """Réponse d'import lot"""
    total: int
    created: int
    updated: int
    errors: int
    results: List[BatchAppResult]


# ===== Auto-Registration Schemas =====
class AppRegisterRequest(BaseModel):
    """Requête d'auto-enregistrement d'une application"""
    slug: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z0-9-]+$', description="Slug unique de l'application (défini dans .env)")
    name: Optional[str] = Field(None, max_length=100, description="Nom de l'application (optionnel, utilise le slug si absent)")
    description: Optional[str] = Field(None, description="Description de l'application")
    api_port: int = Field(..., ge=1, le=65535, description="Port de l'API backend")
    frontend_port: Optional[int] = Field(None, ge=1, le=65535, description="Port du frontend (optionnel)")
    frontend_https_port: Optional[int] = Field(None, ge=1, le=65535, description="Port HTTPS du frontend (si HTTPS activé)")
    custom_host: Optional[str] = Field(None, description="Host/IP de l'application si hébergée sur un serveur distant (ex: 192.168.1.200)")
    is_shared: bool = Field(False, description="Si True, l'application sera accessible par tous les service accounts (M2M)")
    api_endpoints: Optional[Dict[str, Any]] = Field(None, description="Spécification OpenAPI (JSON) décrivant les endpoints métier de l'application")
    version: Optional[str] = Field(None, max_length=50, description="Version de l'app (fichier VERSION), capturée à l'enregistrement")


class AppRegisterResponse(BaseModel):
    """Réponse à l'auto-enregistrement"""
    message: str
    application: "AppResponse"
    credentials: Optional[dict] = None  # client_id, client_secret (retourné une seule fois)
    is_new: bool  # True si nouvelle app, False si existante
    pending_approval: bool = False  # True si l'app est en attente d'approbation admin


# ===== Allocation dynamique des ports =====
class AllocatePortsRequest(BaseModel):
    """Requête d'allocation de ports (pré-vol, avant docker compose up)."""
    slug: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z0-9-]+$', description="Slug unique de l'application")
    host: Optional[str] = Field(None, description="Host/IP de l'app (collision scopée par host). Si absent, le host du Hub est utilisé.")


class AllocatePortsResponse(BaseModel):
    """Réponse contenant les ports attribués."""
    api_port: int
    frontend_port: int
    frontend_https_port: int
    subnet: str | None = Field(None, description="Sous-réseau Docker de l'app (CIDR, ex: 10.50.0.0/24)")
    host: str
    reused: bool = Field(False, description="True si une allocation existante a été renvoyée (idempotence)")


class PortAllocationItem(BaseModel):
    id: int
    app_slug: str
    host: str
    api_port: int
    frontend_port: int
    frontend_https_port: int
    subnet: str | None = None


class PortsStatusResponse(BaseModel):
    """État du pool de ports (admin)."""
    api_range: list[int]
    frontend_range: list[int]
    used_api_ports: list[int]
    used_frontend_ports: list[int]
    free_api_count: int
    free_frontend_count: int
    host_occupied_ports: list[int] = Field(
        default_factory=list,
        description="Ports liés sur l'hôte local par des conteneurs Docker (exclus de l'attribution)",
    )
    allocations: list[PortAllocationItem]


class AppOpenApiPush(BaseModel):
    """Requête pour pousser la spécification OpenAPI d'une application"""
    openapi_spec: Dict[str, Any] = Field(..., description="Spécification OpenAPI complète (JSON)")


class AppOpenApiResponse(BaseModel):
    """Réponse après push de la spec OpenAPI"""
    message: str
    app_slug: str
    endpoints_count: int = Field(0, description="Nombre d'endpoints métier (après filtrage)")
    filtered_count: int = Field(0, description="Nombre d'endpoints techniques exclus")
    updated_at: datetime


class AppApprovalRequest(BaseModel):
    """Requête d'approbation ou rejet d'une application"""
    approve: bool = Field(..., description="True pour approuver, False pour rejeter")
    reason: Optional[str] = Field(None, max_length=500, description="Raison du rejet (obligatoire si approve=False)")


class AppApprovalResponse(BaseModel):
    """Réponse après approbation/rejet d'une application"""
    message: str
    application: "AppResponse"
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None


class PendingAppsResponse(BaseModel):
    """Liste des applications en attente d'approbation"""
    pending_apps: List["AppResponse"]
    total: int


class AppCredentialsResponse(BaseModel):
    """Réponse contenant les credentials M2M d'une application"""
    app_slug: str
    app_name: str
    client_id: str
    client_secret: Optional[str] = None  # Seulement après régénération
    hub_host: str
    hub_api_port: int = 8000
    message: str


# ===== App Backup Schemas =====
class BackupModeEnum(str, Enum):
    """Mode de sauvegarde pour les applications"""
    disabled = "disabled"    # Sauvegarde désactivée
    manual = "manual"        # Sauvegarde manuelle uniquement
    scheduled = "scheduled"  # Sauvegarde automatique planifiée


class BackupStatusEnum(str, Enum):
    """Statut d'un backup individuel ou du dernier backup d'une app"""
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    timeout = "timeout"


def _format_duration_human(seconds: Optional[int]) -> str:
    """Format une durée en secondes en chaîne lisible (ex: '1m 23s', '2h 5m')."""
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


class AppBackupConfig(BaseModel):
    """Configuration de sauvegarde pour une application"""
    backup_mode: BackupModeEnum = BackupModeEnum.manual
    backup_cron: Optional[str] = Field(None, description="Expression cron (ex: '0 2 * * *' = 2h du matin)")
    backup_retention: int = Field(5, ge=0, description="Nombre de backups à conserver (0 = illimité)")
    backup_quiesce: bool = Field(False, description="Arrêter Vaultwarden pendant le snapshot pour cohérence (Vaultwarden uniquement)")


class AppBackupConfigUpdate(BaseModel):
    """Mise à jour de la configuration de sauvegarde"""
    backup_mode: Optional[BackupModeEnum] = None
    backup_cron: Optional[str] = None
    backup_retention: Optional[int] = Field(None, ge=0)
    backup_quiesce: Optional[bool] = None


class AppBackupReceive(BaseModel):
    """Données reçues lors d'un push de backup depuis une application"""
    backup_id: str = Field(..., description="UUID du backup généré par l'application")
    app_version: Optional[str] = Field(None, description="Version de l'application")
    format_version: Optional[str] = Field(None, description="Version du format de backup")
    total_items: int = Field(0, ge=0)
    total_preferences: int = Field(0, ge=0)
    total_files: int = Field(0, ge=0)
    total_tables: int = Field(0, ge=0)
    total_rows: int = Field(0, ge=0)
    checksum: Optional[str] = Field(None, description="SHA-256 du fichier ZIP")
    description: Optional[str] = None
    includes_vaultwarden_snapshot: bool = False
    includes_secret_bundle: bool = False
    restore_requires_offline: bool = False


class AppBackupResponse(BaseModel):
    """Réponse détaillée d'un backup"""
    id: int
    backup_id: str
    app_slug: str
    app_version: Optional[str] = None
    total_items: int = 0
    total_preferences: int = 0
    total_files: int = 0
    total_tables: int = 0
    total_rows: int = 0
    total_size_bytes: int = 0
    total_size_human: str = "0 B"
    checksum: Optional[str] = None
    format_version: Optional[str] = None
    includes_vaultwarden_snapshot: bool = False
    includes_secret_bundle: bool = False
    restore_requires_offline: bool = False
    is_encrypted: bool = False
    created_by: Optional[str] = None
    created_by_type: str = "app"
    created_at: datetime
    description: Optional[str] = None
    # Cycle de vie / progression (migration 024)
    status: str = "success"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    duration_human: str = ""
    progress_step: Optional[str] = "done"
    progress_percent: int = 100
    error_message: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
    
    @classmethod
    def from_orm_model(cls, backup) -> "AppBackupResponse":
        """Convertit un AppBackup ORM en réponse"""
        size = backup.total_size_bytes or 0
        # Formatage lisible de la taille
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                size_human = f"{size:.1f} {unit}"
                break
            size /= 1024
        else:
            size_human = f"{size:.1f} TB"
        
        return cls(
            id=backup.id,
            backup_id=backup.backup_id,
            app_slug=backup.app_slug,
            app_version=backup.app_version,
            total_items=backup.total_items or 0,
            total_preferences=backup.total_preferences or 0,
            total_files=backup.total_files or 0,
            total_tables=getattr(backup, "total_tables", 0) or 0,
            total_rows=getattr(backup, "total_rows", 0) or 0,
            total_size_bytes=backup.total_size_bytes or 0,
            total_size_human=size_human,
            checksum=backup.checksum,
            format_version=getattr(backup, "format_version", None),
            includes_vaultwarden_snapshot=bool(getattr(backup, "includes_vaultwarden_snapshot", False)),
            includes_secret_bundle=bool(getattr(backup, "includes_secret_bundle", False)),
            restore_requires_offline=bool(getattr(backup, "restore_requires_offline", False)),
            is_encrypted=backup.is_encrypted or False,
            created_by=backup.created_by,
            created_by_type=backup.created_by_type or "app",
            created_at=backup.created_at,
            description=backup.description,
            status=getattr(backup, "status", None) or "success",
            started_at=getattr(backup, "started_at", None),
            completed_at=getattr(backup, "completed_at", None),
            duration_seconds=getattr(backup, "duration_seconds", None),
            duration_human=_format_duration_human(getattr(backup, "duration_seconds", None)),
            progress_step=getattr(backup, "progress_step", None) or "done",
            progress_percent=int(getattr(backup, "progress_percent", None) or 100),
            error_message=getattr(backup, "error_message", None),
        )


class AppBackupBrief(BaseModel):
    """Résumé d'un backup pour les listes"""
    id: int
    backup_id: str
    app_slug: str
    total_size_human: str
    created_at: datetime
    is_encrypted: bool = False
    includes_vaultwarden_snapshot: bool = False
    includes_secret_bundle: bool = False
    
    model_config = ConfigDict(from_attributes=True)


class AppBackupListResponse(BaseModel):
    """Liste des backups d'une application"""
    app_slug: str
    app_name: str
    backups: List[AppBackupResponse] = []
    total: int = 0
    backup_config: Optional[AppBackupConfig] = None
    is_unmanaged: bool = False


class AppBackupStatusResponse(BaseModel):
    """Statut de backup d'une application"""
    app_slug: str
    app_name: str
    app_id: Optional[int] = None
    is_system_app: bool = False
    is_unmanaged: bool = False
    backup_mode: BackupModeEnum
    backup_cron: Optional[str] = None
    backup_retention: int = 5
    backup_quiesce: bool = False
    last_backup_at: Optional[datetime] = None
    last_backup_id: Optional[str] = None
    last_backup_status: Optional[str] = None
    last_backup_duration_seconds: Optional[int] = None
    last_backup_duration_human: str = ""
    avg_backup_duration_seconds: Optional[int] = None
    next_backup_at: Optional[datetime] = None
    backups_count: int = 0
    total_size_bytes: int = 0
    total_size_human: str = "0 B"
    has_running_backup: bool = False
    running_backup_id: Optional[str] = None


class AllBackupsResponse(BaseModel):
    """Vue globale de tous les backups (admin)"""
    apps: List[AppBackupStatusResponse] = []
    total_apps: int = 0
    total_backups: int = 0
    total_size_bytes: int = 0
    total_size_human: str = "0 B"


class BackupTriggerResponse(BaseModel):
    """Réponse après déclenchement d'un backup"""
    success: bool
    message: str
    backup_id: Optional[str] = None
    app_slug: str
    status: str = "pending"
    started_at: Optional[datetime] = None


class InProgressBackupItem(BaseModel):
    """Backup actuellement en cours d'exécution."""
    backup_id: str
    app_slug: str
    app_name: str
    status: str
    progress_step: str
    progress_percent: int
    started_at: Optional[datetime] = None
    elapsed_seconds: int = 0
    avg_duration_seconds: Optional[int] = None
    avg_duration_human: str = ""
    created_by: Optional[str] = None
    created_by_type: Optional[str] = None


class InProgressBackupsResponse(BaseModel):
    """Liste des backups en cours sur l'ensemble du Hub."""
    items: List[InProgressBackupItem] = []
    total: int = 0


class BackupExportResponse(BaseModel):
    """Réponse après export chiffré d'un backup"""
    success: bool
    message: str
    backup_id: str
    encrypted_filename: str
    encrypted_size_bytes: int
    encrypted_size_human: str


class RestoreToAppRequest(BaseModel):
    """Options de restauration vers une application"""
    clear_existing: bool = False
    restore_items: bool = True
    restore_preferences: bool = True
    restore_files: bool = True
    restore_test_data: bool = True
    restore_vaultwarden: bool = False
    restore_secret_bundle: bool = False
    require_vaultwarden_offline: bool = True
    confirm_full_restore: bool = False


class RestoreToAppResponse(BaseModel):
    """Réponse après restauration vers une application"""
    success: bool
    message: str
    app_slug: str
    backup_id: str
    items_restored: int = 0
    preferences_restored: int = 0
    files_restored: int = 0
    items_skipped: int = 0
    files_skipped: int = 0
    errors: List[str] = []
    warnings: List[str] = []
    vaultwarden_snapshot_restored: bool = False
    vaultwarden_restore: Optional[Dict[str, Any]] = None
    secret_bundle_restored: bool = False
    secret_bundle_secret_names: List[str] = []
    accounts_reencrypted: int = 0
    requires_vaultwarden_restart: bool = False


class BackupImportResponse(BaseModel):
    """Réponse de l'import d'un backup .encrypted externe."""
    success: bool
    message: str
    backup_stored: bool = False
    backup_id: Optional[str] = None
    decrypted_size_bytes: int = 0
    restore: Optional[RestoreToAppResponse] = None


# ===== Bulk Operations Schemas =====

class BulkUserAppRoleAssignment(BaseModel):
    """Attribution d'un rôle sur une application"""
    app_id: int
    role: str = "user"
    reset: bool = False  # True : réinitialiser au rôle attendu par le profil


class BulkUserCreate(BaseModel):
    """Données pour créer un utilisateur en masse"""
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=8)
    is_admin: bool = False
    is_active: bool = True
    must_change_password: bool = False
    can_change_password: bool = True
    profile_ids: List[int] = []
    app_roles: List[BulkUserAppRoleAssignment] = []


class BulkUsersCreateRequest(BaseModel):
    """Requête de création d'utilisateurs en masse"""
    users: List[BulkUserCreate] = Field(..., min_length=1, max_length=100)


class BulkUserCreateResult(BaseModel):
    """Résultat de création d'un utilisateur"""
    username: str
    success: bool
    user_id: Optional[int] = None
    error: Optional[str] = None


class BulkUsersCreateResponse(BaseModel):
    """Réponse de création en masse"""
    total_requested: int
    total_created: int
    total_failed: int
    results: List[BulkUserCreateResult]


class BulkUserRoleUpdate(BaseModel):
    """Mise à jour des rôles d'un utilisateur"""
    user_id: int
    app_roles: List[BulkUserAppRoleAssignment]


class BulkUsersRolesUpdateRequest(BaseModel):
    """Requête de mise à jour des rôles en masse"""
    updates: List[BulkUserRoleUpdate] = Field(..., min_length=1, max_length=100)


class BulkUserRoleUpdateResult(BaseModel):
    """Résultat de mise à jour des rôles d'un utilisateur"""
    user_id: int
    username: str
    success: bool
    roles_updated: int = 0
    error: Optional[str] = None


class BulkUsersRolesUpdateResponse(BaseModel):
    """Réponse de mise à jour des rôles en masse"""
    total_requested: int
    total_updated: int
    total_failed: int
    results: List[BulkUserRoleUpdateResult]


class BulkUsersDeleteRequest(BaseModel):
    """Requête de suppression d'utilisateurs en masse"""
    user_ids: List[int] = Field(..., min_length=1, max_length=100)
    confirm: bool = Field(..., description="Doit être True pour confirmer la suppression")


class BulkUserDeleteResult(BaseModel):
    """Résultat de suppression d'un utilisateur"""
    user_id: int
    username: str
    success: bool
    error: Optional[str] = None


class BulkUsersDeleteResponse(BaseModel):
    """Réponse de suppression en masse"""
    total_requested: int
    total_deleted: int
    total_failed: int
    results: List[BulkUserDeleteResult]


class BulkAppRoleAssignment(BaseModel):
    """Attribution d'un rôle à plusieurs utilisateurs sur une application"""
    app_id: int
    user_ids: List[int] = Field(..., min_length=1)
    role: str = "user"


class BulkAppRolesAssignRequest(BaseModel):
    """Requête d'attribution de rôles sur applications en masse"""
    assignments: List[BulkAppRoleAssignment] = Field(..., min_length=1, max_length=50)


class BulkAppRoleAssignResult(BaseModel):
    """Résultat d'attribution d'un rôle"""
    app_id: int
    app_name: str
    user_id: int
    username: str
    role: str
    success: bool
    action: str = ""  # "created", "updated", "unchanged"
    error: Optional[str] = None


class BulkAppRolesAssignResponse(BaseModel):
    """Réponse d'attribution de rôles en masse"""
    total_assignments: int
    total_success: int
    total_failed: int
    results: List[BulkAppRoleAssignResult]


class BulkRemoveAppAccess(BaseModel):
    """Suppression d'accès à une application pour plusieurs utilisateurs"""
    app_id: int
    user_ids: List[int] = Field(..., min_length=1)


class BulkRemoveAppAccessRequest(BaseModel):
    """Requête de suppression d'accès en masse"""
    removals: List[BulkRemoveAppAccess] = Field(..., min_length=1, max_length=50)


class BulkRemoveAppAccessResult(BaseModel):
    """Résultat de suppression d'accès"""
    app_id: int
    app_name: str
    user_id: int
    username: str
    success: bool
    error: Optional[str] = None


class BulkRemoveAppAccessResponse(BaseModel):
    """Réponse de suppression d'accès en masse"""
    total_removals: int
    total_success: int
    total_failed: int
    results: List[BulkRemoveAppAccessResult]


class M2MObservedUsersRolesRequest(BaseModel):
    """Requête M2M pour récupérer les rôles applicatifs par identifiant utilisateur"""
    identifiers: List[str] = Field(..., min_length=1, max_length=500)

    @field_validator("identifiers")
    @classmethod
    def validate_identifiers(cls, value: List[str]) -> List[str]:
        cleaned = []
        for item in value:
            normalized = (item or "").strip()
            if not normalized:
                raise ValueError("Chaque identifiant doit être non vide")
            cleaned.append(normalized)
        return cleaned


class M2MObservedAppRole(BaseModel):
    """Rôle applicatif observé pour un utilisateur"""
    app_slug: str
    role: str


class M2MObservedUserData(BaseModel):
    """Données observées pour un utilisateur Hub"""
    profiles: List[ProfileBriefResponse] = []
    apps: List[M2MObservedAppRole] = []


class M2MUserSearchItem(BaseModel):
    """Utilisateur retourné par la recherche M2M"""
    username: str
    email: Optional[EmailStr] = None
    app_slug: str
    role: str


class M2MUserSearchResponse(BaseModel):
    """Réponse paginée de la recherche M2M d'utilisateurs"""
    count: int
    users: List[M2MUserSearchItem] = []


# ─── CSV Import Schemas ───

class CsvUserPreviewLine(BaseModel):
    """Résultat de validation d'une ligne du CSV d'import"""
    line_number: int
    username: str
    password_length: int
    profile_requested: Optional[str] = None
    profile_matched: Optional[ProfileBriefResponse] = None
    profile_ignored: bool = False
    is_valid: bool = True
    errors: List[str] = []
    warnings: List[str] = []


class CsvPreviewResponse(BaseModel):
    """Réponse de preview d'un import CSV"""
    lines: List[CsvUserPreviewLine]
    total_lines: int
    total_valid: int
    total_errors: int
    total_warnings: int
    available_profiles: List[ProfileBriefResponse] = []


# Forward references
UserResponse.model_rebuild()
ProfileResponse.model_rebuild()
ServiceAccountResponse.model_rebuild()
ServiceAccountCreated.model_rebuild()
DockerInfoResponse.model_rebuild()


# ===== Message Schemas =====

class MessagePriority(str, Enum):
    """Niveaux de priorité des messages"""
    info = "info"
    warning = "warning"
    urgent = "urgent"


class MessageTargetType(str, Enum):
    """Type de ciblage des messages"""
    user = "user"
    broadcast = "broadcast"


class MessageSenderType(str, Enum):
    """Type d'expéditeur des messages"""
    admin = "admin"
    system = "system"
    app = "app"


class MessageCreate(BaseModel):
    """Creation d'un message par un admin"""
    title: str = Field(..., min_length=1, max_length=200)
    body: Optional[str] = None
    priority: MessagePriority = MessagePriority.info
    target_type: MessageTargetType = MessageTargetType.broadcast
    target_user_ids: Optional[List[int]] = None  # Requis si target_type == "user"
    action_url: Optional[str] = Field(None, max_length=500)
    action_label: Optional[str] = Field(None, max_length=100)
    expires_at: Optional[datetime] = None
    scheduled_at: Optional[datetime] = None  # Envoi differe
    template_id: Optional[int] = None  # Template pre-remplissage


class MessageM2MCreate(BaseModel):
    """Création d'un message par une application (M2M)"""
    title: str = Field(..., min_length=1, max_length=200)
    body: Optional[str] = None
    priority: MessagePriority = MessagePriority.info
    target_usernames: List[str] = Field(..., min_length=1)
    action_url: Optional[str] = Field(None, max_length=500)
    action_label: Optional[str] = Field(None, max_length=100)
    expires_at: Optional[datetime] = None
    # Champs de renvoi (optionnels, rétro-compatible)
    is_resend: Optional[bool] = False
    original_hub_message_id: Optional[int] = None


class MessageBrief(BaseModel):
    """Message résumé pour l'overlay SSE et la vue utilisateur"""
    id: int
    title: str
    body: Optional[str] = None
    priority: str
    sender_type: str
    sender_app_slug: Optional[str] = None
    action_url: Optional[str] = None
    action_label: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    is_read: bool = False
    # Champs ajoutés (rétro-compat : optionnels)
    status: Optional[str] = "active"
    target_type: Optional[str] = None
    delivered_at: Optional[datetime] = None
    clicked_at: Optional[datetime] = None
    read_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MessageTemplateCreate(BaseModel):
    """Creation d'un template de message"""
    name: str = Field(..., min_length=1, max_length=255)
    title_template: Optional[str] = Field(None, max_length=500)
    body_template: Optional[str] = None
    default_priority: MessagePriority = MessagePriority.info
    default_target_type: MessageTargetType = MessageTargetType.broadcast
    default_expiry_hours: Optional[int] = Field(None, ge=0)
    default_action_url: Optional[str] = Field(None, max_length=500)
    default_action_label: Optional[str] = Field(None, max_length=100)


class MessageTemplateUpdate(BaseModel):
    """Mise a jour d'un template de message"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    title_template: Optional[str] = Field(None, max_length=500)
    body_template: Optional[str] = None
    default_priority: Optional[MessagePriority] = None
    default_target_type: Optional[MessageTargetType] = None
    default_expiry_hours: Optional[int] = Field(None, ge=0)
    default_action_url: Optional[str] = Field(None, max_length=500)
    default_action_label: Optional[str] = Field(None, max_length=100)


class MessageTemplateResponse(BaseModel):
    """Reponse d'un template de message"""
    id: int
    name: str
    title_template: Optional[str] = None
    body_template: Optional[str] = None
    default_priority: str
    default_target_type: str
    default_expiry_hours: Optional[int] = None
    default_action_url: Optional[str] = None
    default_action_label: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MessageReadReceiptResponse(BaseModel):
    """Accusé de lecture"""
    user_id: int
    username: str
    read_at: Optional[datetime] = None
    # Champs optionnels (rétro-compat)
    delivered_at: Optional[datetime] = None
    clicked_at: Optional[datetime] = None


class MessageResponse(BaseModel):
    """Réponse complète d'un message (vue admin)"""
    id: int
    sender_type: str
    sender_id: Optional[int] = None
    sender_app_slug: Optional[str] = None
    sender_username: Optional[str] = None
    target_type: str
    target_user_id: Optional[int] = None
    target_username: Optional[str] = None
    priority: str
    title: str
    body: Optional[str] = None
    action_url: Optional[str] = None
    action_label: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    read_count: int = 0
    total_targets: int = 0
    is_resend: bool = False
    original_message_id: Optional[int] = None
    # Cycle de vie + engagement (ajouts rétro-compat)
    status: str = "active"
    archived_at: Optional[datetime] = None
    delivered_count: int = 0
    click_count: int = 0
    is_expired: bool = False

    model_config = ConfigDict(from_attributes=True)


class PaginatedMessagesResponse(BaseModel):
    """Réponse paginée pour la liste des messages"""
    messages: List[MessageResponse]
    total: int
    page: int
    per_page: int
    pages: int


# ===== Sync M2M Schemas (pour exemple-app) =====

class MessageSyncItem(BaseModel):
    """Message dans la réponse de synchronisation M2M."""
    id: int
    sender_type: str
    sender_id: Optional[int] = None
    sender_app_slug: Optional[str] = None
    sender_username: Optional[str] = None
    target_type: str
    target_user_id: Optional[int] = None
    target_username: Optional[str] = None
    priority: str
    title: str
    body: Optional[str] = None
    action_url: Optional[str] = None
    action_label: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    is_resend: bool = False
    original_message_id: Optional[int] = None
    read_count: int = 0
    total_targets: int = 0
    read_receipts: List[MessageReadReceiptResponse] = []
    # Cycle de vie + engagement (ajouts rétro-compat)
    status: Optional[str] = "active"
    archived_at: Optional[datetime] = None
    delivered_count: int = 0
    click_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class MessageSyncResponse(BaseModel):
    """Réponse de synchronisation M2M."""
    messages: List[MessageSyncItem]
    total: int
    page: int
    pages: int
    sync_timestamp: str


class ReceiptSyncItem(BaseModel):
    """Accusé de lecture dans la réponse de synchronisation."""
    message_id: int
    user_id: int
    username: Optional[str] = None
    read_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    clicked_at: Optional[datetime] = None


class ReceiptSyncResponse(BaseModel):
    """Réponse de synchronisation des accusés de lecture."""
    receipts: List[ReceiptSyncItem]
    total: int
    page: int
    pages: int
    sync_timestamp: str


class MessageDetailSyncResponse(BaseModel):
    """Détail complet d'un message pour la synchronisation M2M."""
    message: MessageSyncItem
    read_receipts: List[ReceiptSyncItem] = []


# ===== Account Request Schemas =====

class AccountRequestCreate(BaseModel):
    """Demande de création de compte par un utilisateur non inscrit"""
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=8)

    @field_validator('username')
    @classmethod
    def normalize_username(cls, v: str) -> str:
        return v.strip().lower()


class AccountRequestResponse(BaseModel):
    """Réponse d'une demande de compte (vue admin)"""
    id: int
    first_name: str
    last_name: str
    username: str
    email: Optional[str] = None
    status: str
    rejection_reason: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AccountRequestStatusResponse(BaseModel):
    """Réponse du polling de statut (vue demandeur, sans auth)"""
    status: str
    rejection_reason: Optional[str] = None
    auto_login_token: Optional[str] = None


class AccountRequestReview(BaseModel):
    """Validation ou rejet d'une demande par un admin"""
    rejection_reason: Optional[str] = Field(None, max_length=500)
    profile_ids: Optional[List[int]] = None
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None

    @field_validator('username')
    @classmethod
    def normalize_username(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        return v or None


class AccountRequestFormConfig(BaseModel):
    """Configuration de la modale de demande de compte (message + champs)"""
    important_message: str = Field(default="", max_length=2000)
    show_first_name: bool = True
    show_last_name: bool = True
    show_email: bool = False


class AccountRequestNotificationAdmin(BaseModel):
    """Admin pouvant recevoir les notifications de demandes de compte"""
    id: int
    username: str
    email: Optional[EmailStr] = None

    model_config = ConfigDict(from_attributes=True)


class AccountRequestNotificationAdminsResponse(BaseModel):
    """Configuration des admins notifies pour les demandes de compte"""
    selected_admin_ids: List[int] = []
    admins: List[AccountRequestNotificationAdmin] = []


class AccountRequestNotificationAdminsUpdate(BaseModel):
    """Mise a jour des admins notifies pour les demandes de compte"""
    admin_ids: List[int] = []


# =============================================================================
# Schemas de snapshots complets
# =============================================================================


class HubSnapshotResponse(BaseModel):
    id: int
    snapshot_id: str
    status: str
    progress_percent: int = 0
    progress_step: Optional[str] = "queued"
    s3_bucket: Optional[str] = None
    s3_key: Optional[str] = None
    local_path: Optional[str] = None
    total_size_bytes: int = 0
    total_size_human: str = ""
    checksum_sha256: Optional[str] = None
    manifest: Optional[Dict[str, Any]] = None
    include_pg_dump: bool = True
    include_files: bool = True
    include_app_backups: bool = True
    include_docker_config: bool = True
    include_apps_code: bool = True
    pg_dump_size_bytes: int = 0
    apps_count: int = 0
    apps_succeeded: int = 0
    apps_failed: int = 0
    error_message: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_human: str = ""
    expires_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm_model(cls, snapshot) -> "HubSnapshotResponse":
        size = snapshot.total_size_bytes or 0
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                size_human = f"{size:.1f} {unit}"
                break
            size /= 1024
        else:
            size_human = f"{size:.1f} TB"

        duration_human = ""
        if getattr(snapshot, "created_at", None) and getattr(snapshot, "completed_at", None):
            secs = int((snapshot.completed_at - snapshot.created_at).total_seconds())
            if secs < 60:
                duration_human = f"{secs}s"
            elif secs < 3600:
                duration_human = f"{secs // 60}m {secs % 60}s"
            else:
                duration_human = f"{secs // 3600}h {(secs % 3600) // 60}m"

        return cls(
            id=snapshot.id,
            snapshot_id=snapshot.snapshot_id,
            status=getattr(snapshot, "status", "pending"),
            progress_percent=getattr(snapshot, "progress_percent", 0),
            progress_step=getattr(snapshot, "progress_step", "queued"),
            s3_bucket=getattr(snapshot, "s3_bucket", None),
            s3_key=getattr(snapshot, "s3_key", None),
            local_path=getattr(snapshot, "local_path", None),
            total_size_bytes=snapshot.total_size_bytes or 0,
            total_size_human=size_human,
            checksum_sha256=getattr(snapshot, "checksum_sha256", None),
            manifest=getattr(snapshot, "manifest", None),
            include_pg_dump=bool(getattr(snapshot, "include_pg_dump", True)),
            include_files=bool(getattr(snapshot, "include_files", True)),
            include_app_backups=bool(getattr(snapshot, "include_app_backups", True)),
            include_docker_config=bool(getattr(snapshot, "include_docker_config", True)),
            include_apps_code=bool(getattr(snapshot, "include_apps_code", True)),
            pg_dump_size_bytes=snapshot.pg_dump_size_bytes or 0,
            apps_count=getattr(snapshot, "apps_count", 0),
            apps_succeeded=getattr(snapshot, "apps_succeeded", 0),
            apps_failed=getattr(snapshot, "apps_failed", 0),
            error_message=getattr(snapshot, "error_message", None),
            created_by=getattr(snapshot, "created_by", None),
            created_at=getattr(snapshot, "created_at", None),
            completed_at=getattr(snapshot, "completed_at", None),
            duration_human=duration_human,
            expires_at=getattr(snapshot, "expires_at", None),
        )


class HubSnapshotListResponse(BaseModel):
    snapshots: List[HubSnapshotResponse] = []
    total: int = 0


class HubSnapshotProgressResponse(BaseModel):
    snapshot_id: str
    status: str
    progress_percent: int
    progress_step: Optional[str] = None
    error_message: Optional[str] = None


class HubSnapshotConfigResponse(BaseModel):
    snapshot_cron: str = ""
    snapshot_retention_max: int = 7
    snapshot_retention_min_age_hours: int = 24
    s3_endpoint: str = ""
    s3_bucket: str = "hub-snapshots"
    s3_region: str = "eu-west-1"
    s3_prefix: str = "prod/"
    s3_configured: bool = False


class HubSnapshotConfigUpdate(BaseModel):
    snapshot_cron: Optional[str] = None
    snapshot_retention_max: Optional[int] = Field(None, ge=1)
    snapshot_retention_min_age_hours: Optional[int] = Field(None, ge=0)
    s3_endpoint: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_prefix: Optional[str] = None


class HubSnapshotRestoreRequest(BaseModel):
    confirm_full_restore: bool = Field(False, description="Confirmation explicite de restauration complete")


class HubSnapshotRestoreResponse(BaseModel):
    snapshot_id: str
    status: str
    message: str


class HubSnapshotCreateResponse(BaseModel):
    snapshot_id: str
    status: str
    message: str


# ===== App Dependency Schemas =====


class DependencyEntry(BaseModel):
    slug: str = Field(..., min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    note: Optional[str] = None


class DependenciesPush(BaseModel):
    """Body de POST /api/apps/dependencies — graphe complet déclaré par l'app appelante."""
    depends_on: List[DependencyEntry] = Field(default_factory=list)


class DependenciesPushResponse(BaseModel):
    stored: int
    source_slug: str


class DependencyOut(BaseModel):
    source: str
    target: str
    note: Optional[str] = None
    declared_at: Optional[str] = None
    last_sync_at: Optional[str] = None


class DependenciesGraph(BaseModel):
    dependencies: List[DependencyOut]


class SyncReport(BaseModel):
    target: str = Field(..., min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')


# ===== GitHub Catalog Schemas =====

class GithubRepo(BaseModel):
    """Repo GitHub tel qu'exposé par GET /api/apps/github/repos."""
    name: str
    full_name: str
    owner: str
    html_url: str
    git_url: str  # ATTENTION : contient le clone_url HTTPS (clonable avec PAT), pas le git:// nu
    description: Optional[str] = None
    default_branch: Optional[str] = None
    fork: bool
    archived: bool
    private: bool
    updated_at: Optional[str] = None  # ISO
    already_integrated: bool = False  # croisé avec git_repo_url des apps existantes


class GithubRepoListResponse(BaseModel):
    repos: list[GithubRepo]
    total: int
    fetched_at: Optional[str] = None  # ISO, dernière sync du cache
    token_configured: bool


class GithubRepoPrepareRequest(BaseModel):
    repo_full_name: str  # "owner/repo"


class GithubRepoPrepareResponse(BaseModel):
    slug: str
    name: str
    git_repo_url: str
    api_port: int
    frontend_port: int
    frontend_https_port: int
    subnet: str | None = None
    host: str
    ports_reused: bool


class GithubRepoRefreshResponse(BaseModel):
    synced: int
    total: int
    fetched_at: str
    token_configured: bool
    error: Optional[str] = None
    skipped: bool = False  # True si garde d'âge < 30s


# ─── Tags Utilisateurs ─────────────────────────────────────────────

class TagCategoryCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=80, pattern=r'^[a-z0-9_-]+$')
    label: str = Field(..., min_length=1, max_length=150)
    is_multiple: bool = Field(default=False)
    source: str = Field(..., pattern=r'^(csv|m2m)$')
    source_app_id: Optional[int] = None
    m2m_endpoint: str = Field(default='/api/tags/export', max_length=255)
    values: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_source_app(self):
        if self.source == 'm2m' and self.source_app_id is None:
            raise ValueError('source_app_id est requis quand source=m2m')
        return self


class TagCategoryUpdate(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=150)
    is_multiple: Optional[bool] = None
    values: Optional[list[str]] = None
    is_active: Optional[bool] = None
    m2m_endpoint: Optional[str] = Field(None, max_length=255)


class TagCategoryResponse(BaseModel):
    id: int
    slug: str
    label: str
    is_multiple: bool
    source: str
    source_app_id: Optional[int] = None
    m2m_endpoint: Optional[str] = '/api/tags/export'
    values: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TagCategoryListResponse(BaseModel):
    categories: list[TagCategoryResponse]
    total: int


class UserTagsResponse(BaseModel):
    tags: dict[str, list[str]]
    seen_categories: list[str] = Field(default_factory=list)


class UserTagsUpdate(BaseModel):
    tags: dict[str, list[str]]


class UserTagsAcknowledge(BaseModel):
    categories: list[str]


class UserTagsAcknowledgeResponse(BaseModel):
    acknowledged: list[str]
