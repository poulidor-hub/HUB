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


class TestGithubCatalogSchemas:
    """Validation des schemas Pydantic (Task 3)."""

    def test_github_repo_minimal(self):
        from app.schemas import GithubRepo
        r = GithubRepo(
            name="x", full_name="o/x", owner="o",
            html_url="https://github.com/o/x",
            git_url="https://github.com/o/x.git",
            fork=False, archived=False, private=False,
        )
        assert r.name == "x"
        assert r.already_integrated is False

    def test_github_repo_list_response(self):
        from app.schemas import GithubRepoListResponse, GithubRepo
        r = GithubRepoListResponse(repos=[], total=0, token_configured=True)
        assert r.fetched_at is None

    def test_github_repo_prepare_request(self):
        from app.schemas import GithubRepoPrepareRequest
        p = GithubRepoPrepareRequest(repo_full_name="o/repo")
        assert p.repo_full_name == "o/repo"

    def test_github_repo_prepare_response(self):
        from app.schemas import GithubRepoPrepareResponse
        p = GithubRepoPrepareResponse(
            slug="x", name="X", git_repo_url="https://x.git",
            api_port=8000, frontend_port=3000, frontend_https_port=3001,
            host="hub.local", ports_reused=False,
        )
        assert p.ports_reused is False

    def test_github_repo_refresh_response(self):
        from app.schemas import GithubRepoRefreshResponse
        r = GithubRepoRefreshResponse(synced=5, total=5, fetched_at="now", token_configured=True)
        assert r.error is None
        assert r.skipped is False

    def test_app_create_simplified_has_git_repo_url(self):
        from app.schemas import AppCreateSimplified
        data = AppCreateSimplified(name="Xx", slug="xx", api_port=8000, git_repo_url="https://x.git")
        assert data.git_repo_url == "https://x.git"

    def test_app_create_simplified_git_repo_url_optional(self):
        from app.schemas import AppCreateSimplified
        data = AppCreateSimplified(name="Xx", slug="xx", api_port=8000)
        assert data.git_repo_url is None
