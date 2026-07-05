import jwt
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Union
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.models import User, ServiceAccount, RefreshToken, RevokedToken, UserAppRole, utcnow

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def hash_token(token: str) -> str:
    """Hash un token pour stockage sécurisé (refresh tokens, etc.)"""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_jti() -> str:
    """Génère un identifiant unique pour le JWT (pour révocation)"""
    return secrets.token_hex(16)


def create_access_token(
    username: str,
    apps: dict,  # Dict {app_slug: role}
    expires_delta: Optional[timedelta] = None,
    is_service_account: bool = False
) -> tuple[str, datetime, str]:
    """Crée un token JWT signé avec la clé privée RSA
    
    Args:
        username: Nom d'utilisateur ou client_id
        apps: Dict des applications avec leurs rôles {"app_slug": "role"}
        is_service_account: True si c'est un service account
    
    Returns:
        tuple: (token, expiration_datetime, jti)
    """
    if expires_delta:
        expire = utcnow() + expires_delta
    else:
        expire = utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    
    jti = generate_jti()
    
    payload = {
        "sub": username,
        "apps": apps,            # Dict {app_slug: role_in_app}
        "exp": expire,
        "iat": utcnow(),
        "jti": jti,
        "type": "service_account" if is_service_account else "user"
    }
    
    # Header avec kid pour identifier la clé de signature
    headers = {
        "kid": "hub-jwt-key-1"  # Doit correspondre au kid dans le JWKS
    }
    
    token = jwt.encode(
        payload,
        settings.jwt_private_key,
        algorithm=settings.jwt_algorithm,
        headers=headers
    )
    
    return token, expire, jti


def password_fingerprint(hashed_password: str) -> str:
    """Empreinte courte du hash de mot de passe (rend le reset token usage-unique)."""
    return hashlib.sha256(hashed_password.encode()).hexdigest()[:16]


def create_reset_token(username: str, hashed_password: str) -> str:
    """Crée un JWT de réinitialisation à usage unique, scope=pwd_reset."""
    expire = utcnow() + timedelta(minutes=settings.recovery_token_ttl_minutes)
    payload = {
        "sub": username,
        "scope": "pwd_reset",
        "pfp": password_fingerprint(hashed_password),
        "exp": expire,
        "iat": utcnow(),
    }
    return jwt.encode(
        payload,
        settings.jwt_private_key,
        algorithm=settings.jwt_algorithm,
        headers={"kid": "hub-jwt-key-1"},
    )


def decode_reset_token(token: str) -> dict:
    """Décode et valide un reset token. Lève HTTPException(400) si invalide."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Jeton de réinitialisation invalide ou expiré")
    if payload.get("scope") != "pwd_reset":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Jeton de réinitialisation invalide ou expiré")
    return payload


async def create_refresh_token(
    db: AsyncSession,
    user_id: Optional[int] = None,
    service_account_id: Optional[int] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> str:
    """Crée un refresh token et le stocke en base"""
    token = secrets.token_urlsafe(64)
    token_hash = hash_token(token)
    
    expires_at = utcnow() + timedelta(days=settings.refresh_token_expire_days)
    
    refresh_token = RefreshToken(
        token_hash=token_hash,
        user_id=user_id,
        service_account_id=service_account_id,
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent[:255] if user_agent else None
    )
    
    db.add(refresh_token)
    await db.commit()
    
    return token


async def verify_refresh_token(
    db: AsyncSession,
    token: str
) -> Optional[RefreshToken]:
    """Vérifie un refresh token et retourne l'objet si valide"""
    token_hash = hash_token(token)
    
    result = await db.execute(
        select(RefreshToken)
        .options(
            selectinload(RefreshToken.user).selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(RefreshToken.service_account).selectinload(ServiceAccount.applications)
        )
        .where(RefreshToken.token_hash == token_hash)
    )
    refresh_token = result.scalar_one_or_none()
    
    if not refresh_token or not refresh_token.is_valid:
        return None
    
    return refresh_token


