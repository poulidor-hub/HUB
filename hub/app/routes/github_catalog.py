"""Catalogue GitHub : découverte et sélection visuelle de repos.

Endpoints admin-only sous /api/apps/github :
- GET  /repos          : liste le cache (0 appel GitHub) + already_integrated
- POST /repos/refresh  : synchronise le cache depuis GitHub (garde d'âge 30s)
- POST /prepare        : pré-remplit la création d'app depuis un repo
"""
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.config import get_settings
from app.database import get_db
from app.github_client import parse_github_url, load_db_token, slugify_repo
from app.models import Application, GithubRepoCache, User, utcnow
from app.port_allocator import allocate_ports_for_app, PortPoolExhausted
from app.schemas import (
    GithubRepo,
    GithubRepoListResponse,
    GithubRepoPrepareRequest,
    GithubRepoPrepareResponse,
    GithubRepoRefreshResponse,
)

router = APIRouter(prefix="/api/apps/github", tags=["github"])

REFRESH_MIN_AGE_SECONDS = 30
GITHUB_MAX_REPOS = 1000


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


@router.get("/repos", response_model=GithubRepoListResponse)
async def list_github_repos(
    search: Optional[str] = Query(None),
    owner: Optional[str] = Query(None),
    include_forks: bool = Query(False),
    include_archived: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Liste les repos GitHub depuis le cache (zéro appel GitHub).

    already_integrated est calculé en croisant clone_url avec git_repo_url
    des apps existantes (normalisation via (owner, repo)).
    """
    query = select(GithubRepoCache)
    if search:
        like = f"%{search.lower()}%"
        query = query.where(
            (GithubRepoCache.name.ilike(like)) | (GithubRepoCache.full_name.ilike(like))
        )
    if owner:
        query = query.where(GithubRepoCache.owner == owner)
    if not include_forks:
        query = query.where(GithubRepoCache.fork == False)  # noqa: E712
    if not include_archived:
        query = query.where(GithubRepoCache.archived == False)  # noqa: E712

    query = query.order_by(GithubRepoCache.updated_at_github.desc().nullslast())
    cached = (await db.execute(query)).scalars().all()

    apps = (await db.execute(select(Application))).scalars().all()
    integrated_keys = set()
    for app in apps:
        parsed = parse_github_url(app.git_repo_url) if app.git_repo_url else None
        if parsed:
            integrated_keys.add(parsed)

    repos = []
    for c in cached:
        parsed = parse_github_url(c.clone_url)
        already = parsed in integrated_keys if parsed else False
        repos.append(GithubRepo(
            name=c.name,
            full_name=c.full_name,
            owner=c.owner,
            html_url=c.html_url,
            git_url=c.clone_url,
            description=c.description,
            default_branch=c.default_branch,
            fork=c.fork,
            archived=c.archived,
            private=c.private,
            updated_at=_iso(c.updated_at_github),
            already_integrated=already,
        ))

    fetched_at = max((c.fetched_at for c in cached), default=None)
    token_configured = bool(get_settings().github_token or await load_db_token(db))
    return GithubRepoListResponse(
        repos=repos,
        total=len(repos),
        fetched_at=_iso(fetched_at),
        token_configured=token_configured,
    )


def _parse_link_next(link_header: str) -> Optional[str]:
    """Extrait l'URL 'rel="next"' du header Link de GitHub."""
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(">")[0].split("<")[1] if "<" in part else None
    return None


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse une date ISO GitHub (ex: 2026-07-20T00:00:00Z) en datetime naïf UTC.

    GitHub renvoie des dates avec 'Z' (UTC). On retourne un datetime naïf
    (tzinfo=None) pour rester cohérent avec le helper utcnow() du codebase
    et les colonnes DateTime (sans timezone=True).
    """
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


@router.post("/repos/refresh", response_model=GithubRepoRefreshResponse)
async def refresh_github_repos(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Synchronise le cache depuis GitHub (/user/repos paginé).

    - Garde d'âge 30s : skip si fetched_at récent.
    - Vérifie le token d'abord ; si absent → error, 0 appel.
    - Sur erreur HTTP/réseau → error, cache inchangé (JAMAIS vidé).
    """
    settings = get_settings()
    token = settings.github_token or await load_db_token(db)
    token_configured = bool(token)

    latest = (await db.execute(
        select(GithubRepoCache).order_by(GithubRepoCache.fetched_at.desc()).limit(1)
    )).scalar_one_or_none()
    if latest and latest.fetched_at:
        age = (utcnow() - latest.fetched_at).total_seconds()
        if age < REFRESH_MIN_AGE_SECONDS:
            return GithubRepoRefreshResponse(
                synced=0, total=0, fetched_at=_iso(latest.fetched_at),
                token_configured=token_configured, skipped=True,
            )

    if not token_configured:
        return GithubRepoRefreshResponse(
            synced=0, total=0, fetched_at=_iso(latest.fetched_at if latest else None) or "",
            token_configured=False, error="Aucun token GitHub configuré",
        )

    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
    all_repos = []
    url = "https://api.github.com/user/repos?affiliation=owner,organization_member&per_page=100&sort=updated&direction=desc"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15), verify=False) as client:
            while url and len(all_repos) < GITHUB_MAX_REPOS:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
                    reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
                    when = datetime.fromtimestamp(reset, tz=timezone.utc).strftime("%H:%M") if reset else "bientôt"
                    return GithubRepoRefreshResponse(
                        synced=0, total=0,
                        fetched_at=_iso(latest.fetched_at if latest else None) or "",
                        token_configured=True,
                        error=f"Rate limit GitHub atteint, réessayez vers {when}",
                    )
                if resp.status_code == 401:
                    return GithubRepoRefreshResponse(
                        synced=0, total=0,
                        fetched_at=_iso(latest.fetched_at if latest else None) or "",
                        token_configured=True,
                        error="Token GitHub invalide ou expiré (401)",
                    )
                if resp.status_code >= 400:
                    return GithubRepoRefreshResponse(
                        synced=0, total=0,
                        fetched_at=_iso(latest.fetched_at if latest else None) or "",
                        token_configured=True,
                        error=f"GitHub a répondu {resp.status_code}",
                    )
                all_repos.extend(resp.json())
                url = _parse_link_next(resp.headers.get("Link", ""))
    except httpx.TimeoutException:
        return GithubRepoRefreshResponse(
            synced=0, total=0,
            fetched_at=_iso(latest.fetched_at if latest else None) or "",
            token_configured=True, error="GitHub inaccessible (timeout)",
        )
    except Exception as e:
        return GithubRepoRefreshResponse(
            synced=0, total=0,
            fetched_at=_iso(latest.fetched_at if latest else None) or "",
            token_configured=True, error=f"GitHub inaccessible ({e})",
        )

    now = utcnow()
    synced = 0
    for r in all_repos:
        repo_id = r.get("id")
        if repo_id is None:
            continue
        existing = (await db.execute(
            select(GithubRepoCache).where(GithubRepoCache.repo_id == repo_id)
        )).scalar_one_or_none()
        updated_str = r.get("updated_at")
        updated_dt = _parse_iso(updated_str) if updated_str else None
        if existing:
            existing.name = r.get("name", existing.name)
            existing.full_name = r.get("full_name", existing.full_name)
            existing.owner = (r.get("owner") or {}).get("login", existing.owner)
            existing.html_url = r.get("html_url", existing.html_url)
            existing.clone_url = r.get("clone_url", existing.clone_url)
            existing.description = r.get("description")
            existing.default_branch = r.get("default_branch")
            existing.fork = r.get("fork", False)
            existing.archived = r.get("archived", False)
            existing.private = r.get("private", False)
            existing.updated_at_github = updated_dt
            existing.fetched_at = now
        else:
            db.add(GithubRepoCache(
                repo_id=repo_id,
                name=r.get("name", ""),
                full_name=r.get("full_name", ""),
                owner=(r.get("owner") or {}).get("login", ""),
                html_url=r.get("html_url", ""),
                clone_url=r.get("clone_url", ""),
                description=r.get("description"),
                default_branch=r.get("default_branch"),
                fork=r.get("fork", False),
                archived=r.get("archived", False),
                private=r.get("private", False),
                updated_at_github=updated_dt,
                fetched_at=now,
            ))
        synced += 1
    await db.commit()

    return GithubRepoRefreshResponse(
        synced=synced, total=synced, fetched_at=_iso(now),
        token_configured=True, error=None,
    )


