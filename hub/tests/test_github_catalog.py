"""Tests pour l'endpoint /api/apps/github/* et le cache des repos."""
import pytest
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient
from sqlalchemy import select
from unittest.mock import MagicMock, AsyncMock, patch

from app.auth import create_access_token
from app.models import GithubRepoCache, Application, utcnow
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


class TestListRepos:
    """Tests de GET /api/apps/github/repos (Task 4)."""

    @pytest.mark.asyncio
    async def test_list_empty_cache(self, client: AsyncClient, admin_user):
        """Cache vide → liste vide + fetched_at None."""
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.get("/api/apps/github/repos", headers=get_auth_header(token))
        assert r.status_code == 200
        body = r.json()
        assert body["repos"] == []
        assert body["total"] == 0
        assert body["fetched_at"] is None

    @pytest.mark.asyncio
    async def test_list_with_cache_and_integration_flag(self, client: AsyncClient, admin_user, async_session):
        """already_integrated calculé depuis git_repo_url des apps."""
        async_session.add(GithubRepoCache(
            repo_id=1, name="thetipitap", full_name="MYCOMPANY/thetipitap",
            owner="MYCOMPANY", html_url="https://github.com/MYCOMPANY/thetipitap",
            clone_url="https://github.com/MYCOMPANY/thetipitap.git",
        ))
        async_session.add(GithubRepoCache(
            repo_id=2, name="catalog", full_name="MYCOMPANY/catalog",
            owner="MYCOMPANY", html_url="https://github.com/MYCOMPANY/catalog",
            clone_url="https://github.com/MYCOMPANY/catalog.git",
        ))
        async_session.add(Application(
            name="Catalog", slug="catalog", api_url="http://h:8100",
            git_repo_url="https://github.com/MYCOMPANY/catalog.git",
        ))
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.get("/api/apps/github/repos", headers=get_auth_header(token))
        body = r.json()
        by_name = {x["name"]: x for x in body["repos"]}
        assert by_name["thetipitap"]["already_integrated"] is False
        assert by_name["catalog"]["already_integrated"] is True

    @pytest.mark.asyncio
    async def test_list_requires_admin(self, client: AsyncClient, test_user):
        """Non-admin → 403."""
        token, _, _ = create_access_token(username=test_user.username, apps={})
        r = await client.get("/api/apps/github/repos", headers=get_auth_header(token))
        assert r.status_code == 403


def _repo_payload(repo_id, name="x", owner="MYCOMPANY"):
    return {
        "id": repo_id,
        "name": name,
        "full_name": f"{owner}/{name}",
        "owner": {"login": owner},
        "html_url": f"https://github.com/{owner}/{name}",
        "clone_url": f"https://github.com/{owner}/{name}.git",
        "description": None,
        "default_branch": "main",
        "fork": False,
        "archived": False,
        "private": True,
        "updated_at": "2026-07-20T00:00:00Z",
    }


