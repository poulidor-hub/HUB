from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum


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
    profile_id: Optional[int] = None


class PasswordChangeRequest(BaseModel):
    """Requête de changement de mot de passe par l'utilisateur"""
    current_password: str = Field(..., description="Mot de passe actuel")
    new_password: str = Field(..., min_length=8, description="Nouveau mot de passe (min 8 caractères)")


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

    class Config:
        from_attributes = True


class ProfileBriefResponse(BaseModel):
    """Résumé d'un profil (pour affichage inline dans la liste users)"""
    id: int
    name: str
    color: str

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


class ProfileApplyRequest(BaseModel):
    """Appliquer un profil à un ou plusieurs utilisateurs"""
    user_ids: List[int] = Field(..., min_length=1)
    sync_roles: bool = Field(default=True, description="Synchroniser les user_app_roles avec ceux du profil")


# ===== User App Role Schema =====
class UserAppRoleResponse(BaseModel):
    app_id: int
    app_name: str
    app_slug: str
    role: str
    
    class Config:
        from_attributes = True
    
    @classmethod
    def from_orm_model(cls, uar):
        """Convertit un UserAppRole ORM en réponse"""
        return cls(
            app_id=uar.app_id,
            app_name=uar.application.name,
            app_slug=uar.application.slug,
            role=uar.role
        )


class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    must_change_password: bool = False
    can_change_password: bool = True
    created_at: datetime
    app_roles: List[UserAppRoleResponse] = []
    profile: Optional[ProfileBriefResponse] = None
    
    class Config:
        from_attributes = True
    
    @classmethod
    def from_orm_model(cls, user) -> "UserResponse":
        """Crée une réponse utilisateur avec les rôles par application"""
        app_roles = [
            UserAppRoleResponse.from_orm_model(uar) 
            for uar in user.app_roles
        ]
        # Profil (peut être None)
        profile_brief = None
        try:
            if user.profile_id and user.profile:
                profile_brief = ProfileBriefResponse(
                    id=user.profile.id,
                    name=user.profile.name,
                    color=user.profile.color or "#6c757d"
                )
        except Exception:
            pass  # profile non chargé
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_admin=user.is_admin,
            must_change_password=user.must_change_password or False,
            can_change_password=user.can_change_password if user.can_change_password is not None else True,
            created_at=user.created_at,
            app_roles=app_roles,
            profile=profile_brief
        )


class UserBrief(BaseModel):
    id: int
    username: str
    is_active: bool
    
    class Config:
        from_attributes = True


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
    
    class Config:
        from_attributes = True


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

    @field_validator('frontend_url', mode='before')
    @classmethod
    def normalize_frontend_url_to_http(cls, v):
        """Normalise frontend_url en HTTP.
        
        Les applications gèrent elles-mêmes la redirection HTTP→HTTPS,
        le Hub ne doit donc utiliser que le port HTTP pour les redirections utilisateur.
        """
        if v and isinstance(v, str) and v.startswith("https://"):
            return "http://" + v[len("https://"):]
        return v
    is_shared_service: bool = Field(default=False, description="Si True, cette app est accessible par tous les service accounts")


class AppCreateSimplified(BaseModel):
    """Schéma simplifié pour créer une application (ports au lieu d'URLs)"""
    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z0-9-]+$')
    description: Optional[str] = None
    icon: str = "bi-app"
    color: str = "#0d6efd"
    api_port: int = Field(..., ge=1, le=65535, description="Port de l'API backend")
    frontend_port: Optional[int] = Field(None, ge=1, le=65535, description="Port du frontend")
    custom_host: Optional[str] = Field(None, description="Host personnalisé si différent du Hub")
    requires_auth: bool = True
    create_service_account: bool = Field(False, description="Créer un service account M2M pour cette application")
    is_shared_service: bool = Field(False, description="Si True, cette app est accessible par tous les service accounts (M2M)")


class AppCreate(AppBase):
    pass


class AppUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    api_url: Optional[str] = None
    frontend_url: Optional[str] = None
    callback_url: Optional[str] = None
    is_active: Optional[bool] = None
    requires_auth: Optional[bool] = None
    is_shared_service: Optional[bool] = None

    @field_validator('frontend_url', mode='before')
    @classmethod
    def normalize_frontend_url_to_http(cls, v):
        """Normalise frontend_url en HTTP (les apps gèrent elles-mêmes le HTTPS)"""
        if v and isinstance(v, str) and v.startswith("https://"):
            return "http://" + v[len("https://"):]
        return v


class AppResponse(AppBase):
    id: int
    is_active: bool
    is_shared_service: bool
    is_remote: bool = False  # True si hébergée sur un serveur distant
    registered_from_ip: Optional[str] = None  # IP source lors de l'auto-enregistrement
    approval_status: ApprovalStatusEnum = ApprovalStatusEnum.approved
    created_at: datetime
    
    @field_validator('is_remote', mode='before')
    @classmethod
    def convert_none_to_false(cls, v):
        """Convertit None en False pour is_remote (anciennes apps en BDD)"""
        return v if v is not None else False
    
    class Config:
        from_attributes = True


class AppResponseWithMyRole(AppResponse):
    """Application avec le rôle de l'utilisateur courant (pour le dashboard)"""
    my_role: Optional[str] = None  # Rôle de l'utilisateur courant sur cette app (null si pas de rôle)
    app_version: Optional[str] = None  # Version de l'app (depuis le dernier backup)
    
    class Config:
        from_attributes = True
    
    @classmethod
    def from_app_with_role(
        cls, 
        app, 
        my_role: Optional[str] = None,
        app_version: Optional[str] = None
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
            is_remote=getattr(app, 'is_remote', False) or False,
            registered_from_ip=getattr(app, 'registered_from_ip', None),
            approval_status=approval_status,
            created_at=app.created_at,
            my_role=my_role,
            app_version=app_version
        )


class AppBrief(BaseModel):
    id: int
    name: str
    slug: str
    icon: str
    color: str
    
    class Config:
        from_attributes = True


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
            is_remote=getattr(app, 'is_remote', False) or False,
            registered_from_ip=getattr(app, 'registered_from_ip', None),
            approval_status=approval_status,
            created_at=app.created_at,
            users=users
        )


class AppWithUsersAndDocker(AppWithUsers):
    """Application avec utilisateurs pour l'admin"""
    docker_status: Optional[str] = None  # Conservé pour compatibilité, toujours None
    health_status: Optional[str] = None  # Conservé pour compatibilité, toujours None
    
    @classmethod
    def from_orm_model_with_docker(cls, app) -> "AppWithUsersAndDocker":
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
            api_url=app.api_url,
            frontend_url=app.frontend_url,
            callback_url=app.callback_url,
            is_active=app.is_active,
            requires_auth=app.requires_auth,
            is_shared_service=app.is_shared_service,
            is_remote=getattr(app, 'is_remote', False) or False,
            registered_from_ip=getattr(app, 'registered_from_ip', None),
            approval_status=approval_status,
            created_at=app.created_at,
            users=users
        )


class DockerInfoResponse(BaseModel):
    """Informations détaillées Docker d'une application pour développeurs"""
    # Info application Hub
    app_slug: str
    app_name: str
    app_api_url: str
    app_frontend_url: Optional[str] = None
    
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
    
    class Config:
        from_attributes = True


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
    roles: Optional[List[str]] = None
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
    
    class Config:
        from_attributes = True


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
    total_items: int = 0
    total_preferences: int = 0
    total_files: int = 0
    total_size_bytes: int = 0
    checksum: Optional[str] = None
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
    custom_host: Optional[str] = Field(None, description="Host/IP de l'application si hébergée sur un serveur distant (ex: 192.168.1.200)")
    is_shared: bool = Field(False, description="Si True, l'application sera accessible par tous les service accounts (M2M)")


