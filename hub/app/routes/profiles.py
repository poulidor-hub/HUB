# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, insert
from sqlalchemy.orm import selectinload
from typing import List

from app.database import get_db
from app.routes.events import notify_event, notify_stats_update
from app.models import (
    Profile, ProfileAppRole, User, UserAppRole, Application,
    user_profiles, highest_role
)
from app.schemas import (
    ProfileCreate, ProfileUpdate, ProfileResponse, ProfileAppRoleResponse,
    ProfileBriefResponse, ProfileApplyRequest, ProfileRemoveUsersRequest
)
from app.auth import get_current_admin

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profiles", tags=["Profiles"])


async def _sync_user_profile_roles(db: AsyncSession, user_id: int):
    """
    Synchronise les rôles source='profile:*' d'un utilisateur avec TOUS ses profils.
    Mode multi-profils : quand plusieurs profils donnent des droits sur la même app,
    le rôle le plus élevé est retenu (admin > manager > user > viewer).

    - Collecte tous les profils de l'utilisateur
    - Pour chaque app, calcule le rôle max entre tous les profils
    - Ajoute/met à jour/supprime les rôles "profile:*"
    - Ne touche JAMAIS aux rôles source='manual'
    - Overrides (source='override:<pid>') : ignorés si le profil d'origine couvre
      encore l'app (la modification admin prime jusqu'à réinitialisation) ;
      supprimés sinon — si un autre profil couvre l'app, la ligne profile:*
      attendue est (re)créée.
    """
    # 1. Récupérer tous les profils de l'utilisateur avec leurs app_roles
    profiles_result = await db.execute(
        select(Profile).options(
            selectinload(Profile.app_roles)
        ).join(user_profiles, user_profiles.c.profile_id == Profile.id)
        .where(user_profiles.c.user_id == user_id)
    )
    user_profiles_list = profiles_result.scalars().all()

    # 2. Calculer le rôle max par app en combinant tous les profils
    # {app_id: {"role": best_role, "profile_id": source_profile_id}}
    best_roles_by_app = {}
    # {app_id: set(profile_ids couvrant l'app)} — pour détecter les overrides périmés
    covering_pids_by_app = {}
    for profile in user_profiles_list:
        for par in profile.app_roles:
            covering_pids_by_app.setdefault(par.app_id, set()).add(profile.id)
            existing = best_roles_by_app.get(par.app_id)
            if existing is None:
                best_roles_by_app[par.app_id] = {
                    "role": par.role,
                    "profile_id": profile.id
                }
            else:
                best = highest_role([existing["role"], par.role])
                if best != existing["role"]:
                    best_roles_by_app[par.app_id] = {
                        "role": best,
                        "profile_id": profile.id
                    }

    # 3. Récupérer les rôles actuels de l'utilisateur (avec verrou pour éviter les races)
    result = await db.execute(
        select(UserAppRole).where(UserAppRole.user_id == user_id)
        .with_for_update()
    )
    current_roles = result.scalars().all()

    # Indexer : séparer manuels, profil et overrides
    manual_app_ids = set()
    profile_roles_by_app = {}   # app_id -> UserAppRole (source='profile:*')
    override_roles_by_app = {}  # app_id -> UserAppRole (source='override:*')
    for role in current_roles:
        if role.source == "manual":
            manual_app_ids.add(role.app_id)
        elif role.source.startswith("profile:"):
            profile_roles_by_app[role.app_id] = role
        elif role.source.startswith("override:"):
            override_roles_by_app[role.app_id] = role

    # 4a. Supprimer les overrides périmés : le profil d'origine ne couvre plus l'app
    #     (profil détaché/supprimé, ou app retirée de la définition du profil)
    stale_override_app_ids = set()
    for app_id, ovr in override_roles_by_app.items():
        try:
            origin_pid = int(ovr.source.split(":", 1)[1])
        except (IndexError, ValueError):
            origin_pid = None  # source illisible → considérer comme périmé
        if origin_pid is None or origin_pid not in covering_pids_by_app.get(app_id, set()):
            stale_override_app_ids.add(app_id)
            await db.delete(ovr)

    # Émettre les DELETE des overrides périmés AVANT les INSERT de l'étape 5 :
    # le flush SQLAlchemy ordonne les INSERT avant les DELETE, ce qui violerait
    # la contrainte unique (user_id, app_id) sur la même app.
    if stale_override_app_ids:
        await db.flush()

    # 4b. Supprimer les rôles profile pour les apps qui ne sont plus dans aucun profil
    apps_to_remove = set(profile_roles_by_app.keys()) - set(best_roles_by_app.keys())
    if apps_to_remove:
        await db.execute(
            delete(UserAppRole).where(
                UserAppRole.user_id == user_id,
                UserAppRole.source.like("profile:%"),
                UserAppRole.app_id.in_(apps_to_remove)
            )
        )

    # 5. Ajouter ou mettre à jour les rôles
    for app_id, best in best_roles_by_app.items():
        # Si l'app est déjà assignée manuellement, ne pas créer de doublon
        if app_id in manual_app_ids:
            continue

        # Override actif : le profil d'origine couvre encore l'app → ne pas toucher
        if app_id in override_roles_by_app and app_id not in stale_override_app_ids:
            continue

        existing = profile_roles_by_app.get(app_id)
        new_source = f"profile:{best['profile_id']}"

        if existing:
            # Mettre à jour si le rôle ou la source a changé
            if existing.role != best["role"] or existing.source != new_source:
                existing.role = best["role"]
                existing.source = new_source
        else:
            # Nouvelle app → ajouter
            uar = UserAppRole(
                user_id=user_id,
                app_id=app_id,
                role=best["role"],
                source=new_source
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

    # Compter les users par profil via la table de jonction
    count_result = await db.execute(
        select(user_profiles.c.profile_id, func.count(user_profiles.c.user_id))
        .group_by(user_profiles.c.profile_id)
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

    # Compter les users via la table de jonction
    count_result = await db.execute(
        select(func.count(user_profiles.c.user_id)).where(user_profiles.c.profile_id == profile_id)
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

        users_result = await db.execute(
            select(User).join(user_profiles, user_profiles.c.user_id == User.id)
            .where(user_profiles.c.profile_id == profile_id)
        )
        profile_users = users_result.scalars().all()

        for user in profile_users:
            await _sync_user_profile_roles(db, user.id)

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
        select(func.count(user_profiles.c.user_id)).where(user_profiles.c.profile_id == profile_id)
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
    """
    Supprime un profil. Recalcule les rôles des utilisateurs concernés
    en tenant compte de leurs autres profils restants. Préserve les rôles manuels.
    """
    result = await db.execute(
        select(Profile).where(Profile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil non trouvé")

    profile_name = profile.name

    # Récupérer les utilisateurs ayant ce profil via la table de jonction
    users_result = await db.execute(
        select(User).join(user_profiles, user_profiles.c.user_id == User.id)
        .where(user_profiles.c.profile_id == profile_id)
    )
    users = users_result.scalars().all()

    # Supprimer le profil (cascade supprime les profile_app_roles et user_profiles entries)
    await db.delete(profile)
    await db.flush()

    # Recalculer les rôles pour chaque utilisateur affecté (avec les profils restants)
    for user in users:
        await _sync_user_profile_roles(db, user.id)

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
    Ajoute un profil à un ou plusieurs utilisateurs (additif — ne remplace pas les profils existants).
    - Ajoute l'entrée dans user_profiles
    - Si sync_roles=True : recalcule les user_app_roles en tenant compte de tous les profils
    """
    # Charger le profil
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
        select(User).where(User.id.in_(request.user_ids))
    )
    users = result.scalars().all()

    if not users:
        raise HTTPException(status_code=404, detail="Aucun utilisateur trouvé")

    # Vérifier les associations existantes
    existing_result = await db.execute(
        select(user_profiles.c.user_id).where(
            user_profiles.c.profile_id == profile_id,
            user_profiles.c.user_id.in_(request.user_ids)
        )
    )
    already_assigned = {row[0] for row in existing_result.all()}

    updated_count = 0
    for user in users:
        if user.id not in already_assigned:
            # Ajouter l'association user <-> profile
            await db.execute(
                insert(user_profiles).values(
                    user_id=user.id,
                    profile_id=profile_id,
                    assigned_by=admin.id
                )
            )

        if request.sync_roles:
            await _sync_user_profile_roles(db, user.id)

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
    Recalcule les rôles en tenant compte de tous les profils de chaque utilisateur.
    Ne touche qu'aux rôles source='profile:*', préserve les rôles manuels.
    """
    # Vérifier que le profil existe
    result = await db.execute(
        select(Profile).where(Profile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil non trouvé")

    # Charger tous les users avec ce profil via la table de jonction
    result = await db.execute(
        select(User).join(user_profiles, user_profiles.c.user_id == User.id)
        .where(user_profiles.c.profile_id == profile_id)
    )
    users = result.scalars().all()

    if not users:
        return {
            "message": f"Aucun utilisateur n'a le profil '{profile.name}'",
            "synced_count": 0
        }

    synced_count = 0
    for user in users:
        await _sync_user_profile_roles(db, user.id)
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
    request: ProfileRemoveUsersRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Retire un profil des utilisateurs spécifiés.
    Recalcule les rôles en tenant compte des profils restants.
    Les rôles manuels sont préservés.
    """
    # Vérifier que le profil existe
    result = await db.execute(
        select(Profile).where(Profile.id == profile_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Profil non trouvé")

    # Récupérer les utilisateurs concernés
    result = await db.execute(
        select(User).join(user_profiles, user_profiles.c.user_id == User.id)
        .where(
            user_profiles.c.profile_id == profile_id,
            User.id.in_(request.user_ids)
        )
    )
    users = result.scalars().all()

    # Supprimer l'association user <-> profile
    for user in users:
        await db.execute(
            delete(user_profiles).where(
                user_profiles.c.user_id == user.id,
                user_profiles.c.profile_id == profile_id
            )
        )

    # Recalculer les rôles avec les profils restants
    if request.sync_roles:
        for user in users:
            await _sync_user_profile_roles(db, user.id)

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
