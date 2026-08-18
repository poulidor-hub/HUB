from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey, Text, Table, Enum as SQLEnum, JSON, UniqueConstraint, Index, func
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from typing import Optional
import secrets
import enum
from app.database import Base


def utcnow() -> datetime:
    """Retourne l'heure UTC actuelle sans timezone (compatible DB et Python 3.12+).
    Remplace utcnow() qui est déprécié depuis Python 3.12.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    Column('assigned_at', DateTime, default=utcnow),
    Column('assigned_by', Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    UniqueConstraint('user_id', 'profile_id', name='uq_user_profiles_user_profile'),
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
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    
    # Relations
    app_roles = relationship("ProfileAppRole", back_populates="profile", cascade="all, delete-orphan", lazy="raise")
    users = relationship("User", secondary=user_profiles, back_populates="profiles", lazy="raise",
                          primaryjoin="Profile.id == user_profiles.c.profile_id",
                          secondaryjoin="User.id == user_profiles.c.user_id")
    
    def __repr__(self):
        return f"<Profile {self.name}>"


class TagCategory(Base):
    __tablename__ = "tag_categories"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(80), unique=True, nullable=False)
    label = Column(String(150), nullable=False)
    is_multiple = Column(Boolean, default=False, nullable=False)
    source = Column(String(20), nullable=False)  # "csv" ou "m2m"
    source_app_id = Column(Integer, ForeignKey('applications.id', ondelete='SET NULL'), nullable=True)
    m2m_endpoint = Column(String(255), default='/api/tags/export')  # chemin endpoint M2M
    values = Column(JSON, nullable=False, default=list)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    source_app = relationship("Application", lazy="raise")
    user_tags = relationship("UserTag", back_populates="category", cascade="all, delete-orphan", lazy="raise")


class UserTag(Base):
    __tablename__ = "user_tags"
    __table_args__ = (
        UniqueConstraint('user_id', 'category_id', 'value', name='uq_user_tags_user_category_value'),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    category_id = Column(Integer, ForeignKey('tag_categories.id', ondelete='CASCADE'), nullable=False)
    value = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="tags", lazy="raise")
    category = relationship("TagCategory", back_populates="user_tags", lazy="raise")


class UserTagSeen(Base):
    """Catégories de tags déjà présentées (popup ack) à un utilisateur."""
    __tablename__ = "user_tag_seen"
    __table_args__ = (
        UniqueConstraint('user_id', 'category_id', name='uq_user_tag_seen_user_category'),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    category_id = Column(Integer, ForeignKey('tag_categories.id', ondelete='CASCADE'), nullable=False)
    seen_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="seen_tag_categories", lazy="raise")
    category = relationship("TagCategory", lazy="raise")


class UserAppRole(Base):
    """Table d'association User <-> Application avec rôle"""
    __tablename__ = "user_app_roles"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    app_id = Column(Integer, ForeignKey('applications.id', ondelete='CASCADE'), nullable=False)
    role = Column(String(50), default="user", nullable=False)  # admin, manager, user, viewer, etc.
    source = Column(String(50), default="manual", nullable=False)  # "manual" ou "profile:<id>"
    created_at = Column(DateTime, default=utcnow)
    
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
    totp_secret = Column(String(255), nullable=True)  # Secret TOTP chiffré Fernet (préfixe ENC:)
    totp_enabled = Column(Boolean, default=False)  # Authentificateur activé (après confirmation)
    totp_confirmed_at = Column(DateTime, nullable=True)  # Date d'activation du TOTP
    totp_last_counter = Column(BigInteger, nullable=True)  # Dernier pas de temps accepté (anti-rejeu)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    
    # Relations (lazy='raise' pour forcer selectinload explicite en async)
    app_roles = relationship("UserAppRole", back_populates="user", cascade="all, delete-orphan", lazy="raise")
    profiles = relationship("Profile", secondary=user_profiles, back_populates="users", lazy="raise",
                             primaryjoin="User.id == user_profiles.c.user_id",
                             secondaryjoin="Profile.id == user_profiles.c.profile_id")
    favorites = relationship("UserFavorite", back_populates="user", cascade="all, delete-orphan", lazy="raise")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    tags = relationship("UserTag", back_populates="user", cascade="all, delete-orphan", lazy="raise")
    seen_tag_categories = relationship(
        "UserTagSeen", back_populates="user", cascade="all, delete-orphan", lazy="raise"
    )
    
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

    def get_tags(self) -> dict[str, list[str]]:
        """Retourne les tags de l'utilisateur sous forme {category_slug: [values]}"""
        result: dict[str, list[str]] = {}
        for ut in self.tags:
            if ut.category.is_active:
                result.setdefault(ut.category.slug, []).append(ut.value)
        return result
    
    def __repr__(self):
        return f"<User {self.username}>"


