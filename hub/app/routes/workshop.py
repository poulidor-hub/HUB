"""Routes atelier — cycle create/update/ship/remove piloté depuis la vue santé."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.models import Application, User, WorkshopOperation
from app.workshop_service import (
    _alloc_subnet,
    _git_state,
    get_apps_dir,
    get_workshop_state,
    has_active_workshop_operation,
    start_workshop_operation,
)

logger = logging.getLogger("hub.workshop")
router = APIRouter(prefix="/api/apps", tags=["atelier"])


async def _get_app_or_404(db: AsyncSession, slug: str) -> Application:
    result = await db.execute(select(Application).where(Application.slug == slug))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"Application '{slug}' inconnue")
    return app


def _op_to_dict(op: WorkshopOperation) -> dict:
    return {
        "id": op.id, "app_slug": op.app_slug, "operation": op.operation,
        "status": op.status, "current_step": op.current_step, "logs": op.logs,
        "triggered_by": op.triggered_by,
        "started_at": op.started_at.isoformat() if op.started_at else None,
        "finished_at": op.finished_at.isoformat() if op.finished_at else None,
    }


async def _ensure_no_active(db: AsyncSession) -> None:
    """Garde-fou global : une seule opération atelier à la fois (toutes apps)."""
    if await has_active_workshop_operation(db):
        raise HTTPException(
            status_code=409,
            detail="Une opération atelier est déjà en cours. Réessayez une fois terminée.")


@router.get("/{slug}/workshop")
async def get_workshop(slug: str, db: AsyncSession = Depends(get_db),
                       _: User = Depends(get_current_admin)):
    """État atelier (contrat .workshop.env) + git_ok + opération active."""
    await _get_app_or_404(db, slug)
    state = get_workshop_state(slug)
    state["git_ok"] = (await _git_state(get_apps_dir() / slug))["ok"]
    state["active_operation"] = await has_active_workshop_operation(db, slug=slug)
    return state


@router.post("/{slug}/workshop", status_code=201)
async def create_workshop(slug: str, db: AsyncSession = Depends(get_db),
                          admin: User = Depends(get_current_admin)):
    await _get_app_or_404(db, slug)
    await _ensure_no_active(db)
    state = get_workshop_state(slug)
    if state["exists"]:
        raise HTTPException(status_code=409,
                            detail=f"Atelier déjà existant pour '{slug}'.")
    if not (await _git_state(get_apps_dir() / slug))["ok"]:
        raise HTTPException(status_code=400,
                            detail="Cette app n'a pas de repo git avec remote origin — "
                                   "l'atelier ne peut pas être créé en ligne.")
    if _alloc_subnet() is None:
        raise HTTPException(status_code=503,
                            detail="Pool de subnets atelier épuisé (10.210.x).")
    op_id = await start_workshop_operation(slug, "create", admin.username)
    logger.info(f"Atelier create #{op_id} lancé pour {slug} par {admin.username}")
    return {"operation_id": op_id, "slug": slug, "operation": "create", "status": "pending"}


@router.post("/{slug}/workshop/update", status_code=201)
async def update_workshop(slug: str, db: AsyncSession = Depends(get_db),
                          admin: User = Depends(get_current_admin)):
    await _get_app_or_404(db, slug)
    await _ensure_no_active(db)
    state = get_workshop_state(slug)
    if not state["exists"]:
        raise HTTPException(status_code=409,
                            detail=f"Aucun atelier pour '{slug}' — créez-le d'abord.")
    if not (await _git_state(get_apps_dir() / slug))["ok"]:
        raise HTTPException(status_code=400,
                            detail="Cette app n'a pas de repo git avec remote origin.")
    op_id = await start_workshop_operation(slug, "update", admin.username)
    logger.info(f"Atelier update #{op_id} lancé pour {slug} par {admin.username}")
    return {"operation_id": op_id, "slug": slug, "operation": "update", "status": "pending"}


@router.post("/{slug}/workshop/ship", status_code=201)
async def ship_workshop(slug: str, db: AsyncSession = Depends(get_db),
                        admin: User = Depends(get_current_admin)):
    await _get_app_or_404(db, slug)
    await _ensure_no_active(db)
    state = get_workshop_state(slug)
    if not state["exists"]:
        raise HTTPException(status_code=409,
                            detail=f"Aucun atelier pour '{slug}' — créez-le d'abord.")
    if not (await _git_state(get_apps_dir() / slug))["ok"]:
        raise HTTPException(status_code=400,
                            detail="Cette app n'a pas de repo git avec remote origin.")
    if state.get("ws_ref") != "main":
        raise HTTPException(
            status_code=409,
            detail=f"L'atelier teste « {state.get('ws_ref')} » — le déploiement ne "
                   "concerne que origin/main. Mergez (release-on-main), mettez à jour "
                   "l'atelier, puis réessayez.")
    op_id = await start_workshop_operation(slug, "ship", admin.username)
    logger.info(f"Atelier ship #{op_id} lancé pour {slug} par {admin.username}")
    return {"operation_id": op_id, "slug": slug, "operation": "ship", "status": "pending"}


@router.delete("/{slug}/workshop", status_code=201)
async def remove_workshop(slug: str, db: AsyncSession = Depends(get_db),
                          admin: User = Depends(get_current_admin)):
    await _get_app_or_404(db, slug)
    await _ensure_no_active(db)
    if not get_workshop_state(slug)["exists"]:
        raise HTTPException(status_code=409,
                            detail=f"Aucun atelier pour '{slug}'.")
    op_id = await start_workshop_operation(slug, "remove", admin.username)
    logger.info(f"Atelier remove #{op_id} lancé pour {slug} par {admin.username}")
    return {"operation_id": op_id, "slug": slug, "operation": "remove", "status": "pending"}


@router.get("/{slug}/workshop/status")
async def get_workshop_status(slug: str, db: AsyncSession = Depends(get_db),
                              _: User = Depends(get_current_admin)):
    await _get_app_or_404(db, slug)
    result = await db.execute(
        select(WorkshopOperation).where(WorkshopOperation.app_slug == slug)
        .order_by(WorkshopOperation.id.desc()).limit(10))
    ops = list(result.scalars().all())
    return {"current": _op_to_dict(ops[0]) if ops else None,
            "history": [_op_to_dict(o) for o in ops]}
