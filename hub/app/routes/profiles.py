from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload
from typing import List

from app.database import get_db
from app.routes.events import notify_event, notify_stats_update
from app.models import Profile, ProfileAppRole, User, UserAppRole, Application
from app.schemas import (
    ProfileCreate, ProfileUpdate, ProfileResponse, ProfileAppRoleResponse,
    ProfileBriefResponse, ProfileApplyRequest
)
from app.auth import get_current_admin

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profiles", tags=["Profiles"])


async def _sync_user_profile_roles(db: AsyncSession, user_id: int, profile_app_roles):
    """
    Synchronise les rôles source='profile' d'un utilisateur avec les app_roles du profil.
    Préserve les rôles source='manual' ajoutés individuellement.
    """
    # Supprimer uniquement les rôles provenant du profil
    await db.execute(
        delete(UserAppRole).where(
            UserAppRole.user_id == user_id,
            UserAppRole.source == "profile"
        )
    )
    # Recréer les rôles du profil
    for par in profile_app_roles:
        uar = UserAppRole(
            user_id=user_id,
            app_id=par.app_id,
            role=par.role,
            source="profile"
        )
        db.add(uar)


def _build_profile_response(profile, user_count: int = 0) -> ProfileResponse:
    """Construit une ProfileResponse depuis un ORM Profile"""
    app_roles = []
    try:
        for par in profile.app_roles:
            app_roles.append(ProfileAppRoleResponse(
                app_id=par.app_id,
                app_name=par.application.name,
                app_slug=par.application.slug,
                role=par.role
            ))
    except Exception:
        pass  # app_roles non chargés

    return ProfileResponse(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        color=profile.color or "#6c757d",
        app_roles=app_roles,
        user_count=user_count,
        created_at=profile.created_at,
        updated_at=profile.updated_at
    )


