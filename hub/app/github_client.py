"""Client GitHub partagé (factorisé depuis routes/apps_health.py).

Centralise : headers HTTP avec token, fetch JSON/raw, parsing d'URL GitHub,
slugification. Le token est lu depuis settings (env) puis DB (AppSetting).
"""
import re
import unicodedata
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker
from app.models import AppSetting

_global_token_cache: Optional[str] = None
_token_cache_valid = False


async def load_db_token(db: Optional[AsyncSession] = None) -> Optional[str]:
    """Charge le GITHUB_TOKEN depuis la DB (cache global simple)."""
    global _global_token_cache, _token_cache_valid
    if _token_cache_valid and db is None:
        return _global_token_cache

    if db is not None:
        result = await db.execute(select(AppSetting).where(AppSetting.key == "github_token"))
        setting = result.scalar_one_or_none()
        token = setting.value if setting else None
        _global_token_cache = token
        _token_cache_valid = True
        return token

    try:
        async with async_session_maker() as session:
            result = await session.execute(select(AppSetting).where(AppSetting.key == "github_token"))
            setting = result.scalar_one_or_none()
            _global_token_cache = setting.value if setting else None
            _token_cache_valid = True
            return _global_token_cache
    except Exception:
        return None


def invalidate_token_cache() -> None:
    global _token_cache_valid, _global_token_cache
    _token_cache_valid = False
    _global_token_cache = None


async def github_headers(db: Optional[AsyncSession] = None) -> dict:
    """Headers HTTP pour l'API GitHub. Priorité : settings.github_token puis DB."""
    headers = {"Accept": "application/vnd.github+json"}
    token = get_settings().github_token or await load_db_token(db)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def fetch_github_json(url: str, db: Optional[AsyncSession] = None) -> tuple:
    """GET JSON depuis l'API GitHub. Retourne (data, status) ou (None, status)."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10), verify=False) as client:
            resp = await client.get(url, headers=await github_headers(db))
            return resp.json() if 200 <= resp.status_code < 300 else None, resp.status_code
    except Exception:
        return None, 0


async def fetch_github_raw(url: str, db: Optional[AsyncSession] = None) -> Optional[str]:
    """GET contenu brut depuis raw.githubusercontent.com."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10), verify=False) as client:
            resp = await client.get(url, headers=await github_headers(db))
            return resp.text if 200 <= resp.status_code < 300 else None
    except Exception:
        return None


def parse_github_url(git_repo_url: str) -> Optional[tuple[str, str]]:
    """Extrait (owner, repo) d'une URL GitHub. Retourne None si pas GitHub."""
    if not git_repo_url:
        return None
    m = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$', git_repo_url)
    if not m:
        return None
    return m.group(1), m.group(2)


def slugify_repo(name: str) -> str:
    """Slugifie un nom de repo : minuscules, accents retirés, non-alphanum → '-'.

    Garantit le pattern ^[a-z0-9-]+$ attendu par AppCreateSimplified.slug.
    """
    if not name:
        return ""
    # retirer les accents
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    # minuscules + remplacer tout non [a-z0-9] par '-'
    slug = re.sub(r'[^a-z0-9]+', '-', ascii_only.lower()).strip('-')
    return slug