class VpnPeer(Base):
    """Peer VPN WireGuard — un device d'un utilisateur HUB."""
    __tablename__ = "vpn_peers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    public_key = Column(String(44), unique=True, nullable=False)
    private_key = Column(String(64), nullable=False)
    vpn_ip = Column(String(18), unique=True, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", lazy="selectin")


class Application(Base):
    __tablename__ = "applications"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(50), unique=True, index=True, nullable=False)
    version = Column(String(50), nullable=True)  # Version de l'app, capturée à l'enregistrement
    description = Column(Text)
    icon = Column(String(50), default="bi-app")  # Bootstrap Icons
    color = Column(String(7), default="#0d6efd")  # Couleur hex
    category = Column(String(100), nullable=True)  # Catégorie (RH, SI, Communication, etc.)
    group_id = Column(Integer, ForeignKey('app_groups.id', ondelete='SET NULL'), nullable=True)  # Groupe visuel (dashboard)
    api_url = Column(String(255), nullable=False)  # URL de l'API backend
    git_repo_url = Column(String(512), nullable=True)  # URL du dépôt Git (GitHub) pour update-check et changelog
    frontend_url = Column(String(255))  # URL du frontend
    callback_url = Column(String(255))  # URL de callback pour le token
    frontend_https_port = Column(Integer, nullable=True)  # Port HTTPS du frontend (si HTTPS activé)
    is_active = Column(Boolean, default=True)
    requires_auth = Column(Boolean, default=True)
    m2m_enforcement_mode = Column(String(16), default='compat', server_default='compat', nullable=False)  # 'compat' ou 'enforced'
    is_shared_service = Column(Boolean, default=False)  # Si True, accessible par tous les service accounts
    is_system_app = Column(Boolean, default=False, nullable=False)  # Application interne du Hub
    is_workshop = Column(Boolean, default=False, nullable=False,
                         comment="True si l'app est un atelier (slug -ws, projet compose X-ws)")
    is_unmanaged = Column(Boolean, default=False, nullable=False,
                          comment="True si app non gérée (launcher-only) : présente/lancable mais exclue du health-check, backup et sync Docker")
    
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
    backup_quiesce = Column(Boolean, default=False, nullable=False)  # Stop/start auto du conteneur Vaultwarden pendant snapshot
    last_backup_at = Column(DateTime, nullable=True)  # Dernier backup réussi
    last_backup_id = Column(String(36), nullable=True)  # UUID du dernier backup
    next_backup_at = Column(DateTime, nullable=True)  # Prochain backup planifié
    last_backup_status = Column(String(20), nullable=True)  # success, failed, running
    
    # Spécification OpenAPI poussée par l'application (pour Discovery Explorer)
    api_endpoints = Column(JSON, nullable=True)  # Spec OpenAPI complète ou filtrée
    api_endpoints_updated_at = Column(DateTime, nullable=True)  # Date de dernière mise à jour de la spec
    
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    
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


class AppGroup(Base):
    """Groupe d'applications (regroupement purement visuel du dashboard)"""
    __tablename__ = "app_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(500), nullable=True)
    icon = Column(String(50), default="bi-collection")  # Bootstrap Icons
    color = Column(String(7), default="#0d6efd")  # Couleur hex
    created_at = Column(DateTime, default=utcnow)

    def __repr__(self):
        return f"<AppGroup {self.name}>"


class UserFavorite(Base):
    """Applications favorites épinglées par chaque utilisateur"""
    __tablename__ = "user_favorites"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    app_id = Column(Integer, ForeignKey('applications.id', ondelete='CASCADE'), nullable=False)
    created_at = Column(DateTime, default=utcnow)
    
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
    created_at = Column(DateTime, default=utcnow, index=True)
    
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
    created_at = Column(DateTime, default=utcnow, index=True)
    
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
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
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
    created_at = Column(DateTime, default=utcnow)
    revoked_at = Column(DateTime, nullable=True)
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    
    # Relations
    user = relationship("User", back_populates="refresh_tokens")
    service_account = relationship("ServiceAccount", backref="refresh_tokens")
    
    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > utcnow()
    
    def __repr__(self):
        return f"<RefreshToken for user_id={self.user_id}>"


