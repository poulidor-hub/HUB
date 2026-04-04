from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Table, Enum as SQLEnum, JSON
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from typing import Optional
import secrets
import enum
from app.database import Base


class ApprovalStatus(enum.Enum):
    """Statut d'approbation des applications auto-enregistrées"""
    pending = "pending"      # En attente de validation admin
    approved = "approved"    # Approuvée par un admin
    rejected = "rejected"    # Rejetée par un admin


# Rôles par défaut disponibles
DEFAULT_ROLES = ["admin", "manager", "user", "viewer"]

# Hiérarchie des rôles (plus le nombre est élevé, plus le rôle est puissant)
ROLE_HIERARCHY = {"admin": 4, "manager": 3, "user": 2, "viewer": 1}


def highest_role(roles: list) -> str:
    """Retourne le rôle le plus élevé parmi une liste de rôles.
    Hiérarchie : admin > manager > user > viewer
    """
    if not roles:
        return "viewer"
    return max(roles, key=lambda r: ROLE_HIERARCHY.get(r, 0))


# Table d'association ServiceAccount <-> Application (simple)
service_account_app_access = Table(
    'service_account_app_access',
    Base.metadata,
    Column('service_account_id', Integer, ForeignKey('service_accounts.id', ondelete='CASCADE')),
    Column('app_id', Integer, ForeignKey('applications.id', ondelete='CASCADE'))
)

# Table d'association User <-> Profile (multi-profils)
user_profiles = Table(
    'user_profiles',
    Base.metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
    Column('profile_id', Integer, ForeignKey('profiles.id', ondelete='CASCADE'), nullable=False),
    Column('assigned_at', DateTime, default=datetime.utcnow),
    Column('assigned_by', Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
)


class ProfileAppRole(Base):
    """Apps + rôles inclus dans un profil"""
    __tablename__ = "profile_app_roles"
    
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey('profiles.id', ondelete='CASCADE'), nullable=False)
    app_id = Column(Integer, ForeignKey('applications.id', ondelete='CASCADE'), nullable=False)
    role = Column(String(50), default="user", nullable=False)
    
    # Relations
    profile = relationship("Profile", back_populates="app_roles")
    application = relationship("Application", lazy="raise")
    
    def __repr__(self):
        return f"<ProfileAppRole profile={self.profile_id} app={self.app_id} role={self.role}>"


class Profile(Base):
    """Profil-type (template de rôles) assignable aux utilisateurs"""
    __tablename__ = "profiles"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text)
    color = Column(String(7), default="#6c757d")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations
    app_roles = relationship("ProfileAppRole", back_populates="profile", cascade="all, delete-orphan", lazy="raise")
    users = relationship("User", secondary=user_profiles, back_populates="profiles", lazy="raise",
                          primaryjoin="Profile.id == user_profiles.c.profile_id",
                          secondaryjoin="User.id == user_profiles.c.user_id")
    
    def __repr__(self):
        return f"<Profile {self.name}>"


class UserAppRole(Base):
    """Table d'association User <-> Application avec rôle"""
    __tablename__ = "user_app_roles"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    app_id = Column(Integer, ForeignKey('applications.id', ondelete='CASCADE'), nullable=False)
    role = Column(String(50), default="user", nullable=False)  # admin, manager, user, viewer, etc.
    source = Column(String(50), default="manual", nullable=False)  # "manual" ou "profile:<id>"
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relations
    user = relationship("User", back_populates="app_roles")
    application = relationship("Application", back_populates="user_roles")
    
    def __repr__(self):
        return f"<UserAppRole user={self.user_id} app={self.app_id} role={self.role} source={self.source}>"


