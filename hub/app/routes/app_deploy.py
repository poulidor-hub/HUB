# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Endpoints de déploiement automatique des apps (spec 2026-08-18-auto-deploy-apps)."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.config import get_settings
from app.database import get_db
from app.deploy_precheck import run_deploy_precheck
from app.deploy_service import has_active_deployment_for, start_deployment
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
    """Déclenche le déploiement automatique (non-bloquant).

    Défense en profondeur : rejoue le precheck complet avant de lancer —
    même un appel direct à l'API (hors UI) ne peut pas démarrer un
    déploiement condamné. Le pipeline conserve ses échecs explicites en
    dernier filet (fenêtre TOCTOU entre les deux évaluations).
    """
    app = await _get_app_or_404(db, slug)
    if not app.git_repo_url:
        raise HTTPException(
            status_code=400,
            detail="git_repo_url manquant : impossible de déployer sans dépôt Git",
        )
    if await has_active_deployment_for(db, slug):
        raise HTTPException(
            status_code=409,
            detail=f"Un déploiement de '{slug}' est déjà en cours ou en file d'attente. Réessayez une fois terminé.",
        )
    ready, checks = await run_deploy_precheck(db, slug, get_settings())
    if not ready:
        # JSONResponse direct (pas HTTPException) : le handler global
        # stringifie `detail`, et un refus de validation n'a pas à polluer
        # le journal d'erreurs — le client reçoit les checks complets.
        return JSONResponse(
            status_code=422,
            content={
                "message": f"Prérequis de déploiement non satisfaits pour '{slug}'",
                "slug": slug,
                "ready": False,
                "checks": checks,
            },
        )
    deployment_id = await start_deployment(slug=slug, triggered_by=admin.username)
    logger.info(f"Déploiement #{deployment_id} lancé pour {slug} par {admin.username}")
    return {"deployment_id": deployment_id, "slug": slug, "status": "pending"}


@router.get("/{slug}/deploy/precheck")
async def get_deploy_precheck(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Prérequis de déploiement de l'app : `{ready, checks}`.

    L'UI n'ouvre la modale de mise en place que si `ready` est vrai ;
    sinon elle affiche la liste des éléments manquants.
    """
    await _get_app_or_404(db, slug)
    ready, checks = await run_deploy_precheck(db, slug, get_settings())
    return {"slug": slug, "ready": ready, "checks": checks}


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