class TestRefresh:
    """Tests de POST /api/apps/github/repos/refresh (Task 5)."""

    @pytest.mark.asyncio
    async def test_refresh_success_upserts(self, client: AsyncClient, admin_user, async_session, settings):
        """Refresh réussi → upsert + retourne synced/total."""
        settings.github_token = "ghp_test"
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = [_repo_payload(1, "thetipitap"), _repo_payload(2, "catalog")]
        mock_resp.headers = {}
        with patch("app.routes.github_catalog.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            token, _, _ = create_access_token(username=admin_user.username, apps={})
            r = await client.post("/api/apps/github/repos/refresh", headers=get_auth_header(token))

        assert r.status_code == 200
        body = r.json()
        assert body["synced"] == 2
        assert body["error"] is None
        rows = (await async_session.execute(select(GithubRepoCache))).scalars().all()
        assert {x.name for x in rows} == {"thetipitap", "catalog"}

    @pytest.mark.asyncio
    async def test_refresh_no_token(self, client: AsyncClient, admin_user, settings):
        """Pas de token → error + token_configured False + 0 appel."""
        settings.github_token = ""
        with patch("app.routes.github_catalog.load_db_token", AsyncMock(return_value=None)):
            token, _, _ = create_access_token(username=admin_user.username, apps={})
            r = await client.post("/api/apps/github/repos/refresh", headers=get_auth_header(token))
        body = r.json()
        assert body["token_configured"] is False
        assert "token" in body["error"].lower()
        assert body["synced"] == 0

    @pytest.mark.asyncio
    async def test_refresh_rate_limit_keeps_cache(self, client: AsyncClient, admin_user, async_session, settings):
        """Rate limit (403) → error + cache existant préservé."""
        settings.github_token = "ghp_test"
        async_session.add(GithubRepoCache(
            repo_id=99, name="existing", full_name="MYCOMPANY/existing",
            owner="MYCOMPANY", html_url="x", clone_url="x.git",
            fetched_at=utcnow() - timedelta(minutes=5),
        ))
        await async_session.commit()

        mock_resp = MagicMock(status_code=403, headers={
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(int(datetime.now(timezone.utc).timestamp()) + 1800),
        })
        mock_resp.json.return_value = {}
        with patch("app.routes.github_catalog.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            token, _, _ = create_access_token(username=admin_user.username, apps={})
            r = await client.post("/api/apps/github/repos/refresh", headers=get_auth_header(token))
        body = r.json()
        assert body["synced"] == 0
        assert "rate limit" in body["error"].lower()
        rows = (await async_session.execute(select(GithubRepoCache))).scalars().all()
        assert any(x.name == "existing" for x in rows)

    @pytest.mark.asyncio
    async def test_refresh_age_guard_skips(self, client: AsyncClient, admin_user, async_session, settings):
        """Refresh récent (< 30s) → skipped."""
        settings.github_token = "ghp_test"
        recent = GithubRepoCache(
            repo_id=1, name="x", full_name="o/x", owner="o",
            html_url="x", clone_url="x.git",
            fetched_at=utcnow(),
        )
        async_session.add(recent)
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.post("/api/apps/github/repos/refresh", headers=get_auth_header(token))
        body = r.json()
        assert body["skipped"] is True
        assert body["synced"] == 0


class TestPrepare:
    """Tests de POST /api/apps/github/prepare (Task 6)."""

    @pytest.mark.asyncio
    async def test_prepare_success(self, client: AsyncClient, admin_user, async_session, settings, monkeypatch):
        """Préparation réussie : slug dérivé + ports alloués + git_repo_url."""
        settings.github_token = "ghp_test"
        # Préflight mocké : aucun appel réseau en test (cf. Task 2 plan)
        import app.routes.github_catalog as gc
        from app.repo_access import RepoAccessResult

        async def _access_ok(clone_url, token):
            return RepoAccessResult(True, "ok", "Accès au dépôt vérifié.")
        monkeypatch.setattr(gc, "check_repo_access", _access_ok)
        async_session.add(GithubRepoCache(
            repo_id=1, name="Mon-Projet_Cool", full_name="MYCOMPANY/Mon-Projet_Cool",
            owner="MYCOMPANY", html_url="https://github.com/MYCOMPANY/Mon-Projet_Cool",
            clone_url="https://github.com/MYCOMPANY/Mon-Projet_Cool.git",
        ))
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.post(
            "/api/apps/github/prepare",
            headers=get_auth_header(token),
            json={"repo_full_name": "MYCOMPANY/Mon-Projet_Cool"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["slug"] == "mon-projet-cool"
        assert body["git_repo_url"] == "https://github.com/MYCOMPANY/Mon-Projet_Cool.git"
        assert body["name"] == "Mon-Projet_Cool"
        assert isinstance(body["api_port"], int)
        assert body["ports_reused"] is False

    @pytest.mark.asyncio
    async def test_prepare_slug_collision_409(self, client: AsyncClient, admin_user, async_session, settings):
        """Slug déjà utilisé par une app → 409."""
        settings.github_token = "ghp_test"
        async_session.add(GithubRepoCache(
            repo_id=1, name="thetipitap", full_name="MYCOMPANY/thetipitap",
            owner="MYCOMPANY", html_url="x", clone_url="x.git",
        ))
        async_session.add(Application(name="Existing", slug="thetipitap", api_url="http://h:8000"))
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.post(
            "/api/apps/github/prepare",
            headers=get_auth_header(token),
            json={"repo_full_name": "MYCOMPANY/thetipitap"},
        )
        assert r.status_code == 409
        assert "thetipitap" in r.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_prepare_repo_not_in_cache_404(self, client: AsyncClient, admin_user, settings):
        """Repo absent du cache → 404."""
        settings.github_token = "ghp_test"
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.post(
            "/api/apps/github/prepare",
            headers=get_auth_header(token),
            json={"repo_full_name": "MYCOMPANY/inconnu"},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_prepare_ports_idempotent(self, client: AsyncClient, admin_user, async_session, settings, monkeypatch):
        """Deuxième prepare même slug → ports_reused True, mêmes ports."""
        settings.github_token = "ghp_test"
        import app.routes.github_catalog as gc
        from app.repo_access import RepoAccessResult

        async def _access_ok(clone_url, token):
            return RepoAccessResult(True, "ok", "Accès au dépôt vérifié.")
        monkeypatch.setattr(gc, "check_repo_access", _access_ok)
        async_session.add(GithubRepoCache(
            repo_id=1, name="x", full_name="o/x", owner="o",
            html_url="x", clone_url="x.git",
        ))
        await async_session.commit()
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r1 = await client.post("/api/apps/github/prepare", headers=get_auth_header(token), json={"repo_full_name": "o/x"})
        r2 = await client.post("/api/apps/github/prepare", headers=get_auth_header(token), json={"repo_full_name": "o/x"})
        b1, b2 = r1.json(), r2.json()
        assert b1["ports_reused"] is False
        assert b2["ports_reused"] is True
        assert (b1["api_port"], b1["frontend_port"]) == (b2["api_port"], b2["frontend_port"])


class TestRepoAccessPreflight:
    """Préflight d'accès Git : prepare enrichi + POST /check-access (spec 2026-09-17)."""

    @staticmethod
    def _add_cache(async_session, repo_id: int, full_name: str):
        async_session.add(GithubRepoCache(
            repo_id=repo_id, name=full_name.split("/")[1], full_name=full_name,
            owner=full_name.split("/")[0],
            html_url=f"https://github.com/{full_name}",
            clone_url=f"https://github.com/{full_name}.git",
        ))

    @pytest.mark.asyncio
    async def test_prepare_includes_repo_access_ok(self, client, admin_user, async_session, settings, monkeypatch):
        """prepare renvoie repo_access (mocké ok) — le formulaire prévient tôt."""
        settings.github_token = "ghp_test"
        self._add_cache(async_session, 901, "o/AccessOk")
        await async_session.commit()

        import app.routes.github_catalog as gc
        from app.repo_access import RepoAccessResult

        async def _access(clone_url, token):
            return RepoAccessResult(True, "ok", "Accès au dépôt vérifié.")
        monkeypatch.setattr(gc, "check_repo_access", _access)

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.post("/api/apps/github/prepare",
                              headers=get_auth_header(token),
                              json={"repo_full_name": "o/AccessOk"})
        assert r.status_code == 200
        assert r.json()["repo_access"]["ok"] is True
        assert r.json()["repo_access"]["reason_key"] == "ok"

    @pytest.mark.asyncio
    async def test_prepare_includes_repo_access_denied(self, client, admin_user, async_session, settings, monkeypatch):
        """prepare renvoie repo_access denied — aucun blocage côté prepare (UX only)."""
        settings.github_token = "ghp_test"
        self._add_cache(async_session, 902, "o/AccessKo")
        await async_session.commit()

        import app.routes.github_catalog as gc
        from app.repo_access import RepoAccessResult

        async def _access(clone_url, token):
            return RepoAccessResult(False, "access_denied",
                                    "Le token configuré ne peut pas accéder à ce dépôt privé.")
        monkeypatch.setattr(gc, "check_repo_access", _access)

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.post("/api/apps/github/prepare",
                              headers=get_auth_header(token),
                              json={"repo_full_name": "o/AccessKo"})
        assert r.status_code == 200
        assert r.json()["repo_access"]["ok"] is False
        assert r.json()["repo_access"]["reason_key"] == "access_denied"

    @pytest.mark.asyncio
    async def test_check_access_by_full_name(self, client, admin_user, async_session, settings, monkeypatch):
        """check-access résout repo_full_name via le cache et délègue au module."""
        settings.github_token = "ghp_test"
        self._add_cache(async_session, 903, "o/CacheTest")
        await async_session.commit()

        import app.routes.github_catalog as gc
        from app.repo_access import RepoAccessResult
        seen = {}

        async def _access(clone_url, token):
            seen["clone_url"] = clone_url
            return RepoAccessResult(True, "ok", "Accès au dépôt vérifié.")
        monkeypatch.setattr(gc, "check_repo_access", _access)

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.post("/api/apps/github/check-access",
                              headers=get_auth_header(token),
                              json={"repo_full_name": "o/CacheTest"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert seen["clone_url"] == "https://github.com/o/CacheTest.git"

    @pytest.mark.asyncio
    async def test_check_access_by_clone_url(self, client, admin_user, settings, monkeypatch):
        """check-access accepte une URL directe (URL Git éditée dans le formulaire)."""
        import app.routes.github_catalog as gc
        from app.repo_access import RepoAccessResult

        async def _access(clone_url, token):
            return RepoAccessResult(False, "access_denied", "Refus.")
        monkeypatch.setattr(gc, "check_repo_access", _access)

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.post("/api/apps/github/check-access",
                              headers=get_auth_header(token),
                              json={"clone_url": "https://github.com/other/direct.git"})
        assert r.status_code == 200
        assert r.json()["ok"] is False

    @pytest.mark.asyncio
    async def test_check_access_unknown_repo_404(self, client, admin_user, settings):
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.post("/api/apps/github/check-access",
                              headers=get_auth_header(token),
                              json={"repo_full_name": "o/Inconnu"})
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_check_access_requires_payload(self, client, admin_user, settings):
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.post("/api/apps/github/check-access",
                              headers=get_auth_header(token), json={})
        assert r.status_code == 400


class TestAlreadyIntegratedBySlug:
    """Bug 2026-10-03 : un repo dont le slug est pris par une app SANS
    git_repo_url apparaissait « Dispo » puis 409 au clic sur « + Ajouter »
    (cas exemple-app). Le croisement doit aussi regarder le slug dérivé."""

    @pytest.mark.asyncio
    async def test_slug_pris_sans_git_repo_url_marque_integre(self, client, admin_user, async_session):
        async_session.add(GithubRepoCache(
            repo_id=30, name="exemple-app", full_name="Poutchouli/exemple-app",
            owner="Poutchouli", html_url="https://github.com/Poutchouli/exemple-app",
            clone_url="https://github.com/Poutchouli/exemple-app.git",
        ))
        # App existante déployée manuellement : pas de git_repo_url
        async_session.add(Application(
            name="exemple-app", slug="exemple-app", api_url="http://h:9001",
            git_repo_url=None,
        ))
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.get("/api/apps/github/repos", headers=get_auth_header(token))
        body = r.json()
        by_name = {x["name"]: x for x in body["repos"]}
        assert by_name["exemple-app"]["already_integrated"] is True

    @pytest.mark.asyncio
    async def test_slug_libre_reste_dispo(self, client, admin_user, async_session):
        async_session.add(GithubRepoCache(
            repo_id=31, name="NouvelOutil", full_name="Poutchouli/NouvelOutil",
            owner="Poutchouli", html_url="https://github.com/Poutchouli/NouvelOutil",
            clone_url="https://github.com/Poutchouli/NouvelOutil.git",
        ))
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.get("/api/apps/github/repos", headers=get_auth_header(token))
        by_name = {x["name"]: x for x in r.json()["repos"]}
        assert by_name["NouvelOutil"]["already_integrated"] is False


class TestGlobalFetchedAt:
    """Bug campagne de tests 2026-10-03 : un filtrage sans résultat affichait
    « jamais synchronisé » — fetched_at était calculé sur le résultat filtré
    (max d'une liste vide = None) au lieu du cache global."""

    @pytest.mark.asyncio
    async def test_filtered_empty_result_keeps_global_fetched_at(self, client, admin_user, async_session):
        async_session.add(GithubRepoCache(
            repo_id=40, name="exemple-app", full_name="Poutchouli/exemple-app",
            owner="Poutchouli", html_url="https://github.com/Poutchouli/exemple-app",
            clone_url="https://github.com/Poutchouli/exemple-app.git",
            fetched_at=datetime(2026, 10, 3, 16, 37, 7),
        ))
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.get("/api/apps/github/repos", headers=get_auth_header(token),
                             params={"search": "zzzaucunrepo"})
        body = r.json()
        assert body["total"] == 0
        assert body["repos"] == []
        # fetched_at = celui du cache global (jamais None si le cache existe)
        assert body["fetched_at"] is not None
        assert "2026-10-03" in body["fetched_at"]


class TestIntegratedAppSlug:
    """Bug campagne de tests 2026-10-03 : « Voir l'app » cherchait le slug
    dérivé du repo (poulidor / exemple-app) au lieu du slug de l'app liée
    (hub / exemple-app) → 0 résultat dans l'onglet Applications."""

    @pytest.mark.asyncio
    async def test_slug_via_git_repo_url(self, client, admin_user, async_session):
        """Repo lié par git_repo_url à une app dont le slug diffère : l'app
        `hub` pointe le méta-repo POULIDOR — integrated_app_slug doit valoir
        `hub`, pas `poulidor`."""
        async_session.add(GithubRepoCache(
            repo_id=50, name="POULIDOR", full_name="POULIDOR-Audit-Developpement/POULIDOR",
            owner="POULIDOR-Audit-Developpement",
            html_url="https://github.com/POULIDOR-Audit-Developpement/POULIDOR",
            clone_url="https://github.com/POULIDOR-Audit-Developpement/POULIDOR.git",
        ))
        async_session.add(Application(
            name="Hub", slug="hub", api_url="http://h:8000",
            git_repo_url="https://github.com/POULIDOR-Audit-Developpement/POULIDOR.git",
        ))
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.get("/api/apps/github/repos", headers=get_auth_header(token))
        by_name = {x["name"]: x for x in r.json()["repos"]}
        assert by_name["POULIDOR"]["already_integrated"] is True
        assert by_name["POULIDOR"]["integrated_app_slug"] == "hub"

    @pytest.mark.asyncio
    async def test_slug_via_app_slug(self, client, admin_user, async_session):
        async_session.add(GithubRepoCache(
            repo_id=51, name="exemple-app", full_name="Poutchouli/exemple-app",
            owner="Poutchouli", html_url="https://github.com/Poutchouli/exemple-app",
            clone_url="https://github.com/Poutchouli/exemple-app.git",
        ))
        async_session.add(Application(
            name="exemple-app", slug="exemple-app", api_url="http://h:9001",
            git_repo_url=None,
        ))
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.get("/api/apps/github/repos", headers=get_auth_header(token))
        by_name = {x["name"]: x for x in r.json()["repos"]}
        assert by_name["exemple-app"]["integrated_app_slug"] == "exemple-app"

    @pytest.mark.asyncio
    async def test_none_when_repo_free(self, client, admin_user, async_session):
        async_session.add(GithubRepoCache(
            repo_id=52, name="NouvelOutil", full_name="Poutchouli/NouvelOutil",
            owner="Poutchouli", html_url="https://github.com/Poutchouli/NouvelOutil",
            clone_url="https://github.com/Poutchouli/NouvelOutil.git",
        ))
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        r = await client.get("/api/apps/github/repos", headers=get_auth_header(token))
        by_name = {x["name"]: x for x in r.json()["repos"]}
        assert by_name["NouvelOutil"]["already_integrated"] is False
        assert by_name["NouvelOutil"]["integrated_app_slug"] is None
