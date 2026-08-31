"""
Recréation des conteneurs d'une app (POST /api/apps/{slug}/recreate).

Second temps du workflow réseau : après propagation de la nouvelle IP dans le
.env (validation admin distincte), l'admin déclenche la recréation — les
conteneurs ne prennent l'IP qu'au boot (backend env + proxy nginx régénérés).

Opération tracée via le moteur workshop_operations (handler "recreate") :
docker compose -p <slug> up -d --force-recreate + health-check sur API_PORT.
Statut pollable via GET /api/apps/{slug}/workshop/status.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Application, User
from app.auth import get_current_admin
from app.workshop_service import (
    has_active_workshop_operation,
    start_workshop_operation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/apps", tags=["Network"])


@router.post("/{slug}/recreate", status_code=status.HTTP_202_ACCEPTED)
async def recreate_app(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Recrée les conteneurs d'une app (up -d --force-recreate + health-check).

    Opération asynchrone tracée — statut via GET /api/apps/{slug}/workshop/status.
    """
    result = await db.execute(
        select(Application).where(Application.slug == slug)
    )
    app = result.scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Application inconnue")
    if app.is_workshop:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Atelier : passez par le cycle atelier "
                                   "(create/remove), pas par la recréation réseau")
    if app.is_system_app:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="App système interne au Hub — rien à recréer")

    if await has_active_workshop_operation(db, slug):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Une opération est déjà en cours sur cette app")

    op_id = await start_workshop_operation(slug, "recreate",
                                           current_admin.username)
    logger.info(f"🔄 Recréation des conteneurs de '{slug}' demandée par "
                f"{current_admin.username} (opération {op_id})")
    return {"operation_id": op_id, "slug": slug, "operation": "recreate",
            "status": "pending"}
