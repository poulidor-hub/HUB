from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List
import re

from app.database import get_db
from app.models import Application, User
from app.schemas import AppCreate, AppUpdate, AppResponse, AppWithUsers
from app.auth import get_current_admin, get_current_user

router = APIRouter(prefix="/api/apps", tags=["Applications"])


def generate_slug(name: str) -> str:
    """Génère un slug à partir du nom"""
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug


@router.get("/", response_model=List[AppResponse])
async def list_apps(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Liste les applications (admin voit tout, user voit les siennes)"""
    if current_user.is_admin:
        result = await db.execute(select(Application))
        return result.scalars().all()
    else:
        # Retourne uniquement les apps de l'utilisateur
        result = await db.execute(
            select(User)
            .options(selectinload(User.applications))
            .where(User.id == current_user.id)
        )
        user = result.scalar_one()
        return user.applications


@router.get("/all", response_model=List[AppWithUsers])
async def list_all_apps_with_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Liste toutes les applications avec leurs utilisateurs (admin uniquement)"""
    result = await db.execute(
        select(Application).options(selectinload(Application.users))
    )
    return result.scalars().all()


@router.post("/", response_model=AppResponse, status_code=status.HTTP_201_CREATED)
async def create_app(
    app_data: AppCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Crée une nouvelle application (admin uniquement)"""
    # Vérifier si le slug existe
    existing = await db.execute(
        select(Application).where(Application.slug == app_data.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce slug existe déjà"
        )
    
    app = Application(**app_data.model_dump())
    db.add(app)
    await db.commit()
    await db.refresh(app)
    
    return app


@router.get("/{app_id}", response_model=AppWithUsers)
async def get_app(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Récupère une application par ID (admin uniquement)"""
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.users))
        .where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    return app


@router.patch("/{app_id}", response_model=AppResponse)
async def update_app(
    app_id: int,
    app_data: AppUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Met à jour une application (admin uniquement)"""
    result = await db.execute(
        select(Application).where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    update_data = app_data.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        setattr(app, field, value)
    
    await db.commit()
    await db.refresh(app)
    
    return app


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Supprime une application (admin uniquement)"""
    result = await db.execute(
        select(Application).where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    await db.delete(app)
    await db.commit()


@router.post("/{app_id}/users")
async def set_app_users(
    app_id: int,
    user_ids: List[int],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Définit les utilisateurs ayant accès à une application"""
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.users))
        .where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application non trouvée"
        )
    
    # Récupérer les users
    users_result = await db.execute(
        select(User).where(User.id.in_(user_ids))
    )
    users = users_result.scalars().all()
    
    app.users = list(users)
    await db.commit()
    
    return {"message": f"Utilisateurs mis à jour pour {app.name}", "users": [u.username for u in users]}
