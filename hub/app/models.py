from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Table, Enum as SQLEnum
from sqlalchemy.orm import relationship
from datetime import datetime
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


# Table d'association ServiceAccount <-> Application (simple)
service_account_app_access = Table(
    'service_account_app_access',
    Base.metadata,
    Column('service_account_id', Integer, ForeignKey('service_accounts.id', ondelete='CASCADE')),
    Column('app_id', Integer, ForeignKey('applications.id', ondelete='CASCADE'))
)


class UserAppRole(Base):
    """Table d'association User <-> Application avec rôle"""
    __tablename__ = "user_app_roles"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    app_id = Column(Integer, ForeignKey('applications.id', ondelete='CASCADE'), nullable=False)
    role = Column(String(50), default="user", nullable=False)  # admin, manager, user, viewer, etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relations
    user = relationship("User", back_populates="app_roles")
    application = relationship("Application", back_populates="user_roles")
    
    def __repr__(self):
        return f"<UserAppRole user={self.user_id} app={self.app_id} role={self.role}>"


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
    
    # Relations
    app_roles = relationship("UserAppRole", back_populates="user", cascade="all, delete-orphan")
    
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
    api_url = Column(String(255), nullable=False)  # URL de l'API backend
    frontend_url = Column(String(255))  # URL du frontend
    callback_url = Column(String(255))  # URL de callback pour le token
    is_active = Column(Boolean, default=True)
    requires_auth = Column(Boolean, default=True)
    is_shared_service = Column(Boolean, default=False)  # Si True, accessible par tous les service accounts
    
    # Système d'approbation pour l'auto-enregistrement
    approval_status = Column(SQLEnum(ApprovalStatus), default=ApprovalStatus.approved)  # pending pour les auto-register
    approved_by_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String(500), nullable=True)  # Raison du rejet si applicable
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations
    user_roles = relationship("UserAppRole", back_populates="application", cascade="all, delete-orphan")
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    available_roles = relationship("AppRole", back_populates="application", cascade="all, delete-orphan")
    service_accounts = relationship("ServiceAccount", secondary=service_account_app_access, back_populates="applications")
    
    @property
    def users(self):
        """Retourne les utilisateurs ayant accès (compatibilité)"""
        return [ur.user for ur in self.user_roles]
    
    def __repr__(self):
        return f"<Application {self.name}>"


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
    applications = relationship("Application", secondary=service_account_app_access, back_populates="service_accounts")
    
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
    logout_reason = Column(String(50))  # explicit, timeout, token_revoked
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    
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
    
    def __repr__(self):
        status = "active" if self.is_active else f"closed ({self.duration_seconds}s)"
        return f"<UserSession user={self.user_id} {status}>"