class RevokedToken(Base):
    """Liste noire des tokens JWT révoqués (avant expiration naturelle)"""
    __tablename__ = "revoked_tokens"
    
    id = Column(Integer, primary_key=True, index=True)
    jti = Column(String(64), unique=True, index=True, nullable=False)  # JWT ID
    expires_at = Column(DateTime, nullable=False)  # Pour nettoyage automatique
    revoked_at = Column(DateTime, default=utcnow)
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
    login_at = Column(DateTime, nullable=False, default=utcnow)
    logout_at = Column(DateTime, nullable=True)  # NULL = session active
    logout_reason = Column(String(50))  # explicit, timeout, token_revoked, browser_closed, sse_disconnect
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    
    # Tracking de l'activité en temps réel
    last_activity_at = Column(DateTime, nullable=False, default=utcnow)  # Dernière activité (SSE ping, refresh, accès app)
    current_app_slug = Column(String(50), nullable=True)  # Application actuellement active (dernière accédée)
    
    # Relations
    user = relationship("User", back_populates="sessions")
    
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
        return int((utcnow() - self.last_activity_at).total_seconds())
    
    def __repr__(self):
        status = "active" if self.is_active else f"closed ({self.duration_seconds}s)"
        return f"<UserSession user={self.user_id} {status}>"


