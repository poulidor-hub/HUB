from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from typing import List
from pydantic import BaseModel

from app.database import get_db
from app.routes.events import notify_event, notify_stats_update
from app.models import User, Application, UserAppRole, AppRole, DEFAULT_ROLES
from app.schemas import UserCreate, UserUpdate, UserResponse
from app.auth import get_current_admin, get_password_hash, revoke_all_user_tokens

router = APIRouter(prefix="/api/users", tags=["Users"])


class UserAppRoleAssignment(BaseModel):
    """Attribution d'un rôle à un utilisateur pour une application"""
    app_id: int
    role: str = "user"


class UserAppsUpdate(BaseModel):
    """Liste des applications avec rôles pour un utilisateur"""
    assignments: List[UserAppRoleAssignment]


@router.get("/")
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Liste tous les utilisateurs (admin uniquement)"""
    result = await db.execute(
        select(User).options(
            selectinload(User.app_roles).selectinload(UserAppRole.application)
        )
    )
    users = result.scalars().all()
    return [UserResponse.from_orm_model(u) for u in users]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Crée un nouvel utilisateur (admin uniquement)"""
    # Vérifier si l'utilisateur existe
    existing = await db.execute(
        select(User).where(User.username == user_data.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce nom d'utilisateur existe déjà"
        )
    
    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),
        is_admin=user_data.is_admin,
        must_change_password=user_data.must_change_password
    )
    db.add(user)
    await db.commit()
    
    # Recharger avec les relations (vide pour un nouvel utilisateur)
    result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
        .where(User.id == user.id)
    )
    user = result.scalar_one()
    
    # Notifier les clients SSE du nouvel utilisateur
    notify_event("users_updated", {"action": "created", "user_id": user.id, "username": user.username})
    notify_stats_update()
    
    return UserResponse.from_orm_model(user)


@router.get("/{user_id}")
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Récupère un utilisateur par ID (admin uniquement)"""
    result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    return UserResponse.from_orm_model(user)


@router.patch("/{user_id}")
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Met à jour un utilisateur (admin uniquement)"""
    result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    update_data = user_data.model_dump(exclude_unset=True)
    
    # Détecter si l'utilisateur est en train d'être désactivé
    is_being_deactivated = "is_active" in update_data and update_data["is_active"] == False and user.is_active == True
    
    if "password" in update_data:
        update_data["hashed_password"] = get_password_hash(update_data.pop("password"))
    
    for field, value in update_data.items():
        setattr(user, field, value)
    
    await db.commit()
    
    # Si l'utilisateur est désactivé, révoquer tous ses tokens et notifier sa déconnexion forcée
    if is_being_deactivated:
        await revoke_all_user_tokens(db, user_id)
        # Notifier via SSE pour forcer la déconnexion côté client
        notify_event("force_logout", {"user_id": user_id, "username": user.username, "reason": "account_disabled"})
    
    # Recharger avec les relations
    result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
        .where(User.id == user_id)
    )
    user = result.scalar_one()
    
    # Notifier les clients SSE de la modification
    notify_event("users_updated", {"action": "updated", "user_id": user.id, "username": user.username})
    
    return UserResponse.from_orm_model(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Supprime un utilisateur (admin uniquement)"""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas vous supprimer vous-même"
        )
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    username = user.username  # Sauvegarder avant suppression
    await db.delete(user)
    await db.commit()
    
    # Notifier les clients SSE de la suppression
    notify_event("users_updated", {"action": "deleted", "user_id": user_id, "username": username})
    notify_stats_update()


@router.post("/{user_id}/apps")
async def set_user_apps(
    user_id: int,
    updates: UserAppsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Définit les applications et rôles pour un utilisateur.
    
    Exemple de body:
    {
        "assignments": [
            {"app_id": 1, "role": "admin"},
            {"app_id": 2, "role": "user"},
            {"app_id": 3, "role": "viewer"}
        ]
    }
    """
    result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    # Vérifier que les apps existent
    app_ids = [a.app_id for a in updates.assignments]
    apps_result = await db.execute(
        select(Application).where(Application.id.in_(app_ids))
    )
    existing_apps = {app.id: app for app in apps_result.scalars().all()}
    
    for assignment in updates.assignments:
        if assignment.app_id not in existing_apps:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application {assignment.app_id} non trouvée"
            )
    
    # Supprimer les anciens rôles
    await db.execute(
        delete(UserAppRole).where(UserAppRole.user_id == user_id)
    )
    
    # Créer les nouveaux rôles
    result_assignments = []
    for assignment in updates.assignments:
        app = existing_apps[assignment.app_id]
        user_app_role = UserAppRole(
            user_id=user_id,
            app_id=assignment.app_id,
            role=assignment.role
        )
        db.add(user_app_role)
        result_assignments.append({
            "app": app.name,
            "slug": app.slug,
            "role": assignment.role
        })
    
    await db.commit()
    
    return {
        "message": f"Applications mises à jour pour {user.username}",
        "assignments": result_assignments
    }


@router.get("/{user_id}/apps")
async def get_user_apps(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Récupère les applications et rôles d'un utilisateur"""
    result = await db.execute(
        select(User)
        .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    return {
        "user": user.username,
        "apps": [
            {
                "app_id": ar.app_id,
                "app_name": ar.application.name,
                "app_slug": ar.application.slug,
                "role": ar.role
            }
            for ar in user.app_roles
        ]
    }


# === Gestion des rôles disponibles ===

@router.get("/roles/available")
async def get_available_roles():
    """Retourne la liste des rôles disponibles par défaut"""
    return {
        "roles": DEFAULT_ROLES,
        "descriptions": {
            "admin": "Administrateur - Tous les droits sur l'application",
            "manager": "Manager - Gestion des données et utilisateurs de l'app",
            "user": "Utilisateur - Accès standard en lecture/écriture",
            "viewer": "Lecteur - Accès en lecture seule",
            "service": "Service - Accès M2M (service accounts uniquement)"
        }
    }


# === Gestion des sessions utilisateur ===

@router.post("/{user_id}/revoke-sessions")
async def revoke_user_sessions(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """
    Révoque toutes les sessions d'un utilisateur (admin uniquement).
    Force la déconnexion de l'utilisateur sur tous ses appareils.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    # Révoquer tous les tokens
    await revoke_all_user_tokens(db, user_id)
    
    # Notifier via SSE pour forcer la déconnexion côté client
    notify_event("force_logout", {
        "user_id": user_id,
        "username": user.username,
        "reason": "admin_revoked",
        "revoked_by": current_admin.username
    })
    
    notify_stats_update()
    
    return {
        "message": f"Sessions révoquées pour {user.username}",
        "user_id": user_id
    }
