"""
Apps Health Dashboard — monitoring et actions admin sur les apps enregistrees.
"""
import re
import sys
import time
import logging
import subprocess
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
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

# Racine du parc POULIDOR (la ou hubctl_cli/ resume)
POULIDOR_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
HUBCTL_TIMEOUT = 120  # secondes pour les operations longues (start/stop/update)


def _run_hubctl(action: str, slug: str, *extra_args: str) -> dict:
    """Execute une commande hubctl dans le parc POULIDOR et retourne {ok, stdout, stderr}."""
    cmd = [sys.executable, "-m", "hubctl_cli", action, slug, *extra_args]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(POULIDOR_ROOT),
            capture_output=True,
            text=True,
            timeout=HUBCTL_TIMEOUT,
        )
        ok = result.returncode == 0
        logger.info(
            f"hubctl {action} {slug} -> {'OK' if ok else 'FAIL'} "
            f"(exit={result.returncode})"
        )
        return {
            "ok": ok,
            "stdout": result.stdout[-3000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        logger.warning(f"hubctl {action} {slug} timeout apres {HUBCTL_TIMEOUT}s")
        return {"ok": False, "stdout": "", "stderr": f"Timeout ({HUBCTL_TIMEOUT}s)"}
    except Exception as e:
        logger.error(f"hubctl {action} {slug} erreur: {e}")
        return {"ok": False, "stdout": "", "stderr": str(e)[:500]}


def _parse_github_url(git_repo_url: str) -> Optional[tuple[str, str]]:
    """Parse une URL GitHub en (owner, repo). Retourne None si non parsee."""
    if not git_repo_url:
        return None
    m = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$', git_repo_url)
    return (m.group(1), m.group(2)) if m else None

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


class HubctlActionResponse(BaseModel):
    ok: bool
    action: str  # "start", "stop", "restart"
    message: str
    detail: Optional[str] = None


class AppLogsResponse(BaseModel):
    ok: bool
    lines: str
    service: str = "api"


class UpdateCheckResponse(BaseModel):
    update_available: bool
    current_version: Optional[str] = None
    latest_version: Optional[str] = None
    release_url: Optional[str] = None
    published_at: Optional[str] = None
    repo_configured: bool = False


class ChangelogResponse(BaseModel):
    ok: bool
    markdown: Optional[str] = None
    repo_configured: bool = False

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


# ─── Pilotage hubctl ──────────────────────────────────────────────────

@router.post("/{slug}/start", response_model=HubctlActionResponse)
async def start_app(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await _get_app_or_404(db, slug)
    result = _run_hubctl("start", slug)
    return HubctlActionResponse(
        ok=result["ok"],
        action="start",
        message=f"Demarrage de {app.name} {'reussi' if result['ok'] else 'echoue'}",
        detail=result["stderr"] if not result["ok"] else None,
    )


@router.post("/{slug}/stop", response_model=HubctlActionResponse)
async def stop_app(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await _get_app_or_404(db, slug)
    result = _run_hubctl("stop", slug)
    return HubctlActionResponse(
        ok=result["ok"],
        action="stop",
        message=f"Arret de {app.name} {'reussi' if result['ok'] else 'echoue'}",
        detail=result["stderr"] if not result["ok"] else None,
    )


@router.get("/{slug}/logs", response_model=AppLogsResponse)
async def get_app_logs(
    slug: str,
    tail: int = Query(default=100, ge=10, le=2000),
    service: str = Query(default="api", description="api, frontend, postgres, ou empty pour tous"),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await _get_app_or_404(db, slug)
    compose_file = POULIDOR_ROOT / "APPS" / slug / "docker-compose.yml"
    if not compose_file.exists():
        raise HTTPException(status_code=404, detail=f"docker-compose.yml introuvable pour '{slug}'")

    svc_args = [service] if service else []
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "logs", "--tail", str(tail), "--no-color", *svc_args],
            capture_output=True, text=True, timeout=20,
        )
        lines = result.stdout if result.returncode == 0 else result.stderr
        return AppLogsResponse(
            ok=result.returncode == 0,
            lines=lines[-10000:],
            service=service or "all",
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Timeout lors de la recuperation des logs")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur logs: {e}")


# ─── GitHub update check + changelog ──────────────────────────────────

async def _fetch_github(url: str) -> Optional[dict]:
    """GET JSON depuis l'API GitHub. Retourne None si erreur."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10), verify=False) as client:
            resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            if 200 <= resp.status_code < 300:
                return resp.json()
            return None
    except Exception:
        return None


async def _fetch_github_raw(url: str) -> Optional[str]:
    """GET contenu brut depuis raw.githubusercontent.com."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10), verify=False) as client:
            resp = await client.get(url)
            if 200 <= resp.status_code < 300:
                return resp.text
            return None
    except Exception:
        return None


@router.get("/{slug}/update-check", response_model=UpdateCheckResponse)
async def check_update(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await _get_app_or_404(db, slug)
    parsed = _parse_github_url(app.git_repo_url)
    if not parsed:
        return UpdateCheckResponse(update_available=False, repo_configured=False)

    owner, repo = parsed

    # Recuperer la version actuelle via un probe rapide
    current_version = await _quick_probe_version(app)

    # Interroger la derniere release GitHub
    release = await _fetch_github(
        f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    )
    if not release:
        return UpdateCheckResponse(
            update_available=False,
            current_version=current_version,
            repo_configured=True,
        )

    latest_tag = (release.get("tag_name") or "").lstrip("v")
    latest_version = latest_tag or release.get("name", "").lstrip("v")

    update_available = False
    if current_version and latest_version:
        try:
            from packaging.version import parse as vparse
            update_available = vparse(latest_version) > vparse(current_version)
        except Exception:
            update_available = latest_version != current_version

    return UpdateCheckResponse(
        update_available=update_available,
        current_version=current_version,
        latest_version=latest_version or None,
        release_url=release.get("html_url"),
        published_at=release.get("published_at"),
        repo_configured=True,
    )


@router.get("/{slug}/changelog", response_model=ChangelogResponse)
async def get_changelog(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await _get_app_or_404(db, slug)
    parsed = _parse_github_url(app.git_repo_url)
    if not parsed:
        return ChangelogResponse(ok=False, repo_configured=False)

    owner, repo = parsed
    markdown = await _fetch_github_raw(
        f"https://raw.githubusercontent.com/{owner}/{repo}/main/CHANGELOG.md"
    )
    if not markdown:
        markdown = await _fetch_github_raw(
            f"https://raw.githubusercontent.com/{owner}/{repo}/master/CHANGELOG.md"
        )

    return ChangelogResponse(
        ok=markdown is not None,
        markdown=markdown,
        repo_configured=True,
    )


# ─── Helpers internes ─────────────────────────────────────────────────

async def _get_app_or_404(db: AsyncSession, slug: str) -> Application:
    result = await db.execute(
        select(Application).where(
            Application.slug == slug,
            Application.is_active == True,
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"Application '{slug}' non trouvee")
    return app


async def _quick_probe_version(app: Application) -> Optional[str]:
    """Sonde rapide de /api/health pour recuperer la version actuelle."""
    import httpx
    base_url = app.api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5), verify=False) as client:
            for path in HEALTH_PATHS:
                try:
                    resp = await client.get(base_url + path)
                    if 200 <= resp.status_code < 300:
                        body = resp.json()
                        if isinstance(body, dict):
                            return body.get("version")
                except Exception:
                    continue
    except Exception:
        pass
    return None