class AppUsageSegment(Base):
    """
    Segment d'usage d'une application au sein d'une session utilisateur.
    Ouvert à chaque accès app, clôt à la bascule (switch), au logout,
    au timeout ou à la révocation forced.
    NULL sur ended_at = segment en cours.
    """
    __tablename__ = "app_usage_segments"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('user_sessions.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    app_slug = Column(String(50), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, default=utcnow)
    ended_at = Column(DateTime, nullable=True)   # NULL = segment en cours
    end_reason = Column(String(20))              # switch, logout, timeout, forced

    __table_args__ = (
        Index('ix_app_usage_user_started', 'user_id', 'started_at'),
        Index('ix_app_usage_app_started', 'app_slug', 'started_at'),
    )

    def __repr__(self):
        return f"<AppUsageSegment user={self.user_id} app={self.app_slug}>"


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
    total_size_bytes = Column(BigInteger, default=0)
    checksum = Column(String(64))  # SHA-256 du fichier ZIP original
    format_version = Column(String(20))
    total_tables = Column(Integer, default=0)
    total_rows = Column(Integer, default=0)
    includes_vaultwarden_snapshot = Column(Boolean, default=False)
    includes_secret_bundle = Column(Boolean, default=False)
    restore_requires_offline = Column(Boolean, default=False)
    
    # Stockage
    # Nullable: la ligne est créée AVANT le téléchargement (status=pending),
    # le storage_path est rempli une fois le ZIP écrit sur disque.
    storage_path = Column(String(500), nullable=True)
    encrypted_path = Column(String(500))  # Chemin du fichier chiffré (si exporté)
    is_encrypted = Column(Boolean, default=False)

    # Cycle de vie / progression (migration 024)
    status = Column(String(20), nullable=False, default="success")
    # Valeurs: pending|running|success|failed|timeout
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    progress_step = Column(String(40), default="done")
    # Valeurs: queued|create|downloading|storing|done
    progress_percent = Column(Integer, default=100)
    error_message = Column(Text, nullable=True)

    # Traçabilité
    created_by = Column(String(100))  # Username ou service_account
    created_by_type = Column(String(20), default="app")  # 'app', 'user', 'scheduler'
    created_at = Column(DateTime, default=utcnow, index=True)
    description = Column(Text)
    
    # Relation
    application = relationship("Application", back_populates="backups")
    
    def __repr__(self):
        return f"<AppBackup {self.backup_id} for {self.app_slug}>"


class HubSnapshot(Base):
    """Snapshot complet du Hub (PostgreSQL + fichiers + backups apps + config Docker)"""
    __tablename__ = "hub_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(String(36), unique=True, index=True, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    progress_percent = Column(Integer, default=0)
    progress_step = Column(String(40), default="queued")
    s3_bucket = Column(String(255))
    s3_key = Column(String(500))
    local_path = Column(String(1000))
    total_size_bytes = Column(BigInteger, default=0)
    checksum_sha256 = Column(String(64))
    manifest = Column(JSON, default={})
    include_pg_dump = Column(Boolean, default=True)
    include_files = Column(Boolean, default=True)
    include_app_backups = Column(Boolean, default=True)
    include_docker_config = Column(Boolean, default=True)
    include_apps_code = Column(Boolean, default=True)
    pg_dump_size_bytes = Column(BigInteger, default=0)
    apps_count = Column(Integer, default=0)
    apps_succeeded = Column(Integer, default=0)
    apps_failed = Column(Integer, default=0)
    error_message = Column(Text)
    retention_policy = Column(JSON, default={})
    created_by = Column(String(100))
    created_at = Column(DateTime, default=utcnow, index=True)
    completed_at = Column(DateTime)
    expires_at = Column(DateTime)

    def __repr__(self):
        return f"<HubSnapshot {self.snapshot_id}>"


class UserMessage(Base):
    """
    Messages envoyés aux utilisateurs (par les admins, le système ou les apps via M2M).
    Supporte les messages ciblés (un ou plusieurs users) et les broadcasts (tous les users).
    """
    __tablename__ = "user_messages"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Expéditeur
    sender_type = Column(String(20), nullable=False)  # "admin", "system", "app"
    sender_id = Column(Integer, nullable=True)  # user_id admin OU service_account_id
    sender_app_slug = Column(String(50), index=True, nullable=True)  # Slug de l'app source (M2M)
    
    # Destinataire
    target_type = Column(String(20), nullable=False)  # "user", "broadcast"
    target_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    target_username = Column(String(150), nullable=True, index=True)  # Username résolu (pour sync NotifCenter)
    
    # Contenu
    priority = Column(String(20), nullable=False, default="info", index=True)  # "info", "warning", "urgent"
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=True)
    action_url = Column(String(500), nullable=True)  # Lien cliquable (ex: ouvrir un ticket)
    action_label = Column(String(100), nullable=True)  # Texte du bouton d'action
    
    # Expiration
    expires_at = Column(DateTime, nullable=True)  # NULL = pas d'expiration
    
    # Envoi differe (NULL = envoi immediat)
    scheduled_at = Column(DateTime, nullable=True)
    
    # Horodatage
    created_at = Column(DateTime, default=utcnow, index=True)
    
    # Cycle de vie (soft-delete admin) — "expired" reste calculé via expires_at
    status = Column(String(20), nullable=False, default="active")  # "active" | "archived"
    archived_at = Column(DateTime, nullable=True)
    
    # Compteurs agrégés cachés (perf admin) — maintenus par le code applicatif
    delivered_count = Column(Integer, nullable=False, default=0)
    read_count = Column(Integer, nullable=False, default=0)
    click_count = Column(Integer, nullable=False, default=0)
    total_targets = Column(Integer, nullable=False, default=0)
    
    # Renvoi (resend)
    is_resend = Column(Boolean, default=False)
    original_message_id = Column(Integer, ForeignKey('user_messages.id', ondelete='SET NULL'), nullable=True)
    
    # Relations
    target_user = relationship("User", foreign_keys=[target_user_id], lazy="raise")
    read_receipts = relationship("MessageReadReceipt", back_populates="message", cascade="all, delete-orphan", lazy="raise")
    
    def __repr__(self):
        return f"<UserMessage {self.id} [{self.priority}] '{self.title[:30]}'>"


class MessageReadReceipt(Base):
    """Accusé de lecture d'un message par un utilisateur"""
    __tablename__ = "message_read_receipts"
    
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey('user_messages.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    # read_at est nullable : un receipt peut exister pour tracker la livraison sans lecture
    read_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)  # Push SSE ou GET /me
    clicked_at = Column(DateTime, nullable=True)    # Clic sur action_url
    
    # Relations
    message = relationship("UserMessage", back_populates="read_receipts")
    user = relationship("User", lazy="raise")
    
    def __repr__(self):
        return f"<MessageReadReceipt msg={self.message_id} user={self.user_id}>"


class MessageTemplate(Base):
    """Templates de messages reutilisables (geres par les admins)"""
    __tablename__ = "message_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    title_template = Column(String(500), nullable=True)
    body_template = Column(Text, nullable=True)
    default_priority = Column(String(20), nullable=False, default="info")
    default_target_type = Column(String(20), nullable=False, default="broadcast")
    default_expiry_hours = Column(Integer, nullable=True)
    default_action_url = Column(String(500), nullable=True)
    default_action_label = Column(String(100), nullable=True)
    created_by = Column(String(150), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self):
        return f"<MessageTemplate {self.id} '{self.name}'>"


