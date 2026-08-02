"""
Apps Health Dashboard — monitoring et actions admin sur les apps enregistrees.
"""
import time
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.models import Application, User
from app.docker_scanner import docker_scanner
from app.github_client import (
    github_headers, load_db_token, invalidate_token_cache,
    fetch_github_json, fetch_github_raw, parse_github_url,
)

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
    # App système (HUB lui-même) — traité différemment côté UI
    is_system_app: bool = False

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


class ReleaseItem(BaseModel):
    tag: str
    name: str = ""
    published_at: str = ""
    body: str = ""
    html_url: str = ""


class ReleasesResponse(BaseModel):
    ok: bool
    releases: List[ReleaseItem] = []
    repo_configured: bool = False


class FileChange(BaseModel):
    filename: str
    status: str  # added, modified, removed, renamed
    additions: int = 0
    deletions: int = 0
    patch: Optional[str] = None


class DiffResponse(BaseModel):
    ok: bool
    from_tag: str
    to_tag: str
    total_commits: int = 0
    files: List[FileChange] = []
    html_url: Optional[str] = None

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
        .where(Application.is_unmanaged == False)
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
        is_system = bool(getattr(app, "is_system_app", False))
        is_hub = is_system and app.slug == "hub"

        # Pour le HUB lui-meme : sonde normale + version lue localement
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

        # Pour le HUB : enrichir avec la version lue localement
        # et les metriques internes (pas de /api/health/detailed expose)
        if is_hub:
            if not version:
                from app.routes.docs import get_hub_version as _get_hub_version
                version = _get_hub_version()
            # Uptime processus calcule depuis /proc/self/stat (Linux)
            if uptime_seconds is None:
                try:
                    with open("/proc/self/stat") as f:
                        # Le champ 22 est starttime en ticks depuis le boot
                        fields = f.read().split()
                        if len(fields) > 21:
                            uptime_ticks = int(fields[21])
                            with open("/proc/uptime") as fu:
                                system_uptime_s = float(fu.read().split()[0])
                            CLK_TCK = 100  # sysconf(_SC_CLK_TCK) typique
                            process_start_s = uptime_ticks / CLK_TCK
                            uptime_seconds = max(0, system_uptime_s - process_start_s)
                except Exception:
                    pass

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
            is_system_app=is_system,
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
    container = _find_container_for_app(slug)
    if not container:
        raise HTTPException(
            status_code=404,
            detail=f"Aucun conteneur Docker trouve pour '{slug}'. "
                   f"Verifiez que le conteneur a APP_SLUG={slug}.",
        )
    try:
        container.start()
        container.reload()
        logger.info(f"Start {container.name} (app: {slug}) par {current_admin.username}")
        return HubctlActionResponse(
            ok=True,
            action="start",
            message=f"Demarrage de {app.name} reussi ({container.name})",
        )
    except Exception as e:
        logger.error(f"Echec start {container.name}: {e}")
        return HubctlActionResponse(
            ok=False,
            action="start",
            message=f"Echec du demarrage de {app.name}",
            detail=str(e)[:300],
        )