class AppRole(Base):
    """Rôles disponibles pour une application"""
    __tablename__ = "app_roles"
    
    id = Column(Integer, primary_key=True, index=True)
    app_id = Column(Integer, ForeignKey('applications.id', ondelete='CASCADE'), nullable=False)
    name = Column(String(50), nullable=False)  # Nom du rôle
    description = Column(String(255))
    
    # Relation
    application = relationship("Application", back_populates="available_roles")
    
    def __repr__(self):
        return f"<AppRole {self.name} for app={self.app_id}>"


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)  # Admin du Hub (pas des apps)
    must_change_password = Column(Boolean, default=False)  # Forcer le changement de mdp à la prochaine connexion
    can_change_password = Column(Boolean, default=True)  # Autoriser l'utilisateur à changer son mdp (False pour comptes partagés)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations (lazy='raise' pour forcer selectinload explicite en async)
    app_roles = relationship("UserAppRole", back_populates="user", cascade="all, delete-orphan", lazy="raise")
    profiles = relationship("Profile", secondary=user_profiles, back_populates="users", lazy="raise",
                             primaryjoin="User.id == user_profiles.c.user_id",
                             secondaryjoin="Profile.id == user_profiles.c.profile_id")
    favorites = relationship("UserFavorite", back_populates="user", cascade="all, delete-orphan", lazy="raise")
    
    @property
    def applications(self):
        """Retourne les applications de l'utilisateur (compatibilité)"""
        return [ar.application for ar in self.app_roles]
    
    def get_role_for_app(self, app_slug: str) -> Optional[str]:
        """Retourne le rôle de l'utilisateur pour une application"""
        for ar in self.app_roles:
            if ar.application.slug == app_slug:
                return ar.role
        return None
    
    def get_apps_with_roles(self) -> dict:
        """Retourne un dict {app_slug: role}"""
        return {ar.application.slug: ar.role for ar in self.app_roles}
    
    def __repr__(self):
        return f"<User {self.username}>"


class Application(Base):
    __tablename__ = "applications"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(50), unique=True, index=True, nullable=False)
    description = Column(Text)
    icon = Column(String(50), default="bi-app")  # Bootstrap Icons
    color = Column(String(7), default="#0d6efd")  # Couleur hex
    category = Column(String(100), nullable=True)  # Catégorie (RH, SI, Communication, etc.)
    api_url = Column(String(255), nullable=False)  # URL de l'API backend
    frontend_url = Column(String(255))  # URL du frontend
    callback_url = Column(String(255))  # URL de callback pour le token
    is_active = Column(Boolean, default=True)
    requires_auth = Column(Boolean, default=True)
    is_shared_service = Column(Boolean, default=False)  # Si True, accessible par tous les service accounts
    
    # Informations sur l'hébergement de l'application
    is_remote = Column(Boolean, default=False)  # True si hébergée sur un serveur distant (pas le même que le HUB)
    registered_from_ip = Column(String(45), nullable=True)  # IP source lors de l'auto-enregistrement
    
    # Système d'approbation pour l'auto-enregistrement
    approval_status = Column(SQLEnum(ApprovalStatus), default=ApprovalStatus.approved)  # pending pour les auto-register
    approved_by_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String(500), nullable=True)  # Raison du rejet si applicable
    
    # Configuration des sauvegardes
    backup_mode = Column(String(20), default="manual")  # disabled, manual, scheduled
    backup_cron = Column(String(100), nullable=True)  # Expression cron (ex: "0 2 * * *")
    backup_retention = Column(Integer, default=5)  # Nombre de backups à conserver (0 = illimité)
    last_backup_at = Column(DateTime, nullable=True)  # Dernier backup réussi
    last_backup_id = Column(String(36), nullable=True)  # UUID du dernier backup
    next_backup_at = Column(DateTime, nullable=True)  # Prochain backup planifié
    last_backup_status = Column(String(20), nullable=True)  # success, failed, running
    
    # Spécification OpenAPI poussée par l'application (pour Discovery Explorer)
    api_endpoints = Column(JSON, nullable=True)  # Spec OpenAPI complète ou filtrée
    api_endpoints_updated_at = Column(DateTime, nullable=True)  # Date de dernière mise à jour de la spec
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations (lazy='raise' pour forcer selectinload explicite en async)
    user_roles = relationship("UserAppRole", back_populates="application", cascade="all, delete-orphan", lazy="raise")
    approved_by = relationship("User", foreign_keys=[approved_by_id], lazy="raise")
    available_roles = relationship("AppRole", back_populates="application", cascade="all, delete-orphan", lazy="raise")
    service_accounts = relationship("ServiceAccount", secondary=service_account_app_access, back_populates="applications", lazy="raise")
    # Note: pas de cascade delete-orphan pour conserver les backups même si l'app est supprimée
    backups = relationship("AppBackup", back_populates="application", lazy="raise")
    
    @property
    def users(self):
        """Retourne les utilisateurs ayant accès (compatibilité)"""
        return [ur.user for ur in self.user_roles]
    
    def __repr__(self):
        return f"<Application {self.name}>"


