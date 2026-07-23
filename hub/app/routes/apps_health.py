"""
Apps Health Dashboard — monitoring et actions admin sur les apps enregistrees.
"""

import time
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.models import Application, User
from app.docker_scanner import docker_scanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/apps", tags=["apps-health"])

# /api/health est le standard du parc (template + exemple-app) → testé en premier.
# On ne break que sur un 2xx (vrai health endpoint) ou un 5xx (degraded) ;
# un 4xx (ex: 404 sur /health nu) indique juste que le serveur est joignable
# et qu'on doit essayer le path suivant.
HEALTH_PATHS = ["/api/health", "/health", "/"]
DETAILED_PATH = "/api/health/detailed"
PROBE_TIMEOUT = 5

# ─── Schemas ──────────────────────────────────────────────────────────

class AppHealthItem(BaseModel):
    id: int
    slug: str
    name: str
    icon: Optional[str] = None
    color: Optional[str] = None
    api_url: str
    status: str  # "online" | "offline" | "degraded"
    latency_ms: int = 0
    version: Optional[str] = None
    error: Optional[str] = None
    checked_at: str = ""
    # Champs enrichis (issus de /api/health/detailed quand disponible)
    uptime_seconds: Optional[float] = None
    hub_connected: Optional[bool] = None
    services_count: Optional[int] = None
    m2m_token_available: Optional[bool] = None

class HealthSummary(BaseModel):
    online: int = 0
    offline: int = 0
    degraded: int = 0
    total: int = 0

class AppsHealthResponse(BaseModel):
    apps: List[AppHealthItem]
    summary: HealthSummary

class RestartResponse(BaseModel):
    ok: bool
    container_name: Optional[str] = None
    status: Optional[str] = None
    detail: Optional[str] = None

# ─── Helpers ──────────────────────────────────────────────────────────

def _get_httpx_client():
    import httpx
    return httpx.AsyncClient(
        timeout=httpx.Timeout(PROBE_TIMEOUT),
        verify=False,
        follow_redirects=False,
    )


def _find_container_for_app(slug: str):
    if not docker_scanner.is_available:
        return None
    try:
        containers = docker_scanner.client.containers.list(all=True)
    except Exception as e:
        logger.warning(f"Docker list failed: {e}")
        return None

    for container in containers:
        try:
            env_list = container.attrs.get('Config', {}).get('Env', [])
            for env in env_list:
                if '=' in env:
                    key, value = env.split('=', 1)
                    if key == 'APP_SLUG' and value == slug:
                        return container
        except Exception:
            pass

        labels = container.labels or {}
        if labels.get('hub.slug') == slug:
            return container

    return None


# ─── Routes ───────────────────────────────────────────────────────────

@router.get("/health", response_model=AppsHealthResponse)
async def get_apps_health(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Application)
        .where(Application.is_active == True)
        .order_by(Application.name)
    )
    apps = result.scalars().all()

    async def probe_app(app: Application) -> AppHealthItem:
        base_url = app.api_url.rstrip("/")
        now = time.time()
        status = "offline"
        latency_ms = 0
        version = None
        error = None
        uptime_seconds = None
        hub_connected = None
        services_count = None
        m2m_token_available = None

        async with _get_httpx_client() as client:
            # Phase 1 : trouver un endpoint sante joignable.
            # On ne s'arrete que sur un 2xx (vrai health endpoint) ou un 5xx
            # (degraded). Un 4xx (ex: 404 sur /health nu) = serveur joignable,
            # on continue avec le path suivant.
            for path in HEALTH_PATHS:
                try:
                    start = time.time()
                    resp = await client.get(base_url + path)
                    latency_ms = int((time.time() - start) * 1000)

                    if 200 <= resp.status_code < 300:
                        status = "online"
                        try:
                            body = resp.json()
                            if isinstance(body, dict):
                                version = body.get("version")
                        except Exception:
                            pass
                        break
                    elif resp.status_code >= 500:
                        status = "degraded"
                        error = f"HTTP {resp.status_code} {resp.reason_phrase or ''}".strip()
                        break
                    else:
                        # 3xx/4xx = serveur joignable mais pas d'endpoint sante
                        # standard sur ce path. On marque joignable et on tente
                        # le path suivant (ne pas break).
                        if status == "offline":
                            status = "online"
                except Exception as e:
                    error = str(e)[:200]
                    continue

            # Phase 2 : si l'app est joignable, sonder /api/health/detailed
            # pour recuperer les donnees riches (uptime, hub, services, version).
            # Endpoint optionnel : echec silencieux.
            if status == "online":
                try:
                    resp = await client.get(base_url + DETAILED_PATH)
                    if 200 <= resp.status_code < 300:
                        body = resp.json()
                        if isinstance(body, dict):
                            if not version:
                                app_info = body.get("app")
                                if isinstance(app_info, dict):
                                    version = app_info.get("version")
                            uptime_seconds = body.get("uptime_seconds")
                            hub_info = body.get("hub")
                            if isinstance(hub_info, dict):
                                hub_connected = hub_info.get("connected")
                                m2m_token_available = hub_info.get("m2m_token_available")
                            services_info = body.get("services")
                            if isinstance(services_info, dict):
                                services_count = services_info.get("discovered_count")
                except Exception:
                    pass

        if status == "offline" and not error:
            error = "Aucune reponse sur /health, /api/health, /"

        return AppHealthItem(
            id=app.id,
            slug=app.slug,
            name=app.name,
            icon=app.icon,
            color=app.color,
            api_url=app.api_url,
            status=status,
            latency_ms=latency_ms,
            version=version,
            error=error,
            checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            uptime_seconds=uptime_seconds,
            hub_connected=hub_connected,
            services_count=services_count,
            m2m_token_available=m2m_token_available,
        )

    import asyncio
    items = await asyncio.gather(*[probe_app(app) for app in apps])

    summary = HealthSummary(
        total=len(items),
        online=sum(1 for i in items if i.status == "online"),
        offline=sum(1 for i in items if i.status == "offline"),
        degraded=sum(1 for i in items if i.status == "degraded"),
    )

    return AppsHealthResponse(apps=list(items), summary=summary)


@router.post("/{slug}/restart", response_model=RestartResponse)
async def restart_app(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Application).where(Application.slug == slug, Application.is_active == True)
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"Application '{slug}' non trouvee")

    container = _find_container_for_app(slug)

    if not container:
        raise HTTPException(
            status_code=404,
            detail=f"Aucun conteneur Docker trouve pour '{slug}'. "
                   f"Verifiez que le conteneur a APP_SLUG={slug} ou le label hub.slug={slug}."
        )

    try:
        container.restart(timeout=10)
        container.reload()
        logger.info(f"Restart {container.name} (app: {slug}) par {current_admin.username}")

        return RestartResponse(
            ok=True,
            container_name=container.name,
            status=container.status,
        )
    except Exception as e:
        logger.error(f"Echec restart {container.name}: {e}")
        raise HTTPException(status_code=500, detail=f"Echec du redemarrage: {e}")