@router.get("/")
async def list_profiles(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Liste tous les profils avec le nombre d'utilisateurs assignés"""
    # Charger les profils avec leurs app_roles
    result = await db.execute(
        select(Profile).options(
            selectinload(Profile.app_roles).selectinload(ProfileAppRole.application)
        ).order_by(Profile.name)
    )
    profiles = result.scalars().all()

    # Compter les users par profil
    count_result = await db.execute(
        select(User.profile_id, func.count(User.id))
        .where(User.profile_id.isnot(None))
        .group_by(User.profile_id)
    )
    user_counts = dict(count_result.all())

    return [
        _build_profile_response(p, user_counts.get(p.id, 0))
        for p in profiles
    ]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_profile(
    profile_data: ProfileCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Crée un nouveau profil avec ses app_roles"""
    # Vérifier l'unicité du nom
    existing = await db.execute(
        select(Profile).where(Profile.name == profile_data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Un profil avec le nom '{profile_data.name}' existe déjà"
        )

    # Vérifier que toutes les apps existent
    if profile_data.app_roles:
        app_ids = [ar.app_id for ar in profile_data.app_roles]
        result = await db.execute(
            select(Application.id).where(Application.id.in_(app_ids))
        )
        existing_ids = {row[0] for row in result.all()}
        missing = set(app_ids) - existing_ids
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Applications introuvables: {missing}"
            )

    profile = Profile(
        name=profile_data.name,
        description=profile_data.description,
        color=profile_data.color
    )
    db.add(profile)
    await db.flush()  # Pour obtenir l'ID

    # Créer les app_roles
    for ar in profile_data.app_roles:
        par = ProfileAppRole(
            profile_id=profile.id,
            app_id=ar.app_id,
            role=ar.role
        )
        db.add(par)

    await db.commit()

    # Recharger avec relations
    result = await db.execute(
        select(Profile).options(
            selectinload(Profile.app_roles).selectinload(ProfileAppRole.application)
        ).where(Profile.id == profile.id)
    )
    profile = result.scalar_one()

    notify_event("profiles_updated", {"action": "created", "profile_id": profile.id, "name": profile.name})
    logger.info(f"✅ Profil créé: {profile.name} ({len(profile_data.app_roles)} apps)")

    return _build_profile_response(profile, 0)


@router.get("/{profile_id}")
async def get_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Récupère un profil avec ses détails"""
    result = await db.execute(
        select(Profile).options(
            selectinload(Profile.app_roles).selectinload(ProfileAppRole.application)
        ).where(Profile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil non trouvé")

    # Compter les users
    count_result = await db.execute(
        select(func.count(User.id)).where(User.profile_id == profile_id)
    )
    user_count = count_result.scalar() or 0

    return _build_profile_response(profile, user_count)


@router.put("/{profile_id}")
async def update_profile(
    profile_id: int,
    profile_data: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Met à jour un profil"""
    result = await db.execute(
        select(Profile).options(
            selectinload(Profile.app_roles).selectinload(ProfileAppRole.application)
        ).where(Profile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil non trouvé")

    # Vérifier l'unicité du nom si modifié
    if profile_data.name and profile_data.name != profile.name:
        existing = await db.execute(
            select(Profile).where(Profile.name == profile_data.name)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Un profil avec le nom '{profile_data.name}' existe déjà"
            )
        profile.name = profile_data.name

    if profile_data.description is not None:
        profile.description = profile_data.description
    if profile_data.color is not None:
        profile.color = profile_data.color

    # Mettre à jour les app_roles si fournis
    if profile_data.app_roles is not None:
        # Vérifier que toutes les apps existent
        app_ids = [ar.app_id for ar in profile_data.app_roles]
        if app_ids:
            result = await db.execute(
                select(Application.id).where(Application.id.in_(app_ids))
            )
            existing_ids = {row[0] for row in result.all()}
            missing = set(app_ids) - existing_ids
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Applications introuvables: {missing}"
                )

        # Supprimer les anciens app_roles
        await db.execute(
            delete(ProfileAppRole).where(ProfileAppRole.profile_id == profile_id)
        )
        # Recréer
        for ar in profile_data.app_roles:
            par = ProfileAppRole(
                profile_id=profile_id,
                app_id=ar.app_id,
                role=ar.role
            )
            db.add(par)

        # Auto-sync : propager les changements aux utilisateurs ayant ce profil
        await db.flush()  # S'assurer que les nouveaux ProfileAppRole ont un ID
        result = await db.execute(
            select(Profile).options(
                selectinload(Profile.app_roles)
            ).where(Profile.id == profile_id)
        )
        updated_profile = result.scalar_one()

        users_result = await db.execute(
            select(User).where(User.profile_id == profile_id)
        )
        profile_users = users_result.scalars().all()

        for user in profile_users:
            await _sync_user_profile_roles(db, user.id, updated_profile.app_roles)

        if profile_users:
            for user in profile_users:
                notify_event("user_permissions_changed", {
                    "user_id": user.id,
                    "username": user.username,
                    "profile_id": profile_id,
                    "action": "auto_sync"
                })
            logger.info(f"🔄 Auto-sync profil '{profile_data.name or profile.name}' → {len(profile_users)} utilisateur(s)")

    await db.commit()

    # Recharger
    result = await db.execute(
        select(Profile).options(
            selectinload(Profile.app_roles).selectinload(ProfileAppRole.application)
        ).where(Profile.id == profile_id)
    )
    profile = result.scalar_one()

    count_result = await db.execute(
        select(func.count(User.id)).where(User.profile_id == profile_id)
    )
    user_count = count_result.scalar() or 0

    notify_event("profiles_updated", {"action": "updated", "profile_id": profile.id, "name": profile.name})
    logger.info(f"✅ Profil modifié: {profile.name}")

    return _build_profile_response(profile, user_count)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Supprime un profil. Supprime les rôles source='profile' des utilisateurs concernés, préserve les manuels."""
    result = await db.execute(
        select(Profile).where(Profile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil non trouvé")

    profile_name = profile.name

    # Supprimer les rôles source='profile' des utilisateurs ayant ce profil
    users_result = await db.execute(
        select(User).where(User.profile_id == profile_id)
    )
    users = users_result.scalars().all()
    for user in users:
        await db.execute(
            delete(UserAppRole).where(
                UserAppRole.user_id == user.id,
                UserAppRole.source == "profile"
            )
        )

    await db.delete(profile)
    await db.commit()

    for user in users:
        notify_event("user_permissions_changed", {
            "user_id": user.id,
            "username": user.username,
            "action": "profile_deleted"
        })

    notify_event("profiles_updated", {"action": "deleted", "profile_id": profile_id, "name": profile_name})
    logger.info(f"🗑️ Profil supprimé: {profile_name}")


@router.post("/{profile_id}/apply")
async def apply_profile(
    profile_id: int,
    request: ProfileApplyRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """
    Applique un profil à un ou plusieurs utilisateurs.
    - Assigne profile_id sur les users
    - Si sync_roles=True : synchronise les user_app_roles source='profile'
      (préserve les rôles ajoutés manuellement par l'admin)
    """
    # Charger le profil avec ses app_roles
    result = await db.execute(
        select(Profile).options(
            selectinload(Profile.app_roles)
        ).where(Profile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil non trouvé")

    # Charger les users
    result = await db.execute(
        select(User).options(
            selectinload(User.app_roles)
        ).where(User.id.in_(request.user_ids))
    )
    users = result.scalars().all()

    if not users:
        raise HTTPException(status_code=404, detail="Aucun utilisateur trouvé")

    updated_count = 0
    for user in users:
        user.profile_id = profile_id

        if request.sync_roles:
            await _sync_user_profile_roles(db, user.id, profile.app_roles)

        updated_count += 1

    await db.commit()

    # Notifier les changements
    for user in users:
        notify_event("user_permissions_changed", {
            "user_id": user.id,
            "username": user.username,
            "profile_id": profile_id,
            "profile_name": profile.name
        })

    notify_stats_update()
    logger.info(f"✅ Profil '{profile.name}' appliqué à {updated_count} utilisateur(s)")

    return {
        "message": f"Profil '{profile.name}' appliqué à {updated_count} utilisateur(s)",
        "updated_count": updated_count,
        "profile_id": profile_id,
        "profile_name": profile.name
    }


@router.post("/{profile_id}/sync")
async def sync_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Re-synchronise tous les utilisateurs du profil.
    Utile après modification des app_roles du profil.
    Ne touche qu'aux rôles source='profile', préserve les rôles manuels.
    """
    # Charger le profil avec ses app_roles
    result = await db.execute(
        select(Profile).options(
            selectinload(Profile.app_roles)
        ).where(Profile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil non trouvé")

    # Charger tous les users avec ce profil
    result = await db.execute(
        select(User).where(User.profile_id == profile_id)
    )
    users = result.scalars().all()

    if not users:
        return {
            "message": f"Aucun utilisateur n'a le profil '{profile.name}'",
            "synced_count": 0
        }

    synced_count = 0
    for user in users:
        await _sync_user_profile_roles(db, user.id, profile.app_roles)
        synced_count += 1

    await db.commit()

    for user in users:
        notify_event("user_permissions_changed", {
            "user_id": user.id,
            "username": user.username,
            "profile_id": profile_id,
            "profile_name": profile.name,
            "action": "sync"
        })

    notify_stats_update()
    logger.info(f"🔄 Profil '{profile.name}' synchronisé pour {synced_count} utilisateur(s)")

    return {
        "message": f"Profil '{profile.name}' synchronisé pour {synced_count} utilisateur(s)",
        "synced_count": synced_count
    }


@router.post("/{profile_id}/detach")
async def detach_profile(
    profile_id: int,
    request: ProfileApplyRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Détache le profil des utilisateurs spécifiés (profile_id → NULL).
    Supprime les rôles source='profile' de ces utilisateurs.
    Les rôles manuels sont préservés.
    """
    result = await db.execute(
        select(User).where(
            User.id.in_(request.user_ids),
            User.profile_id == profile_id
        )
    )
    users = result.scalars().all()

    for user in users:
        user.profile_id = None
        # Supprimer les rôles provenant du profil
        await db.execute(
            delete(UserAppRole).where(
                UserAppRole.user_id == user.id,
                UserAppRole.source == "profile"
            )
        )

    await db.commit()

    for user in users:
        notify_event("user_permissions_changed", {
            "user_id": user.id,
            "username": user.username,
            "action": "profile_detached"
        })

    return {
        "message": f"Profil détaché de {len(users)} utilisateur(s)",
        "detached_count": len(users)
    }
