from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class GrantType(str, Enum):
    """Types de grant OAuth2 supportés"""
    password = "password"
    refresh_token = "refresh_token"
    client_credentials = "client_credentials"


# ===== User Schemas =====
class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[EmailStr] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=4)
    is_admin: bool = False


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=4)
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


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
    created_at: datetime
    app_roles: List[UserAppRoleResponse] = []
    
    class Config:
        from_attributes = True
    
    @classmethod
    def from_orm_model(cls, user) -> "UserResponse":
        """Crée une réponse utilisateur avec les rôles par application"""
        app_roles = [
            UserAppRoleResponse.from_orm_model(uar) 
            for uar in user.app_roles
        ]
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_admin=user.is_admin,
            created_at=user.created_at,
            app_roles=app_roles
        )


class UserBrief(BaseModel):
    id: int
    username: str
    is_active: bool
    
    class Config:
        from_attributes = True


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


class AppResponse(AppBase):
    id: int
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


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
            created_at=app.created_at,
            users=users
        )


# ===== Auth Schemas =====
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    refresh_token: Optional[str] = None  # Inclus seulement pour les logins utilisateur


class TokenPayload(BaseModel):
    sub: str
    roles: List[str]
    apps: List[str]
    exp: datetime
    jti: Optional[str] = None  # JWT ID pour révocation


class LoginRequest(BaseModel):
    username: str
    password: str
    redirect_to: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


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
    apps: Optional[List[str]] = None
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


# Forward references
UserResponse.model_rebuild()
ServiceAccountResponse.model_rebuild()
ServiceAccountCreated.model_rebuild()
