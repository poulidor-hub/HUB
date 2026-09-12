"""
Socle-check d'une application (POST /api/apps/{slug}/socle-check).

Déclenche à la demande (vue santé admin) le rapport d'intégration hub↔app
défini dans app.socle_check : 7 contrôles en conditions réelles, aucun
effet de bord (round-trip préférences idempotent, backup en lecture seule).

La route mint deux tokens éphémères dédiés à ce contrôle, puis ne les
persiste nulle part :
- un JWT user « admin sur l'app » : prouve la validation JWKS côté app et
  porte les rôles requis par discovery/backup (TTL 5 min ≫ pire cas 7×5 s) ;
- un token service account au périmètre M2M minimal ({slug: "service"}),
  fidèle au claim d'un vrai service account (AN-2).

Un contrôle en échec est documenté dans le rapport — la route reste en 200.
"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, get_current_admin
from app.database import get_db
from app.models import Application, User
from app.routes.docs import get_hub_version
from app.socle_check import run_socle_check

logger = logging.getLogger(__name__)

SOCLE_TOKEN_TTL = timedelta(minutes=5)

router = APIRouter(prefix="/api/apps", tags=["apps-health"])


@router.post("/{slug}/socle-check")
async def socle_check_app(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Exécute les 7 contrôles du socle contre l'app {slug} (réservé admin).

    Retourne le rapport complet (contrôles, score, versions hub/app).
    """
    result = await db.execute(
        select(Application).where(Application.slug == slug)
    )
    app = result.scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Application inconnue")
    if not (app.api_url or "").strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Application sans api_url — socle non contrôlable",
        )

    # Tokens éphémères dédiés au contrôle (TTL court, périmètre minimal) :
    user_token, _, _ = create_access_token(
        username=current_admin.username,
        apps={slug: "admin"},
        expires_delta=SOCLE_TOKEN_TTL,
    )
    service_token, _, _ = create_access_token(
        username=f"sa_{slug}",
        apps={slug: "service"},
        expires_delta=SOCLE_TOKEN_TTL,
        is_service_account=True,
    )

    logger.info("🩺 Socle-check de '%s' demandé par %s",
                slug, current_admin.username)
    return await run_socle_check(
        slug,
        app.api_url,
        user_token=user_token,
        service_token=service_token,
        hub_version=get_hub_version(),
    )