class AppSetting(Base):
    """Paramètre applicatif clé-valeur, modifiable à chaud par les admins"""
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    updated_by_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

    updated_by = relationship("User", foreign_keys=[updated_by_id], lazy="raise")

    def __repr__(self):
        return f"<AppSetting {self.key}={self.value}>"


class AccountRequest(Base):
    """Demande de création de compte par un utilisateur non inscrit"""
    __tablename__ = "account_requests"
    
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    username = Column(String(50), nullable=False, index=True)
    email = Column(String(100), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending, approved, rejected
    rejection_reason = Column(String(500), nullable=True)
    reviewed_by_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    polling_token = Column(String(64), nullable=False, index=True)
    auto_login_token = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow)
    
    # Relations
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id], lazy="raise")
    
    def __repr__(self):
        return f"<AccountRequest {self.username} [{self.status}]>"


class PortAllocation(Base):
    """Allocation de ports attribuée par le Hub à une application (pré-vol).

    Clé d'idempotence : (app_slug, host). La détection de collision est scopée
    par host car deux apps sur des machines différentes peuvent réutiliser le
    même port.
    """
    __tablename__ = "port_allocations"
    __table_args__ = (
        UniqueConstraint('app_slug', 'host', name='uq_port_allocations_slug_host'),
    )

    id = Column(Integer, primary_key=True, index=True)
    app_slug = Column(String(50), index=True, nullable=False)
    host = Column(String(255), nullable=False, default="")
    api_port = Column(Integer, nullable=False)
    frontend_port = Column(Integer, nullable=False)
    frontend_https_port = Column(Integer, nullable=False)
    # Sous-réseau Docker de l'app (ex: 10.50.0.0/24). NULL pour les allocations
    # antérieures à l'introduction du pool — rempli paresseusement au prochain
    # appel d'allocation de l'app.
    subnet = Column(String(30), nullable=True)
    allocated_at = Column(DateTime, default=utcnow)


class AppDeployment(Base):
    """Déploiement automatique d'une app (clone -> .env -> build -> up -> health).

    Lien par app_slug (pas de FK, cohérent avec PortAllocation).
    Un déploiement à la fois globalement (lock asyncio dans deploy_service).
    """

    __tablename__ = "app_deployments"

    id = Column(Integer, primary_key=True, index=True)
    app_slug = Column(String(50), index=True, nullable=False)
    status = Column(String(16), default="pending", nullable=False)  # pending|running|success|failed
    current_step = Column(String(50), nullable=True)  # clone|env|build|up|health
    logs = Column(Text, default="")
    triggered_by = Column(String(100), nullable=True)  # username admin
    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)


class AppDependency(Base):
    """Dépendance déclarée entre deux apps (M2M).

    Pilote : (1) les suggestions de groupe atelier côté hubctl (lecture disque
    du depends.yaml), (2) l'affichage dashboard avec last_sync_at.

    Clé d'idempotence : (source_slug, target_slug). Les slugs sont stockés
    en String (pas de FK vers applications.id) car l'app cible peut ne pas
    être encore enregistrée au moment du register().
    """
    __tablename__ = "app_dependencies"
    __table_args__ = (
        UniqueConstraint('source_slug', 'target_slug', name='uq_app_dep_pair'),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_slug = Column(String(100), index=True, nullable=False)
    target_slug = Column(String(100), index=True, nullable=False)
    note = Column(Text, nullable=True)
    declared_at = Column(DateTime, default=utcnow)
    last_sync_at = Column(DateTime, nullable=True)


class OutboxMessage(Base):
    """Message en attente de livraison différée (Transactional Outbox)."""
    __tablename__ = "outbox"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    target_url = Column(Text, nullable=False)
    target_slug = Column(String(100), nullable=True)
    method = Column(String(10), nullable=False, default="POST")
    headers = Column(JSON, default={})
    payload = Column(JSON, nullable=True)
    auth_context = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, default=utcnow)
    scheduled_at = Column(DateTime, default=utcnow)
    next_retry_at = Column(DateTime, nullable=True, default=utcnow)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=10)
    last_error = Column(Text, nullable=True)
    delivered_at = Column(DateTime, nullable=True)

    def __repr__(self):
        slug = self.target_slug or self.target_url
        return f"<OutboxMessage(id={self.id}, target='{slug}', status='{self.status}')>"


