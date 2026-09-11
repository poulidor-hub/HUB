"""
Proxy « Droits Philia » : le Hub parle à l'app philia-proxy (phase 1 :
lecture + suppression des périmètres PhiliaNG via l'app).

Authentification Hub→app : token de SERVICE signé par le Hub (TTL 90 s,
apps={philia-proxy: admin}), validé par l'app via JWKS. Chaque suppression
est auditée (username admin Hub + cible).

Design : specs/2026-09-11-philia-rights-hub-design.md (dépôt exemple-app).
"""
import time
from datetime import timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Application
from app.auth import create_access_token, get_current_admin
from app.routes.audit import log_action

router = APIRouter(prefix="/api/philia-rights", tags=["Philia Rights"])

APP_SLUG = "philia-proxy"
_TOKEN_TTL_SECONDS = 90
_TOKEN_CACHE_SECONDS = 60
_HTTP_TIMEOUT = 15.0

# Transport injectable pour les tests (httpx.MockTransport)
_http_transport: Optional[httpx.AsyncBaseTransport] = None

# Cache mémoire du token service (TTL 90 s, re-généré toutes les 60 s)
_service_token_cache: dict = {"token": None, "created": 0.0}


class RemoveRightRequest(BaseModel):
    label: str


async def _get_service_token() -> str:
    now = time.monotonic()
    if _service_token_cache["token"] and now - _service_token_cache["created"] < _TOKEN_CACHE_SECONDS:
        return _service_token_cache["token"]
    token, _, _ = create_access_token(
        username="hub-m2m",
        apps={APP_SLUG: "admin"},
        expires_delta=timedelta(seconds=_TOKEN_TTL_SECONDS),
        is_service_account=True,
    )
    _service_token_cache["token"] = token
    _service_token_cache["created"] = now
    return token


async def _get_app_api_url(db: AsyncSession) -> str:
    result = await db.execute(select(Application).where(Application.slug == APP_SLUG))
    app = result.scalar_one_or_none()
    if not app or not app.api_url:
        raise HTTPException(status_code=404,
                            detail=f"Application '{APP_SLUG}' introuvable ou sans api_url")
    return app.api_url.rstrip("/")


async def _app_request(db: AsyncSession, method: str, path: str) -> dict:
    """Appel serveur-serveur vers l'app (token service + X-Slug: hub)."""
    base = await _get_app_api_url(db)
    token = await _get_service_token()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, transport=_http_transport) as client:
            resp = await client.request(
                method, f"{base}{path}",
                headers={"Authorization": f"Bearer {token}", "X-Slug": "hub"},
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"App '{APP_SLUG}' injoignable: {e}")
    if resp.status_code >= 500:
        raise HTTPException(status_code=502,
                            detail=f"L'application a retourné {resp.status_code}")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail") or resp.text[:200]
        except ValueError:
            detail = resp.text[:200]
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


@router.get("/users")
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """Sessions proxy Philia connues avec leur id numérique PhiliaNG."""
    return await _app_request(db, "GET", "/api/admin/philia-rights/users")


@router.get("/users/{philia_user_id}/perimeters")
async def get_perimeters(
    philia_user_id: str,
    ptype: str = "agencies",
    db: AsyncSession = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """Lecture des périmètres (ptype=mo|agencies), en direct source."""
    if ptype not in ("mo", "agencies"):
        raise HTTPException(status_code=422, detail="ptype doit valoir 'mo' ou 'agencies'")
    return await _app_request(
        db, "GET",
        f"/api/admin/philia-rights/users/{philia_user_id}/perimeters?ptype={ptype}",
    )


@router.delete("/users/{philia_user_id}/perimeters/{agency_id}/groups/{group_value}")
async def remove_right(
    philia_user_id: str,
    agency_id: str,
    group_value: str,
    body: RemoveRightRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Supprime un droit (groupe sur agence) dans PhiliaNG, via l'app. Audité."""
    result = await _app_request(
        db, "DELETE",
        f"/api/admin/philia-rights/users/{philia_user_id}/perimeters/{agency_id}/groups/{group_value}",
    )
    await log_action(
        db=db,
        user_id=admin.id,
        action="philia_right_removed",
        target=f"user_ng={philia_user_id} agency={agency_id} group={group_value}",
        details=f"Retrait droit Philia '{body.label}' — résultat app: {result}",
    )
    await db.commit()
    return result