@router.post("/prepare", response_model=GithubRepoPrepareResponse)
async def prepare_app_from_repo(
    payload: GithubRepoPrepareRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Prépare la création d'une app depuis un repo GitHub du cache.

    1. trouve le repo dans le cache (404 sinon)
    2. slug = slugify(repo.name) ; 409 si slug déjà pris par une app
    3. alloue ports via allocate_ports_for_app (idempotent) ; 409 si pool saturé
    """
    cached = (await db.execute(
        select(GithubRepoCache).where(GithubRepoCache.full_name == payload.repo_full_name)
    )).scalar_one_or_none()
    if not cached:
        raise HTTPException(
            status_code=404,
            detail=f"Repo '{payload.repo_full_name}' non trouvé dans le cache. Synchronisez d'abord.",
        )

    slug = slugify_repo(cached.name)
    if not slug:
        raise HTTPException(status_code=400, detail=f"Slug dérivé vide pour le repo '{cached.name}'")

    existing_app = (await db.execute(
        select(Application).where(Application.slug == slug)
    )).scalar_one_or_none()
    if existing_app:
        raise HTTPException(
            status_code=409,
            detail=f"Slug '{slug}' déjà utilisé par une app existante",
        )

    host = get_settings().get_hub_host()
    try:
        alloc, reused = await allocate_ports_for_app(db, slug, host, get_settings())
    except PortPoolExhausted as e:
        raise HTTPException(status_code=409, detail=f"Pool de ports saturé : {e}")

    return GithubRepoPrepareResponse(
        slug=slug,
        name=cached.name,
        git_repo_url=cached.clone_url,
        api_port=alloc.api_port,
        frontend_port=alloc.frontend_port,
        frontend_https_port=alloc.frontend_https_port,
        host=host,
        ports_reused=reused,
    )