async def rotate_refresh_token(
    db: AsyncSession,
    old_token: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> Optional[tuple[str, RefreshToken]]:
    """Rotation du refresh token : invalide l'ancien et crée un nouveau"""
    old_refresh = await verify_refresh_token(db, old_token)
    if not old_refresh:
        return None
    
    # Révoquer l'ancien token
    old_refresh.revoked_at = utcnow()
    
    # Créer un nouveau token
    new_token = await create_refresh_token(
        db,
        user_id=old_refresh.user_id,
        service_account_id=old_refresh.service_account_id,
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    # Récupérer le nouveau token object
    new_refresh = await verify_refresh_token(db, new_token)
    
    await db.commit()
    return new_token, new_refresh


async def revoke_token(
    db: AsyncSession,
    jti: str,
    expires_at: datetime,
    reason: str = "logout"
) -> None:
    """Ajoute un token à la liste de révocation"""
    revoked = RevokedToken(
        jti=jti,
        expires_at=expires_at,
        reason=reason
    )
    db.add(revoked)
    await db.commit()


async def is_token_revoked(db: AsyncSession, jti: str) -> bool:
    """Vérifie si un token est dans la liste de révocation"""
    result = await db.execute(
        select(RevokedToken).where(RevokedToken.jti == jti)
    )
    return result.scalar_one_or_none() is not None


async def cleanup_expired_tokens(db: AsyncSession) -> int:
    """Nettoie les tokens expirés (à appeler périodiquement)"""
    now = utcnow()
    
    # Supprimer les refresh tokens expirés
    result1 = await db.execute(
        delete(RefreshToken).where(RefreshToken.expires_at < now)
    )
    
    # Supprimer les tokens révoqués expirés
    result2 = await db.execute(
        delete(RevokedToken).where(RevokedToken.expires_at < now)
    )
    
    await db.commit()
    return result1.rowcount + result2.rowcount


async def revoke_all_user_tokens(db: AsyncSession, user_id: int) -> None:
    """Révoque tous les refresh tokens d'un utilisateur"""
    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    )
    for token in result.scalars():
        token.revoked_at = utcnow()
    await db.commit()


def verify_token(token: str) -> dict:
    """Vérifie un token JWT avec la clé publique RSA"""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expiré"
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide"
        )

    if payload.get("scope") == "pwd_reset":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide"
        )

    return payload


async def verify_token_not_revoked(
    token: str,
    db: AsyncSession
) -> dict:
    """Vérifie un token JWT et qu'il n'est pas révoqué"""
    payload = verify_token(token)
    
    jti = payload.get("jti")
    if jti and await is_token_revoked(db, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token révoqué"
        )
    
    return payload


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Récupère l'utilisateur actuel à partir du token"""
    payload = await verify_token_not_revoked(token, db)
    username = payload.get("sub")
    
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide"
        )
    
    # Vérifier si c'est un service account
    if payload.get("type") == "service_account":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Endpoint réservé aux utilisateurs"
        )
    
    result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
        .where(User.username == username)
    )
    user = result.scalar_one_or_none()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur non trouvé"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Utilisateur désactivé"
        )
    
    return user


async def get_current_entity(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> Union[User, ServiceAccount]:
    """Récupère l'utilisateur OU le service account à partir du token"""
    payload = await verify_token_not_revoked(token, db)
    identifier = payload.get("sub")
    
    if identifier is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide"
        )
    
    if payload.get("type") == "service_account":
        result = await db.execute(
            select(ServiceAccount)
            .options(selectinload(ServiceAccount.applications))
            .where(ServiceAccount.client_id == identifier)
        )
        entity = result.scalar_one_or_none()
        if not entity or not entity.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Service account non trouvé ou désactivé"
            )
    else:
        result = await db.execute(
            select(User)
            .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
            .where(User.username == identifier)
        )
        entity = result.scalar_one_or_none()
        if not entity or not entity.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Utilisateur non trouvé ou désactivé"
            )
    
    return entity


async def get_current_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """Vérifie que l'utilisateur est admin"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès administrateur requis"
        )
    return current_user