class AppRegisterResponse(BaseModel):
    """Réponse à l'auto-enregistrement"""
    message: str
    application: "AppResponse"
    credentials: Optional[dict] = None  # client_id, client_secret (retourné une seule fois)
    is_new: bool  # True si nouvelle app, False si existante
    pending_approval: bool = False  # True si l'app est en attente d'approbation admin


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
    """Statut du dernier backup"""
    success = "success"
    failed = "failed"
    running = "running"


class AppBackupConfig(BaseModel):
    """Configuration de sauvegarde pour une application"""
    backup_mode: BackupModeEnum = BackupModeEnum.manual
    backup_cron: Optional[str] = Field(None, description="Expression cron (ex: '0 2 * * *' = 2h du matin)")
    backup_retention: int = Field(5, ge=0, description="Nombre de backups à conserver (0 = illimité)")


class AppBackupConfigUpdate(BaseModel):
    """Mise à jour de la configuration de sauvegarde"""
    backup_mode: Optional[BackupModeEnum] = None
    backup_cron: Optional[str] = None
    backup_retention: Optional[int] = Field(None, ge=0)


class AppBackupReceive(BaseModel):
    """Données reçues lors d'un push de backup depuis une application"""
    backup_id: str = Field(..., description="UUID du backup généré par l'application")
    app_version: Optional[str] = Field(None, description="Version de l'application")
    total_items: int = Field(0, ge=0)
    total_preferences: int = Field(0, ge=0)
    total_files: int = Field(0, ge=0)
    checksum: Optional[str] = Field(None, description="SHA-256 du fichier ZIP")
    description: Optional[str] = None


class AppBackupResponse(BaseModel):
    """Réponse détaillée d'un backup"""
    id: int
    backup_id: str
    app_slug: str
    app_version: Optional[str] = None
    total_items: int = 0
    total_preferences: int = 0
    total_files: int = 0
    total_size_bytes: int = 0
    total_size_human: str = "0 B"
    checksum: Optional[str] = None
    is_encrypted: bool = False
    created_by: Optional[str] = None
    created_by_type: str = "app"
    created_at: datetime
    description: Optional[str] = None
    
    class Config:
        from_attributes = True
    
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
            total_size_bytes=backup.total_size_bytes or 0,
            total_size_human=size_human,
            checksum=backup.checksum,
            is_encrypted=backup.is_encrypted or False,
            created_by=backup.created_by,
            created_by_type=backup.created_by_type or "app",
            created_at=backup.created_at,
            description=backup.description
        )


class AppBackupBrief(BaseModel):
    """Résumé d'un backup pour les listes"""
    id: int
    backup_id: str
    app_slug: str
    total_size_human: str
    created_at: datetime
    is_encrypted: bool = False
    
    class Config:
        from_attributes = True


class AppBackupListResponse(BaseModel):
    """Liste des backups d'une application"""
    app_slug: str
    app_name: str
    backups: List[AppBackupResponse] = []
    total: int = 0
    backup_config: Optional[AppBackupConfig] = None


class AppBackupStatusResponse(BaseModel):
    """Statut de backup d'une application"""
    app_slug: str
    app_name: str
    backup_mode: BackupModeEnum
    backup_cron: Optional[str] = None
    backup_retention: int = 5
    last_backup_at: Optional[datetime] = None
    last_backup_id: Optional[str] = None
    last_backup_status: Optional[str] = None
    next_backup_at: Optional[datetime] = None
    backups_count: int = 0
    total_size_bytes: int = 0
    total_size_human: str = "0 B"


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


# ===== Bulk Operations Schemas =====

class BulkUserAppRoleAssignment(BaseModel):
    """Attribution d'un rôle sur une application"""
    app_id: int
    role: str = "user"


class BulkUserCreate(BaseModel):
    """Données pour créer un utilisateur en masse"""
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=8)
    is_admin: bool = False
    is_active: bool = True
    must_change_password: bool = False
    can_change_password: bool = True
    profile_id: Optional[int] = None
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


# Forward references
UserResponse.model_rebuild()
ProfileResponse.model_rebuild()
ServiceAccountResponse.model_rebuild()
ServiceAccountCreated.model_rebuild()
DockerInfoResponse.model_rebuild()
