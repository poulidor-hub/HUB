from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Table
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


# Table d'association User <-> Application
user_app_access = Table(
    'user_app_access',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE')),
    Column('app_id', Integer, ForeignKey('applications.id', ondelete='CASCADE'))
)


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations
    applications = relationship("Application", secondary=user_app_access, back_populates="users")
    
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations
    users = relationship("User", secondary=user_app_access, back_populates="applications")
    
    def __repr__(self):
        return f"<Application {self.name}>"


class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    action = Column(String(50), nullable=False)  # login, logout, access_app, etc.
    target = Column(String(100))  # app_slug, user_id, etc.
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    details = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<AuditLog {self.action} by user {self.user_id}>"
