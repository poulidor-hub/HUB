"""Tests pour l'endpoint /api/apps/github/* et le cache des repos."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.auth import create_access_token
from app.models import GithubRepoCache
from tests.conftest import get_auth_header


class TestGithubRepoCacheModel:
    """Tests du modèle GithubRepoCache (Task 1)."""

    @pytest.mark.asyncio
    async def test_table_exists_and_can_insert(self, async_session):
        """La table github_repo_cache existe et on peut insérer/lire."""
        repo = GithubRepoCache(
            repo_id=123456,
            name="thetipitap",
            full_name="MYCOMPANY/thetipitap",
            owner="MYCOMPANY",
            html_url="https://github.com/MYCOMPANY/thetipitap",
            clone_url="https://github.com/MYCOMPANY/thetipitap.git",
            description="Gestion des tipis",
            default_branch="main",
            fork=False,
            archived=False,
            private=True,
        )
        async_session.add(repo)
        await async_session.commit()

        result = await async_session.execute(
            select(GithubRepoCache).where(GithubRepoCache.repo_id == 123456)
        )
        fetched = result.scalar_one()
        assert fetched.name == "thetipitap"
        assert fetched.owner == "MYCOMPANY"
        assert fetched.clone_url.endswith(".git")
        assert fetched.fetched_at is not None