@router.post("/{slug}/stop", response_model=HubctlActionResponse)
async def stop_app(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await _get_app_or_404(db, slug)
    container = _find_container_for_app(slug)
    if not container:
        raise HTTPException(
            status_code=404,
            detail=f"Aucun conteneur Docker trouve pour '{slug}'. "
                   f"Verifiez que le conteneur a APP_SLUG={slug}.",
        )
    try:
        container.stop(timeout=10)
        container.reload()
        logger.info(f"Stop {container.name} (app: {slug}) par {current_admin.username}")
        return HubctlActionResponse(
            ok=True,
            action="stop",
            message=f"Arret de {app.name} reussi ({container.name})",
        )
    except Exception as e:
        logger.error(f"Echec stop {container.name}: {e}")
        return HubctlActionResponse(
            ok=False,
            action="stop",
            message=f"Echec de l'arret de {app.name}",
            detail=str(e)[:300],
        )


@router.get("/{slug}/logs", response_model=AppLogsResponse)
async def get_app_logs(
    slug: str,
    tail: int = Query(default=100, ge=10, le=2000),
    service: str = Query(default="api", description="api, frontend, postgres, db, ou empty pour tous"),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await _get_app_or_404(db, slug)

    # Trouver le conteneur cible via l'API Docker (docker-py, pas CLI)
    container = None
    if service:
        # Chercher un conteneur dont le nom contient {slug}-{service}
        try:
            if docker_scanner.is_available:
                all_containers = docker_scanner.client.containers.list(all=True)
                for c in all_containers:
                    if c.name and (slug + "-" + service) in c.name:
                        container = c
                        break
        except Exception:
            pass

    # Fallback : utiliser le conteneur trouve par APP_SLUG (generalement l'API)
    if not container:
        container = _find_container_for_app(slug)

    if not container:
        svc_label = service or "tous"
        raise HTTPException(
            status_code=404,
            detail=f"Aucun conteneur trouve pour '{slug}' (service={svc_label}). "
                   f"Verifiez que le conteneur a APP_SLUG={slug}.",
        )

    try:
        logs_bytes = container.logs(tail=tail, stdout=True, stderr=True)
        lines = logs_bytes.decode("utf-8", errors="replace")
        return AppLogsResponse(
            ok=True,
            lines=lines[-10000:],
            service=service or container.name,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur logs: {e}")


# ─── GitHub update check + changelog ──────────────────────────────────

@router.get("/{slug}/update-check", response_model=UpdateCheckResponse)
async def check_update(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await _get_app_or_404(db, slug)
    parsed = parse_github_url(app.git_repo_url)
    if not parsed:
        return UpdateCheckResponse(update_available=False, repo_configured=False)

    owner, repo = parsed

    # Recuperer la version actuelle via un probe rapide
    current_version = await _quick_probe_version(app)

    # Interroger la derniere release GitHub
    release, _ = await fetch_github_json(
        f"https://api.github.com/repos/{owner}/{repo}/releases/latest", db
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
    parsed = parse_github_url(app.git_repo_url)
    if not parsed:
        return ChangelogResponse(ok=False, repo_configured=False)

    owner, repo = parsed
    markdown = await fetch_github_raw(
        f"https://raw.githubusercontent.com/{owner}/{repo}/main/CHANGELOG.md", db
    )
    if not markdown:
        markdown = await fetch_github_raw(
            f"https://raw.githubusercontent.com/{owner}/{repo}/master/CHANGELOG.md", db
        )

    return ChangelogResponse(
        ok=markdown is not None,
        markdown=markdown,
        repo_configured=True,
    )


@router.get("/{slug}/releases", response_model=ReleasesResponse)
async def get_releases(
    slug: str,
    per_page: int = Query(default=30, ge=1, le=100),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Liste l'historique des releases GitHub (tag, date, notes)."""
    app = await _get_app_or_404(db, slug)
    parsed = parse_github_url(app.git_repo_url)
    if not parsed:
        return ReleasesResponse(ok=False, repo_configured=False)

    owner, repo = parsed
    data, _ = await fetch_github_json(
        f"https://api.github.com/repos/{owner}/{repo}/releases?per_page={per_page}", db
    )
    if data is None or not isinstance(data, list):
        return ReleasesResponse(ok=False, repo_configured=True)

    releases = [
        ReleaseItem(
            tag=(r.get("tag_name") or "").lstrip("v"),
            name=r.get("name") or r.get("tag_name") or "",
            published_at=r.get("published_at") or r.get("created_at") or "",
            body=r.get("body") or "",
            html_url=r.get("html_url") or "",
        )
        for r in data
    ]
    return ReleasesResponse(ok=True, releases=releases, repo_configured=True)


@router.get("/{slug}/diff", response_model=DiffResponse)
async def get_diff(
    slug: str,
    from_tag: str = Query(..., alias="from", description="Tag de base (ex: v1.21.2)"),
    to_tag: str = Query(..., alias="to", description="Tag cible (ex: v1.21.3)"),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Compare deux tags via GitHub et retourne les fichiers modifies + patches."""
    app = await _get_app_or_404(db, slug)
    parsed = parse_github_url(app.git_repo_url)
    if not parsed:
        return DiffResponse(ok=False, from_tag=from_tag, to_tag=to_tag)

    owner, repo = parsed
    # Normaliser les tags avec un prefix v si absent
    base = from_tag if from_tag.startswith("v") else "v" + from_tag
    head = to_tag if to_tag.startswith("v") else "v" + to_tag

    data, _ = await fetch_github_json(
        f"https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}", db
    )
    if data is None:
        return DiffResponse(ok=False, from_tag=from_tag, to_tag=to_tag)

    files = [
        FileChange(
            filename=f.get("filename") or "?",
            status=f.get("status") or "modified",
            additions=f.get("additions") or 0,
            deletions=f.get("deletions") or 0,
            patch=f.get("patch"),  # None si diff trop grand (GitHub tronque)
        )
        for f in (data.get("files") or [])[:50]  # plafonner a 50 fichiers
    ]
    return DiffResponse(
        ok=True,
        from_tag=from_tag,
        to_tag=to_tag,
        total_commits=data.get("total_commits") or 0,
        files=files,
        html_url=data.get("html_url"),
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
