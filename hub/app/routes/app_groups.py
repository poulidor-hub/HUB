# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Groupes d'applications — regroupement purement visuel du dashboard.

Un groupe est un conteneur cosmétique : cliquer sur sa tuile ouvre une modale
listant les applications du groupe (aucun impact fonctionnel sur les apps).
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.models import AppGroup, Application, User
from app.schemas import AppGroupBrief, AppGroupCreate, AppGroupResponse, AppGroupUpdate

router = APIRouter(prefix="/api/app-groups", tags=["App Groups"])


async def _get_group_or_404(db: AsyncSession, group_id: int) -> AppGroup:
    result = await db.execute(select(AppGroup).where(AppGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Groupe introuvable")
    return group


def group_to_brief(group: AppGroup) -> AppGroupBrief:
    """Conversion ORM -> brief embarqué avec chaque app du dashboard"""
    return AppGroupBrief(id=group.id, name=group.name, icon=group.icon, color=group.color)


@router.get("/", response_model=List[AppGroupResponse])
async def list_groups(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Liste tous les groupes avec le nombre d'applications de chacun"""
    counts = await db.execute(
        select(Application.group_id, func.count(Application.id))
        .where(Application.group_id.isnot(None))
        .group_by(Application.group_id)
    )
    count_map = dict(counts.all())

    result = await db.execute(select(AppGroup).order_by(AppGroup.name))
    return [
        AppGroupResponse(
            id=g.id,
            name=g.name,
            description=g.description,
            icon=g.icon,
            color=g.color,
            created_at=g.created_at,
            app_count=count_map.get(g.id, 0),
        )
        for g in result.scalars().all()
    ]


@router.post("/", response_model=AppGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    data: AppGroupCreate,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Crée un groupe (409 si le nom existe déjà)"""
    dup = await db.execute(select(AppGroup).where(AppGroup.name == data.name))
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un groupe avec ce nom existe déjà")

    group = AppGroup(**data.model_dump())
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return AppGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        icon=group.icon,
        color=group.color,
        created_at=group.created_at,
        app_count=0,
    )


@router.patch("/{group_id}", response_model=AppGroupResponse)
async def update_group(
    group_id: int,
    data: AppGroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Modifie nom / description / icône / couleur d'un groupe"""
    group = await _get_group_or_404(db, group_id)

    update_data = data.model_dump(exclude_unset=True)
    if update_data.get("name") and update_data["name"] != group.name:
        dup = await db.execute(
            select(AppGroup).where(AppGroup.name == update_data["name"], AppGroup.id != group_id)
        )
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un groupe avec ce nom existe déjà")

    for field, value in update_data.items():
        setattr(group, field, value)
    await db.commit()
    await db.refresh(group)

    cnt = await db.execute(
        select(func.count(Application.id)).where(Application.group_id == group_id)
    )
    return AppGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        icon=group.icon,
        color=group.color,
        created_at=group.created_at,
        app_count=cnt.scalar() or 0,
    )


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Supprime un groupe — les applications sont simplement dégrouppées"""
    group = await _get_group_or_404(db, group_id)

    # Dégroupper explicitement (robuste quel que soit le mode d'application des FK)
    await db.execute(
        update(Application).where(Application.group_id == group_id).values(group_id=None)
    )
    await db.delete(group)
    await db.commit()
