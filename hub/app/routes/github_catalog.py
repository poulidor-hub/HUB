"""Catalogue GitHub : découverte et sélection visuelle de repos.

Endpoints admin-only sous /api/apps/github :
- GET  /repos          : liste le cache (0 appel GitHub) + already_integrated
- POST /repos/refresh  : synchronise le cache depuis GitHub (garde d'âge 30s)
- POST /prepare        : pré-remplit la création d'app depuis un repo
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.github_client import parse_github_url, load_db_token
from app.models import Application, GithubRepoCache, User
from app.schemas import (
    GithubRepo,
    GithubRepoListResponse,
)

router = APIRouter(prefix="/api/apps/github", tags=["github"])


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
    token = await load_db_token(db)
    return GithubRepoListResponse(
        repos=repos,
        total=len(repos),
        fetched_at=_iso(fetched_at),
        token_configured=token is not None,
    )