class UserFavorite(Base):
    """Applications favorites épinglées par chaque utilisateur"""
    __tablename__ = "user_favorites"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    app_id = Column(Integer, ForeignKey('applications.id', ondelete='CASCADE'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relations
    user = relationship("User", back_populates="favorites")
    application = relationship("Application", lazy="raise")
    
    def __repr__(self):
        return f"<UserFavorite user={self.user_id} app={self.app_id}>"


class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String(36), index=True)  # UUID pour traçabilité cross-service
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    service_account_id = Column(Integer, ForeignKey('service_accounts.id', ondelete='SET NULL'), nullable=True)
    session_id = Column(Integer, ForeignKey('user_sessions.id', ondelete='SET NULL'), nullable=True)
    
    action = Column(String(50), nullable=False)  # login, logout, access_app, etc.
    target = Column(String(100))  # app_slug, user_id, etc.
    source_app_slug = Column(String(50), index=True, nullable=True)  # App source (X-Slug)
    
    request_method = Column(String(10))  # GET, POST, PUT, DELETE
    request_path = Column(String(255))   # /api/apps/xxx
    response_status = Column(Integer)     # 200, 403, 500, etc.
    duration_ms = Column(Integer)         # Temps de traitement en ms
    
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    details = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Relations
    service_account = relationship("ServiceAccount", backref="audit_logs")
    session = relationship("UserSession", backref="audit_logs")
    
    def __repr__(self):
        return f"<AuditLog {self.action} by user {self.user_id}>"


class ErrorLog(Base):
    """Logs des erreurs HTTP (4xx, 5xx) - Vue séparée pour monitoring"""
    __tablename__ = "error_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String(36), index=True)  # Lien avec AuditLog si existe
    
    error_code = Column(Integer, nullable=False, index=True)  # 401, 403, 404, 500, etc.
    error_type = Column(String(50))  # authentication, authorization, not_found, internal
    error_message = Column(String(500))
    
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    service_account_id = Column(Integer, ForeignKey('service_accounts.id', ondelete='SET NULL'), nullable=True)
    source_app_slug = Column(String(50), index=True, nullable=True)
    
    request_method = Column(String(10))
    request_path = Column(String(255), index=True)
    request_body = Column(Text)  # Corps de la requête (sanitisé)
    
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    stack_trace = Column(Text)  # Traceback pour erreurs 5xx
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    def __repr__(self):
        return f"<ErrorLog {self.error_code} on {self.request_path}>"


class ServiceAccount(Base):
    """Compte de service pour l'authentification Machine-to-Machine (M2M)"""
    __tablename__ = "service_accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    client_id = Column(String(64), unique=True, index=True, nullable=False)
    client_secret_hash = Column(String(255), nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    
    # Relations
    applications = relationship("Application", secondary=service_account_app_access, back_populates="service_accounts", lazy="raise")
    
    @staticmethod
    def generate_client_id() -> str:
        """Génère un client_id unique"""
        return f"sa_{secrets.token_hex(16)}"
    
    @staticmethod
    def generate_client_secret() -> str:
        """Génère un client_secret sécurisé"""
        return secrets.token_urlsafe(32)
    
    def __repr__(self):
        return f"<ServiceAccount {self.name}>"


class RefreshToken(Base):
    """Token de rafraîchissement pour les sessions longues"""
    __tablename__ = "refresh_tokens"
    
    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String(255), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=True)
    service_account_id = Column(Integer, ForeignKey('service_accounts.id', ondelete='CASCADE'), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    
    # Relations
    user = relationship("User", backref="refresh_tokens")
    service_account = relationship("ServiceAccount", backref="refresh_tokens")
    
    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.utcnow()
    
    def __repr__(self):
        return f"<RefreshToken for user_id={self.user_id}>"


class RevokedToken(Base):
    """Liste noire des tokens JWT révoqués (avant expiration naturelle)"""
    __tablename__ = "revoked_tokens"
    
    id = Column(Integer, primary_key=True, index=True)
    jti = Column(String(64), unique=True, index=True, nullable=False)  # JWT ID
    expires_at = Column(DateTime, nullable=False)  # Pour nettoyage automatique
    revoked_at = Column(DateTime, default=datetime.utcnow)
    reason = Column(String(100))  # logout, password_change, admin_revoke, etc.
    
    def __repr__(self):
        return f"<RevokedToken {self.jti}>"


class UserSession(Base):
    """
    Suivi des sessions utilisateurs (connexions/déconnexions).
    Ne stocke que les changements d'état = très léger en données.
    ~100 bytes par événement → 100 users × 2 events/jour = ~7 KB/jour
    """
    __tablename__ = "user_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    login_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    logout_at = Column(DateTime, nullable=True)  # NULL = session active
    logout_reason = Column(String(50))  # explicit, timeout, token_revoked, browser_closed, sse_disconnect
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    
    # Tracking de l'activité en temps réel
    last_activity_at = Column(DateTime, nullable=False, default=datetime.utcnow)  # Dernière activité (SSE ping, refresh, accès app)
    current_app_slug = Column(String(50), nullable=True)  # Application actuellement active (dernière accédée)
    
    # Relations
    user = relationship("User", backref="sessions")
    
    @property
    def duration_seconds(self) -> Optional[int]:
        """Durée de la session en secondes"""
        if self.logout_at:
            return int((self.logout_at - self.login_at).total_seconds())
        return None
    
    @property
    def is_active(self) -> bool:
        """Session considérée active si pas de logout"""
        return self.logout_at is None
    
    @property
    def idle_seconds(self) -> int:
        """Temps d'inactivité en secondes depuis la dernière activité"""
        return int((datetime.utcnow() - self.last_activity_at).total_seconds())
    
    def __repr__(self):
        status = "active" if self.is_active else f"closed ({self.duration_seconds}s)"
        return f"<UserSession user={self.user_id} {status}>"


class BackupMode(enum.Enum):
    """Mode de sauvegarde pour les applications"""
    disabled = "disabled"    # Sauvegarde désactivée
    manual = "manual"        # Sauvegarde manuelle uniquement
    scheduled = "scheduled"  # Sauvegarde automatique planifiée


class BackupStatus(enum.Enum):
    """Statut du dernier backup"""
    success = "success"
    failed = "failed"
    running = "running"


class AppBackup(Base):
    """
    Sauvegardes centralisées des applications.
    Stocke les métadonnées et références vers les fichiers ZIP.
    
    IMPORTANT: Les sauvegardes sont liées au app_slug (identifiant unique de l'application)
    et non à l'app_id. Cela permet de conserver les sauvegardes même si l'application
    est supprimée puis recréée (changement de serveur, réinstallation, etc.).
    """
    __tablename__ = "app_backups"
    
    id = Column(Integer, primary_key=True, index=True)
    backup_id = Column(String(36), unique=True, index=True, nullable=False)  # UUID
    
    # Référence à l'application - SET NULL pour conserver les backups si l'app est supprimée
    app_id = Column(Integer, ForeignKey('applications.id', ondelete='SET NULL'), nullable=True)
    app_slug = Column(String(50), index=True, nullable=False)  # Clé de rattachement principale
    app_version = Column(String(50))  # Version de l'app au moment du backup
    
    # Métadonnées du backup
    total_items = Column(Integer, default=0)
    total_preferences = Column(Integer, default=0)
    total_files = Column(Integer, default=0)
    total_size_bytes = Column(Integer, default=0)
    checksum = Column(String(64))  # SHA-256 du fichier ZIP original
    
    # Stockage
    storage_path = Column(String(500), nullable=False)  # Chemin relatif du fichier
    encrypted_path = Column(String(500))  # Chemin du fichier chiffré (si exporté)
    is_encrypted = Column(Boolean, default=False)
    
    # Traçabilité
    created_by = Column(String(100))  # Username ou service_account
    created_by_type = Column(String(20), default="app")  # 'app', 'user', 'scheduler'
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    description = Column(Text)
    
    # Relation
    application = relationship("Application", back_populates="backups")
    
    def __repr__(self):
        return f"<AppBackup {self.backup_id} for {self.app_slug}>"
