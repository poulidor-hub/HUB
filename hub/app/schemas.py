from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime


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


class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    created_at: datetime
    applications: List["AppBrief"] = []
    
    class Config:
        from_attributes = True


class UserBrief(BaseModel):
    id: int
    username: str
    is_active: bool
    
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
    users: List[UserBrief] = []


# ===== Auth Schemas =====
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class TokenPayload(BaseModel):
    sub: str
    roles: List[str]
    apps: List[str]
    exp: datetime


class LoginRequest(BaseModel):
    username: str
    password: str
    redirect_to: Optional[str] = None


# ===== User Access Schemas =====
class UserAppAccess(BaseModel):
    user_id: int
    app_ids: List[int]


# Forward references
UserResponse.model_rebuild()