# ============================================================
# OIDC Provider - Bridge OIDC pour apps externes (Vikunja, etc.)
# ============================================================

class OAuthClient(Base):
    """Client OIDC : apps externe qui consomme le SSO HUB via OIDC (ex: Vikunja)."""
    __tablename__ = "oauth_clients"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String(100), unique=True, index=True, nullable=False)
    client_secret_hash = Column(String(255), nullable=False)
    name = Column(String(100), nullable=False)
    redirect_uris = Column(JSON, nullable=False, default=list)  # ["https://.../cb"]
    scopes = Column(JSON, nullable=False, default=lambda: ["openid", "profile", "email"])
    is_active = Column(Boolean, default=True)
    auto_approve = Column(Boolean, default=True)
    backchannel_logout_uri = Column(String(500), nullable=True)
    app_id = Column(Integer, ForeignKey('applications.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relations (lazy='raise' pour async)
    application = relationship("Application", lazy="raise")

    # --- Methods required by OIDC flow ---
    def check_client_secret(self, plain_secret: str) -> bool:
        from app.auth import verify_password
        return verify_password(plain_secret, self.client_secret_hash)

    def check_redirect_uri(self, uri: str) -> bool:
        return uri in (self.redirect_uris or [])

    def get_allowed_scopes(self, requested: list) -> list:
        allowed = set(self.scopes or [])
        return [s for s in (requested or []) if s in allowed]

    @staticmethod
    def generate_client_id() -> str:
        return f"oc_{secrets.token_hex(16)}"

    @staticmethod
    def generate_client_secret() -> str:
        return secrets.token_urlsafe(32)

    def __repr__(self):
        return f"<OAuthClient {self.client_id}>"


class OAuthAuthorizationCode(Base):
    """Code OIDC one-shot émis par /oidc/launch, échangé à /oidc/token."""
    __tablename__ = "oauth_authorization_codes"

    id = Column(Integer, primary_key=True, index=True)
    code_hash = Column(String(100), unique=True, index=True, nullable=False)
    client_id = Column(String(100), ForeignKey('oauth_clients.client_id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    redirect_uri = Column(String(500), nullable=False)
    scope = Column(String(200), nullable=False)
    code_challenge = Column(String(200), nullable=True)
    code_challenge_method = Column(String(10), nullable=True)
    nonce = Column(String(100), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    client = relationship("OAuthClient", lazy="raise")
    user = relationship("User", lazy="raise")

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= utcnow()

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def __repr__(self):
        return f"<OAuthAuthorizationCode {self.client_id} user={self.user_id}>"


class OAuthRefreshToken(Base):
    """Refresh token OIDC persistant et révocable (mirroir apps hub)."""
    __tablename__ = "oauth_refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String(100), unique=True, index=True, nullable=False)
    client_id = Column(String(100), ForeignKey('oauth_clients.client_id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    scope = Column(String(200), nullable=False)
    expires_at = Column(DateTime, nullable=True)  # NULL = pas d'expiration
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    client = relationship("OAuthClient", lazy="raise")
    user = relationship("User", lazy="raise")

    @property
    def is_valid(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= utcnow():
            return False
        return True

    def __repr__(self):
        return f"<OAuthRefreshToken {self.client_id} user={self.user_id} valid={self.is_valid}>"


class GithubRepoCache(Base):
    """Cache persistant des repos GitHub accessibles au PAT configuré.

    Peuplé par POST /api/apps/github/repos/refresh (upsert par repo_id).
    Lu par GET /api/apps/github/repos (zéro appel GitHub).
    """

    __tablename__ = "github_repo_cache"

    id = Column(Integer, primary_key=True)
    repo_id = Column(BigInteger, unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    full_name = Column(String(512), nullable=False, index=True)
    owner = Column(String(255), nullable=False, index=True)
    html_url = Column(String(1024), nullable=False)
    clone_url = Column(String(1024), nullable=False)
    description = Column(Text, nullable=True)
    default_branch = Column(String(255), nullable=True)
    fork = Column(Boolean, default=False, nullable=False)
    archived = Column(Boolean, default=False, nullable=False)
    private = Column(Boolean, default=False, nullable=False)
    updated_at_github = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, server_default=func.now(), nullable=False)
