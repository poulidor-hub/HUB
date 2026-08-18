"""Endpoints de déploiement automatique des apps (spec 2026-08-18-auto-deploy-apps)."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.deploy_service import has_active_deployment, start_deployment
from app.models import AppDeployment, Application, User

router = APIRouter(prefix="/api/apps", tags=["deploiement"])
logger = logging.getLogger(__name__)


async def _get_app_or_404(db: AsyncSession, slug: str) -> Application:
    result = await db.execute(select(Application).where(Application.slug == slug))
    app = result.scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="Application inconnue")
    return app


def _deployment_to_dict(d: AppDeployment) -> dict:
    return {
        "id": d.id,
        "slug": d.app_slug,
        "status": d.status,
        "current_step": d.current_step,
        "logs": d.logs or "",
        "started_at": d.started_at,
        "finished_at": d.finished_at,
        "triggered_by": d.triggered_by,
    }


@router.post("/{slug}/deploy", status_code=201)
async def deploy_app(
    slug: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Déclenche le déploiement automatique (non-bloquant)."""
    app = await _get_app_or_404(db, slug)
    if not app.git_repo_url:
        raise HTTPException(
            status_code=400,
            detail="git_repo_url manquant : impossible de déployer sans dépôt Git",
        )
    if await has_active_deployment(db):
        raise HTTPException(
            status_code=409,
            detail="Un déploiement est déjà en cours. Réessayez une fois terminé.",
        )
    deployment_id = await start_deployment(slug=slug, triggered_by=admin.username)
    logger.info(f"Déploiement #{deployment_id} lancé pour {slug} par {admin.username}")
    return {"deployment_id": deployment_id, "slug": slug, "status": "pending"}


@router.get("/{slug}/deploy/status")
async def get_deploy_status(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Statut courant + historique (max 10) des déploiements de l'app."""
    await _get_app_or_404(db, slug)
    result = await db.execute(
        select(AppDeployment)
        .where(AppDeployment.app_slug == slug)
        .order_by(AppDeployment.id.desc())
        .limit(10)
    )
    deps = list(result.scalars().all())
    return {
        "current": _deployment_to_dict(deps[0]) if deps else None,
        "history": [_deployment_to_dict(d) for d in deps],
    }
