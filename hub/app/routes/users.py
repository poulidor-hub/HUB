from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, or_
from sqlalchemy.orm import selectinload
from typing import List, Optional
from pydantic import BaseModel
import math

from app.database import get_db
from app.routes.events import notify_event, notify_stats_update
from app.models import (
    User, Application, UserAppRole,
    AppRole, DEFAULT_ROLES, Profile
)
from app.schemas import (
    UserCreate, UserUpdate, UserResponse, PaginatedUsersResponse,
    BulkUsersCreateRequest, BulkUsersCreateResponse, BulkUserCreateResult,
    BulkUsersRolesUpdateRequest, BulkUsersRolesUpdateResponse, BulkUserRoleUpdateResult,
    BulkUsersDeleteRequest, BulkUsersDeleteResponse, BulkUserDeleteResult,
    BulkAppRolesAssignRequest, BulkAppRolesAssignResponse, BulkAppRoleAssignResult,
    BulkRemoveAppAccessRequest, BulkRemoveAppAccessResponse, BulkRemoveAppAccessResult
)
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
    page: int = Query(default=0, ge=0, description="Page (0 = pas de pagination, retourne tout)"),
    per_page: int = Query(default=50, ge=10, le=200),
    search: Optional[str] = Query(default=None, description="Recherche dans username, email, noms d'apps"),
    role: Optional[str] = Query(default=None, description="Filtrer par rôle app (admin, manager, user, viewer)"),
    app_slug: Optional[str] = Query(default=None, description="Filtrer par slug d'application"),
    profile_id: Optional[int] = Query(default=None, description="Filtrer par profil"),
    is_active: Optional[bool] = Query(default=None, description="Filtrer par statut actif"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Liste les utilisateurs avec pagination et filtres.
    - page=0 : rétro-compatible, retourne TOUS les utilisateurs (List[UserResponse])
    - page>=1 : pagination serveur avec filtres, retourne PaginatedUsersResponse
    """
    # Requête de base avec options de chargement
    load_options = [
        selectinload(User.app_roles).selectinload(UserAppRole.application),
        selectinload(User.profile)
    ]

    # --- Mode rétro-compatible (page=0) : tout retourner ---
    if page == 0:
        result = await db.execute(
            select(User).options(*load_options)
        )
        users = result.scalars().all()
        return [UserResponse.from_orm_model(u) for u in users]

    # --- Mode paginé (page >= 1) ---
    # Construire la requête avec filtres
    base_query = select(User)

    # Filtre par statut actif
    if is_active is not None:
        base_query = base_query.where(User.is_active == is_active)

    # Filtre par profil
    if profile_id is not None:
        if profile_id == -1:
            # -1 = utilisateurs sans profil
            base_query = base_query.where(User.profile_id.is_(None))
        else:
            base_query = base_query.where(User.profile_id == profile_id)

    # Filtre par app_slug et/ou rôle (nécessite un join)
    if app_slug or role:
        base_query = base_query.join(UserAppRole, User.id == UserAppRole.user_id)
        if app_slug:
            base_query = base_query.join(
                Application, UserAppRole.app_id == Application.id
            ).where(Application.slug == app_slug)
        if role:
            base_query = base_query.where(UserAppRole.role == role)
        # Distinct car le join peut dupliquer les users
        base_query = base_query.distinct()

    # Recherche texte (username, email, noms d'apps)
    if search:
        search_term = f"%{search}%"
        # Sous-requête pour les users qui matchent par app_name
        app_match_subq = (
            select(UserAppRole.user_id)
            .join(Application, UserAppRole.app_id == Application.id)
            .where(Application.name.ilike(search_term))
        ).scalar_subquery()
        
        base_query = base_query.where(
            or_(
                User.username.ilike(search_term),
                User.email.ilike(search_term),
                User.id.in_(app_match_subq)
            )
        )

    # Compter le total
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    pages = max(1, math.ceil(total / per_page))

    # Paginer et charger
    paginated_query = (
        base_query
        .options(*load_options)
        .order_by(User.username)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(paginated_query)
    users = result.scalars().unique().all()

    return PaginatedUsersResponse(
        users=[UserResponse.from_orm_model(u) for u in users],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages
    )


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
        must_change_password=user_data.must_change_password,
        can_change_password=user_data.can_change_password
    )
    db.add(user)
    await db.commit()
    
    # Recharger avec les relations (vide pour un nouvel utilisateur)
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profile)
        )
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
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profile)
        )
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
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profile)
        )
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
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profile)
        )
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
    
    # Notifier SSE que les droits de l'utilisateur ont changé
    # Permet au frontend de rafraîchir automatiquement le token
    notify_event("user_permissions_changed", {
        "user_id": user_id,
        "username": user.username,
        "apps_count": len(result_assignments)
    })
    
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
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profile)
        )
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


# ===== Opérations en masse (Bulk Operations) =====

@router.post("/bulk/create", response_model=BulkUsersCreateResponse)
async def bulk_create_users(
    request: BulkUsersCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Crée plusieurs utilisateurs en une seule opération.
    
    Chaque utilisateur peut avoir des rôles sur des applications assignés directement.
    Les erreurs sur un utilisateur n'empêchent pas la création des autres.
    
    Exemple:
    ```json
    {
        "users": [
            {
                "username": "user1",
                "email": "user1@example.com",
                "password": "password123",
                "is_admin": false,
                "app_roles": [
                    {"app_id": 1, "role": "user"},
                    {"app_id": 2, "role": "viewer"}
                ]
            },
            {
                "username": "user2",
                "password": "password456"
            }
        ]
    }
    ```
    """
    results = []
    created_count = 0
    
    for user_data in request.users:
        try:
            # Vérifier si l'utilisateur existe déjà
            existing = await db.execute(
                select(User).where(User.username == user_data.username)
            )
            if existing.scalar_one_or_none():
                results.append(BulkUserCreateResult(
                    username=user_data.username,
                    success=False,
                    error="Ce nom d'utilisateur existe déjà"
                ))
                continue
            
            # Vérifier l'email si fourni
            if user_data.email:
                existing_email = await db.execute(
                    select(User).where(User.email == user_data.email)
                )
                if existing_email.scalar_one_or_none():
                    results.append(BulkUserCreateResult(
                        username=user_data.username,
                        success=False,
                        error="Cet email est déjà utilisé"
                    ))
                    continue
            
            # Vérifier le profil si fourni
            profile = None
            if user_data.profile_id:
                profile_result = await db.execute(
                    select(Profile)
                    .options(
                        selectinload(Profile.app_roles)
                    )
                    .where(
                        Profile.id == user_data.profile_id
                    )
                )
                profile = profile_result.scalar_one_or_none()

            # Créer l'utilisateur
            user = User(
                username=user_data.username,
                email=user_data.email,
                hashed_password=get_password_hash(
                    user_data.password
                ),
                is_admin=user_data.is_admin,
                is_active=user_data.is_active,
                must_change_password=(
                    user_data.must_change_password
                ),
                can_change_password=(
                    user_data.can_change_password
                ),
                profile_id=(
                    profile.id if profile else None
                )
            )
            db.add(user)
            await db.flush()  # Pour obtenir l'ID

            # Construire la map des rôles finaux
            # Profil d'abord, puis app_roles manuels
            # (les manuels écrasent le profil par app)
            final_roles = {}
            if profile:
                for par in profile.app_roles:
                    final_roles[par.app_id] = par.role
            if user_data.app_roles:
                for ar in user_data.app_roles:
                    final_roles[ar.app_id] = ar.role

            # Vérifier que les apps existent
            if final_roles:
                apps_result = await db.execute(
                    select(Application).where(
                        Application.id.in_(
                            list(final_roles.keys())
                        )
                    )
                )
                existing_apps = {
                    app.id
                    for app in apps_result.scalars().all()
                }
                for app_id, role in final_roles.items():
                    if app_id in existing_apps:
                        db.add(UserAppRole(
                            user_id=user.id,
                            app_id=app_id,
                            role=role
                        ))
            
            results.append(BulkUserCreateResult(
                username=user_data.username,
                success=True,
                user_id=user.id
            ))
            created_count += 1
            
        except Exception as e:
            results.append(BulkUserCreateResult(
                username=user_data.username,
                success=False,
                error=str(e)
            ))
    
    await db.commit()
    
    # Notifier les clients SSE
    if created_count > 0:
        notify_event("users_updated", {"action": "bulk_created", "count": created_count})
        notify_stats_update()
    
    return BulkUsersCreateResponse(
        total_requested=len(request.users),
        total_created=created_count,
        total_failed=len(request.users) - created_count,
        results=results
    )


@router.post("/bulk/roles", response_model=BulkUsersRolesUpdateResponse)
async def bulk_update_user_roles(
    request: BulkUsersRolesUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Met à jour les rôles de plusieurs utilisateurs en une seule opération.
    
    Remplace TOUS les rôles existants de chaque utilisateur par les nouveaux.
    
    Exemple:
    ```json
    {
        "updates": [
            {
                "user_id": 1,
                "app_roles": [
                    {"app_id": 1, "role": "admin"},
                    {"app_id": 2, "role": "user"}
                ]
            },
            {
                "user_id": 2,
                "app_roles": [
                    {"app_id": 1, "role": "viewer"}
                ]
            }
        ]
    }
    ```
    """
    results = []
    updated_count = 0
    
    for update in request.updates:
        try:
            # Vérifier que l'utilisateur existe
            result = await db.execute(
                select(User).where(User.id == update.user_id)
            )
            user = result.scalar_one_or_none()
            
            if not user:
                results.append(BulkUserRoleUpdateResult(
                    user_id=update.user_id,
                    username="inconnu",
                    success=False,
                    error="Utilisateur non trouvé"
                ))
                continue
            
            # Vérifier que les applications existent
            app_ids = [ar.app_id for ar in update.app_roles]
            if app_ids:
                apps_result = await db.execute(
                    select(Application).where(Application.id.in_(app_ids))
                )
                existing_apps = {app.id for app in apps_result.scalars().all()}
                
                missing_apps = set(app_ids) - existing_apps
                if missing_apps:
                    results.append(BulkUserRoleUpdateResult(
                        user_id=update.user_id,
                        username=user.username,
                        success=False,
                        error=f"Applications non trouvées: {missing_apps}"
                    ))
                    continue
            
            # Supprimer les anciens rôles
            await db.execute(
                delete(UserAppRole).where(UserAppRole.user_id == update.user_id)
            )
            
            # Créer les nouveaux rôles
            for ar in update.app_roles:
                user_app_role = UserAppRole(
                    user_id=update.user_id,
                    app_id=ar.app_id,
                    role=ar.role
                )
                db.add(user_app_role)
            
            results.append(BulkUserRoleUpdateResult(
                user_id=update.user_id,
                username=user.username,
                success=True,
                roles_updated=len(update.app_roles)
            ))
            updated_count += 1
            
            # Notifier le changement de permissions pour cet utilisateur
            notify_event("user_permissions_changed", {
                "user_id": update.user_id,
                "username": user.username,
                "apps_count": len(update.app_roles)
            })
            
        except Exception as e:
            results.append(BulkUserRoleUpdateResult(
                user_id=update.user_id,
                username="erreur",
                success=False,
                error=str(e)
            ))
    
    await db.commit()
    
    if updated_count > 0:
        notify_event("users_updated", {"action": "bulk_roles_updated", "count": updated_count})
    
    return BulkUsersRolesUpdateResponse(
        total_requested=len(request.updates),
        total_updated=updated_count,
        total_failed=len(request.updates) - updated_count,
        results=results
    )


@router.post("/bulk/delete", response_model=BulkUsersDeleteResponse)
async def bulk_delete_users(
    request: BulkUsersDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Supprime plusieurs utilisateurs en une seule opération.
    
    ⚠️ ATTENTION: Cette opération est irréversible !
    Le champ "confirm" doit être à true pour valider la suppression.
    
    L'admin connecté ne peut pas se supprimer lui-même.
    
    Exemple:
    ```json
    {
        "user_ids": [2, 3, 4],
        "confirm": true
    }
    ```
    """
    if not request.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous devez confirmer la suppression (confirm: true)"
        )
    
    results = []
    deleted_count = 0
    
    for user_id in request.user_ids:
        try:
            # Vérifier qu'on ne se supprime pas soi-même
            if user_id == current_user.id:
                results.append(BulkUserDeleteResult(
                    user_id=user_id,
                    username=current_user.username,
                    success=False,
                    error="Vous ne pouvez pas vous supprimer vous-même"
                ))
                continue
            
            # Vérifier que l'utilisateur existe
            result = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            
            if not user:
                results.append(BulkUserDeleteResult(
                    user_id=user_id,
                    username="inconnu",
                    success=False,
                    error="Utilisateur non trouvé"
                ))
                continue
            
            username = user.username
            await db.delete(user)
            
            results.append(BulkUserDeleteResult(
                user_id=user_id,
                username=username,
                success=True
            ))
            deleted_count += 1
            
        except Exception as e:
            results.append(BulkUserDeleteResult(
                user_id=user_id,
                username="erreur",
                success=False,
                error=str(e)
            ))
    
    await db.commit()
    
    if deleted_count > 0:
        notify_event("users_updated", {"action": "bulk_deleted", "count": deleted_count})
        notify_stats_update()
    
    return BulkUsersDeleteResponse(
        total_requested=len(request.user_ids),
        total_deleted=deleted_count,
        total_failed=len(request.user_ids) - deleted_count,
        results=results
    )


@router.post("/bulk/assign-app-roles", response_model=BulkAppRolesAssignResponse)
async def bulk_assign_app_roles(
    request: BulkAppRolesAssignRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Assigne un rôle à plusieurs utilisateurs sur des applications.
    
    Permet d'ajouter ou modifier le rôle de plusieurs utilisateurs sur une ou plusieurs apps.
    
    Exemple:
    ```json
    {
        "assignments": [
            {
                "app_id": 1,
                "user_ids": [2, 3, 4],
                "role": "user"
            },
            {
                "app_id": 2,
                "user_ids": [2, 5],
                "role": "admin"
            }
        ]
    }
    ```
    """
    results = []
    success_count = 0
    
    # Pré-charger toutes les applications et utilisateurs nécessaires
    all_app_ids = {a.app_id for a in request.assignments}
    all_user_ids = set()
    for a in request.assignments:
        all_user_ids.update(a.user_ids)
    
    apps_result = await db.execute(
        select(Application).where(Application.id.in_(all_app_ids))
    )
    apps_map = {app.id: app for app in apps_result.scalars().all()}
    
    users_result = await db.execute(
        select(User).where(User.id.in_(all_user_ids))
    )
    users_map = {user.id: user for user in users_result.scalars().all()}
    
    for assignment in request.assignments:
        app = apps_map.get(assignment.app_id)
        if not app:
            for user_id in assignment.user_ids:
                results.append(BulkAppRoleAssignResult(
                    app_id=assignment.app_id,
                    app_name="inconnu",
                    user_id=user_id,
                    username=users_map.get(user_id, User()).username if users_map.get(user_id) else "inconnu",
                    role=assignment.role,
                    success=False,
                    error=f"Application {assignment.app_id} non trouvée"
                ))
            continue
        
        for user_id in assignment.user_ids:
            user = users_map.get(user_id)
            if not user:
                results.append(BulkAppRoleAssignResult(
                    app_id=assignment.app_id,
                    app_name=app.name,
                    user_id=user_id,
                    username="inconnu",
                    role=assignment.role,
                    success=False,
                    error=f"Utilisateur {user_id} non trouvé"
                ))
                continue
            
            try:
                # Chercher un rôle existant
                existing_role = await db.execute(
                    select(UserAppRole).where(
                        UserAppRole.user_id == user_id,
                        UserAppRole.app_id == assignment.app_id
                    )
                )
                existing = existing_role.scalar_one_or_none()
                
                if existing:
                    if existing.role == assignment.role:
                        results.append(BulkAppRoleAssignResult(
                            app_id=assignment.app_id,
                            app_name=app.name,
                            user_id=user_id,
                            username=user.username,
                            role=assignment.role,
                            success=True,
                            action="unchanged"
                        ))
                    else:
                        existing.role = assignment.role
                        results.append(BulkAppRoleAssignResult(
                            app_id=assignment.app_id,
                            app_name=app.name,
                            user_id=user_id,
                            username=user.username,
                            role=assignment.role,
                            success=True,
                            action="updated"
                        ))
                        success_count += 1
                else:
                    # Créer un nouveau rôle
                    new_role = UserAppRole(
                        user_id=user_id,
                        app_id=assignment.app_id,
                        role=assignment.role
                    )
                    db.add(new_role)
                    results.append(BulkAppRoleAssignResult(
                        app_id=assignment.app_id,
                        app_name=app.name,
                        user_id=user_id,
                        username=user.username,
                        role=assignment.role,
                        success=True,
                        action="created"
                    ))
                    success_count += 1
                
                # Notifier le changement de permissions
                notify_event("user_permissions_changed", {
                    "user_id": user_id,
                    "username": user.username
                })
                
            except Exception as e:
                results.append(BulkAppRoleAssignResult(
                    app_id=assignment.app_id,
                    app_name=app.name,
                    user_id=user_id,
                    username=user.username,
                    role=assignment.role,
                    success=False,
                    error=str(e)
                ))
    
    await db.commit()
    
    if success_count > 0:
        notify_event("users_updated", {"action": "bulk_app_roles_assigned", "count": success_count})
    
    failed_count = len([r for r in results if not r.success])
    return BulkAppRolesAssignResponse(
        total_assignments=len(results),
        total_success=len(results) - failed_count,
        total_failed=failed_count,
        results=results
    )


@router.post("/bulk/remove-app-access", response_model=BulkRemoveAppAccessResponse)
async def bulk_remove_app_access(
    request: BulkRemoveAppAccessRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Supprime l'accès à des applications pour plusieurs utilisateurs.
    
    Exemple:
    ```json
    {
        "removals": [
            {
                "app_id": 1,
                "user_ids": [2, 3, 4]
            },
            {
                "app_id": 2,
                "user_ids": [2, 5]
            }
        ]
    }
    ```
    """
    results = []
    success_count = 0
    
    # Pré-charger toutes les applications et utilisateurs nécessaires
    all_app_ids = {r.app_id for r in request.removals}
    all_user_ids = set()
    for r in request.removals:
        all_user_ids.update(r.user_ids)
    
    apps_result = await db.execute(
        select(Application).where(Application.id.in_(all_app_ids))
    )
    apps_map = {app.id: app for app in apps_result.scalars().all()}
    
    users_result = await db.execute(
        select(User).where(User.id.in_(all_user_ids))
    )
    users_map = {user.id: user for user in users_result.scalars().all()}
    
    for removal in request.removals:
        app = apps_map.get(removal.app_id)
        if not app:
            for user_id in removal.user_ids:
                results.append(BulkRemoveAppAccessResult(
                    app_id=removal.app_id,
                    app_name="inconnu",
                    user_id=user_id,
                    username=users_map.get(user_id).username if users_map.get(user_id) else "inconnu",
                    success=False,
                    error=f"Application {removal.app_id} non trouvée"
                ))
            continue
        
        for user_id in removal.user_ids:
            user = users_map.get(user_id)
            if not user:
                results.append(BulkRemoveAppAccessResult(
                    app_id=removal.app_id,
                    app_name=app.name,
                    user_id=user_id,
                    username="inconnu",
                    success=False,
                    error=f"Utilisateur {user_id} non trouvé"
                ))
                continue
            
            try:
                # Supprimer le rôle
                delete_result = await db.execute(
                    delete(UserAppRole).where(
                        UserAppRole.user_id == user_id,
                        UserAppRole.app_id == removal.app_id
                    )
                )
                
                results.append(BulkRemoveAppAccessResult(
                    app_id=removal.app_id,
                    app_name=app.name,
                    user_id=user_id,
                    username=user.username,
                    success=True
                ))
                success_count += 1
                
                # Notifier le changement de permissions
                notify_event("user_permissions_changed", {
                    "user_id": user_id,
                    "username": user.username
                })
                
            except Exception as e:
                results.append(BulkRemoveAppAccessResult(
                    app_id=removal.app_id,
                    app_name=app.name,
                    user_id=user_id,
                    username=user.username,
                    success=False,
                    error=str(e)
                ))
    
    await db.commit()
    
    if success_count > 0:
        notify_event("users_updated", {"action": "bulk_app_access_removed", "count": success_count})
    
    failed_count = len([r for r in results if not r.success])
    return BulkRemoveAppAccessResponse(
        total_removals=len(results),
        total_success=len(results) - failed_count,
        total_failed=failed_count,
        results=results
    )
