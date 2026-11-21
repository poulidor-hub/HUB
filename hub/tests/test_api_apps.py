# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests d'intégration pour les endpoints API des applications
"""
import pytest
from httpx import AsyncClient

from app.auth import create_access_token, get_password_hash
from app.models import User, Application, UserAppRole
from tests.conftest import get_auth_header


class TestAppsEndpoints:
    """Tests pour les endpoints /api/apps/*"""
    
    @pytest.mark.asyncio
    async def test_list_apps_as_admin(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Admin voit toutes les apps"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.get(
            "/api/apps/",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        apps = response.json()
        assert isinstance(apps, list)
        assert len(apps) >= 1
    
    @pytest.mark.asyncio
    async def test_list_apps_as_user(self, client: AsyncClient, user_with_app_access):
        """User voit seulement ses apps"""
        user, app = user_with_app_access
        token, _, _ = create_access_token(
            username=user.username,
            apps={app.slug: "user"}
        )
        
        response = await client.get(
            "/api/apps/",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_list_apps_unauthorized(self, client: AsyncClient):
        """Liste des apps sans authentification échoue"""
        response = await client.get("/api/apps/")
        
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_list_all_apps_admin_only(self, client: AsyncClient, admin_user: User, test_app: Application, admin_with_app):
        """Seul admin peut voir /all"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.get(
            "/api/apps/all",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_list_all_apps_forbidden_for_user(self, client: AsyncClient, test_user: User):
        """User ne peut pas voir /all"""
        token, _, _ = create_access_token(
            username=test_user.username,
            apps={}
        )
        
        response = await client.get(
            "/api/apps/all",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_create_app(self, client: AsyncClient, admin_user: User):
        """Création d'une application par admin"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Nouvelle App",
                "slug": "nouvelle-app",
                "description": "Description de la nouvelle app",
                "api_port": 8080,
                "frontend_port": 3000
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["application"]["slug"] == "nouvelle-app"
        # Note: service_account n'est plus créé automatiquement (l'app s'auto-enregistre)
    
    @pytest.mark.asyncio
    async def test_create_app_duplicate_slug(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Création avec slug existant échoue"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "App Duplicate",
                "slug": test_app.slug,  # Slug existant
                "description": "Test",
                "api_port": 8080
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 400
    
    @pytest.mark.asyncio
    async def test_create_app_forbidden_for_user(self, client: AsyncClient, test_user: User):
        """User ne peut pas créer d'app"""
        token, _, _ = create_access_token(
            username=test_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Test App",
                "slug": "test-app-new",
                "description": "Test",
                "api_port": 8080
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_get_app_by_id(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Récupérer une app par son ID"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.get(
            f"/api/apps/{test_app.id}",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["slug"] == test_app.slug
    
    @pytest.mark.asyncio
    async def test_get_app_not_found(self, client: AsyncClient, admin_user: User):
        """App inexistante retourne 404"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.get(
            "/api/apps/99999",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 404
    
    @pytest.mark.asyncio
    async def test_update_app(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Mise à jour d'une app"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.patch(
            f"/api/apps/{test_app.id}",
            json={
                "description": "Nouvelle description"
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Nouvelle description"
    
    @pytest.mark.asyncio
    async def test_delete_app(self, client: AsyncClient, admin_user: User, async_session):
        """Suppression d'une app"""
        # Créer une app à supprimer
        app_to_delete = Application(
            name="App to Delete",
            slug="app-to-delete",
            api_url="http://localhost:9999",
            is_active=True
        )
        async_session.add(app_to_delete)
        await async_session.commit()
        await async_session.refresh(app_to_delete)
        
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.delete(
            f"/api/apps/{app_to_delete.id}",
            headers=get_auth_header(token)
        )
        
        assert response.status_code in [200, 204]


class TestAppUserAssignment:
    """Tests pour l'assignation utilisateurs aux apps"""
    
    @pytest.mark.asyncio
    async def test_set_app_users(self, client: AsyncClient, admin_user: User, test_user: User, test_app: Application):
        """Définir les utilisateurs d'une app"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            f"/api/apps/{test_app.id}/users",
            json=[test_user.id],
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
    
    @pytest.mark.asyncio
    async def test_set_multiple_users(self, client: AsyncClient, admin_user: User, test_user: User, test_app: Application):
        """Définir plusieurs utilisateurs pour une app"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            f"/api/apps/{test_app.id}/users",
            json=[test_user.id, admin_user.id],
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_clear_app_users(self, client: AsyncClient, admin_user: User, user_with_app_access):
        """Vider les utilisateurs d'une app"""
        user, app = user_with_app_access
        
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            f"/api/apps/{app.id}/users",
            json=[],  # Liste vide pour supprimer tous les accès
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_app_users_in_all_response(self, client: AsyncClient, admin_user: User, user_with_app_access, admin_with_app):
        """Vérifier que /all retourne les utilisateurs des apps"""
        _, app = user_with_app_access
        
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.get(
            "/api/apps/all",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        apps = response.json()
        assert isinstance(apps, list)


class TestIsSharedService:
    """Tests pour le paramètre is_shared_service lors de la création d'applications"""
    
    @pytest.mark.asyncio
    async def test_create_app_with_is_shared_service_true(self, client: AsyncClient, admin_user: User):
        """Création d'une app avec is_shared_service=True via POST /api/apps/"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Shared API Test",
                "slug": "shared-api-test",
                "description": "API partagée pour test",
                "api_port": 18099,
                "is_shared_service": True
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["application"]["slug"] == "shared-api-test"
        assert data["application"]["is_shared_service"] is True
    
    @pytest.mark.asyncio
    async def test_create_app_with_is_shared_service_false(self, client: AsyncClient, admin_user: User):
        """Création d'une app avec is_shared_service=False (défaut)"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Non Shared App Test",
                "slug": "non-shared-app-test",
                "description": "App non partagée",
                "api_port": 18098,
                "is_shared_service": False
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["application"]["is_shared_service"] is False
    
    @pytest.mark.asyncio
    async def test_create_app_default_is_shared_service(self, client: AsyncClient, admin_user: User):
        """Création d'une app sans spécifier is_shared_service (doit être False par défaut)"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Default Shared Test",
                "slug": "default-shared-test",
                "description": "Test valeur par défaut",
                "api_port": 18097
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["application"]["is_shared_service"] is False
    
    @pytest.mark.asyncio
    async def test_auto_register_with_is_shared(self, client: AsyncClient, admin_user: User):
        """Auto-enregistrement admin avec is_shared=True via POST /api/apps/auto-register"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/auto-register",
            json={
                "slug": "auto-shared-test",
                "name": "Auto Shared Test",
                "api_port": 18096,
                "is_shared": True
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["application"]["slug"] == "auto-shared-test"
        assert data["application"]["is_shared_service"] is True


class TestCreateAppGitRepoUrl:
    """Test de persistence git_repo_url via POST /api/apps/"""

    @pytest.mark.asyncio
    async def test_create_app_with_git_repo_url(self, client: AsyncClient, admin_user, monkeypatch):
        """POST /api/apps/ persiste git_repo_url quand fourni (option α)."""
        # Préflight mocké : ce test valide la persistence, pas l'accès réseau
        import app.routes.apps as apps_mod
        from app.hub_app_manifest import ManifestCheckResult
        from app.repo_access import RepoAccessResult

        async def _access_ok(clone_url, token):
            return RepoAccessResult(True, "ok", "Accès au dépôt vérifié.")
        monkeypatch.setattr(apps_mod, "check_repo_access", _access_ok)

        # Porte certificat (Task 8) mockée OK : branche via API + certificat valide,
        # SANS identité de manifeste (la persistence reste l'objet du test).
        async def _fetch_repo(url, db=None):
            return {"default_branch": "main"}, 200
        monkeypatch.setattr(apps_mod, "fetch_github_json", _fetch_repo)
        async def _manifest_ok(owner, repo, branch, db=None):
            return ManifestCheckResult(valid=True)
        monkeypatch.setattr(apps_mod, "check_manifest", _manifest_ok)

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        payload = {
            "name": "Mon App",
            "slug": "mon-app-gh",
            "api_port": 8201,
            "git_repo_url": "https://github.com/MYCOMPANY/mon-app.git",
        }
        r = await client.post("/api/apps/", headers=get_auth_header(token), json=payload)
        assert r.status_code == 201
        rget = await client.get("/api/apps/", headers=get_auth_header(token))
        apps = {a["slug"]: a for a in rget.json()}
        assert apps["mon-app-gh"].get("git_repo_url") == "https://github.com/MYCOMPANY/mon-app.git"
        await client.delete(f"/api/apps/{apps['mon-app-gh']['id']}", headers=get_auth_header(token))


class TestCreateAppGitPreflight:
    """Garde serveur : création bloquée (422) si le dépôt Git est inaccessible (spec 2026-09-17 §3.1)."""

    @pytest.mark.asyncio
    async def test_create_app_git_access_denied_422(self, client, admin_token, async_session, monkeypatch):
        """Refus AVANT création : aucune ligne en DB si le dépôt est inaccessible."""
        import app.routes.apps as apps_mod
        from app.repo_access import RepoAccessResult

        async def fake_access(clone_url, token):
            return RepoAccessResult(False, "access_denied",
                                    "Le token configuré ne peut pas accéder à ce dépôt privé.")
        monkeypatch.setattr(apps_mod, "check_repo_access", fake_access)

        payload = {
            "name": "Inaccessible", "slug": "inaccessible", "api_port": 8601,
            "git_repo_url": "https://github.com/o/prive.git",
        }
        r = await client.post("/api/apps/", json=payload, headers=get_auth_header(admin_token))
        assert r.status_code == 422
        assert "Dépôt Git inaccessible" in r.json()["detail"]

        from sqlalchemy import select
        rows = (await async_session.execute(
            select(Application).where(Application.slug == "inaccessible")
        )).scalars().all()
        assert rows == []

    @pytest.mark.asyncio
    async def test_create_app_git_access_ok_creates(self, client, admin_token, async_session, monkeypatch):
        import app.routes.apps as apps_mod
        from app.hub_app_manifest import ManifestCheckResult
        from app.repo_access import RepoAccessResult

        async def fake_access(clone_url, token):
            return RepoAccessResult(True, "ok", "Accès au dépôt vérifié.")
        monkeypatch.setattr(apps_mod, "check_repo_access", fake_access)

        # Porte certificat (Task 8) mockée OK — le test reste focalisé sur le
        # préflight ACCÈS (le certificat a ses propres tests).
        async def fake_fetch(url, db=None):
            return {"default_branch": "main"}, 200
        monkeypatch.setattr(apps_mod, "fetch_github_json", fake_fetch)
        async def fake_manifest(owner, repo, branch, db=None):
            return ManifestCheckResult(valid=True)
        monkeypatch.setattr(apps_mod, "check_manifest", fake_manifest)

        payload = {
            "name": "Accessible", "slug": "accessible", "api_port": 8602,
            "git_repo_url": "https://github.com/o/pub.git",
        }
        r = await client.post("/api/apps/", json=payload, headers=get_auth_header(admin_token))
        assert r.status_code == 201

    @pytest.mark.asyncio
    async def test_create_app_without_git_skips_preflight(self, client, admin_token, monkeypatch):
        """Sans git_repo_url : aucun appel au préflight (comportement inchangé)."""
        import app.routes.apps as apps_mod

        async def unexpected(clone_url, token):
            raise AssertionError("préflight ne doit pas être appelé sans git_repo_url")
        monkeypatch.setattr(apps_mod, "check_repo_access", unexpected)

        payload = {"name": "SansGit", "slug": "sansgit", "api_port": 8603}
        r = await client.post("/api/apps/", json=payload, headers=get_auth_header(admin_token))
        assert r.status_code == 201


class TestAppsStatusProbe:
    """Sonde manuelle GET /api/apps/status (bouton « Tester connectivité ») :
    une app volontairement arrêtée (conteneur Docker off) n'est pas une erreur.
    Même contrat stopped que le dashboard (routes/apps_health.py, plan
    notif-app-offline) : classification 'stopped' uniquement quand Docker est
    certain (état ≠ None et ≠ 'running'), JAMAIS sur incertitude ; un seul
    appel Docker, seulement sur le chemin d'échec ; app distante exclue."""

    @staticmethod
    def _container_state_mock(return_value):
        """Mock de get_container_state (patché sur le module route) : compte les appels."""
        calls = {"n": 0}

        def _state(slug):
            calls["n"] += 1
            return return_value

        return _state, calls

    @staticmethod
    def _patch_httpx(monkeypatch, fake_client):
        """Remplace httpx.AsyncClient (la route l'importe au niveau fonction)."""
        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: fake_client)

    @staticmethod
    def _find(data, slug):
        return next(a for a in data["apps"] if a["slug"] == slug)

    @pytest.mark.asyncio
    async def test_probe_stopped_container_reports_stopped(self, client, admin_token, test_app, monkeypatch):
        """Ping fail + conteneur 'exited' -> stopped (pas offline), summary.stopped."""
        import httpx

        class _ErrorClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                raise httpx.ConnectError("Connection refused")

        import app.routes.apps as apps_mod
        state_mock, calls = self._container_state_mock("exited")
        self._patch_httpx(monkeypatch, _ErrorClient())
        monkeypatch.setattr(apps_mod, "get_container_state", state_mock)

        resp = await client.get("/api/apps/status", headers=get_auth_header(admin_token))

        assert resp.status_code == 200
        app = self._find(resp.json(), "test-app")
        assert app["status"] == "stopped"
        assert app["error"] == "Conteneur exited"
        summary = resp.json()["summary"]
        assert summary["offline"] == 0
        assert summary["stopped"] == 1
        assert calls["n"] == 1  # un seul appel Docker, sur le chemin d'échec

    @pytest.mark.asyncio
    async def test_probe_absent_container_reports_stopped(self, client, admin_token, test_app, monkeypatch):
        """Docker joignable mais aucun conteneur -> stopped, 'Conteneur absent'."""
        import httpx

        class _ErrorClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                raise httpx.ConnectError("Connection refused")

        import app.routes.apps as apps_mod
        state_mock, _ = self._container_state_mock("absent")
        self._patch_httpx(monkeypatch, _ErrorClient())
        monkeypatch.setattr(apps_mod, "get_container_state", state_mock)

        resp = await client.get("/api/apps/status", headers=get_auth_header(admin_token))

        app = self._find(resp.json(), "test-app")
        assert app["status"] == "stopped"
        assert app["error"] == "Conteneur absent"

    @pytest.mark.asyncio
    async def test_probe_running_but_unreachable_reports_offline(self, client, admin_token, test_app, monkeypatch):
        """Conteneur 'running' mais ping fail -> vraie panne : offline (pas stopped)."""
        import httpx

        class _ErrorClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                raise httpx.ConnectError("Connection refused")

        import app.routes.apps as apps_mod
        state_mock, _ = self._container_state_mock("running")
        self._patch_httpx(monkeypatch, _ErrorClient())
        monkeypatch.setattr(apps_mod, "get_container_state", state_mock)

        resp = await client.get("/api/apps/status", headers=get_auth_header(admin_token))

        app = self._find(resp.json(), "test-app")
        assert app["status"] == "offline"
        assert resp.json()["summary"]["stopped"] == 0

    @pytest.mark.asyncio
    async def test_probe_docker_unavailable_reports_offline(self, client, admin_token, test_app, monkeypatch):
        """Docker indisponible (None) -> incertitude : fallback offline, jamais stopped."""
        import httpx

        class _ErrorClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                raise httpx.ConnectError("Connection refused")

        import app.routes.apps as apps_mod
        state_mock, _ = self._container_state_mock(None)
        self._patch_httpx(monkeypatch, _ErrorClient())
        monkeypatch.setattr(apps_mod, "get_container_state", state_mock)

        resp = await client.get("/api/apps/status", headers=get_auth_header(admin_token))

        app = self._find(resp.json(), "test-app")
        assert app["status"] == "offline"
        assert resp.json()["summary"]["stopped"] == 0

    @pytest.mark.asyncio
    async def test_probe_remote_app_reports_offline(self, client, admin_token, async_session, monkeypatch):
        """App distante : pas d'appel Docker (get_container_state jamais appelé),
        status offline même si Docker dirait 'absent' (pas de conteneur local)."""
        import httpx

        class _ErrorClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                raise httpx.ConnectError("Connection refused")

        remote_app = Application(
            name="Remote App", slug="remote-app",
            api_url="http://remote.example.com:8000",
            is_active=True, requires_auth=True,
            is_remote=True,
        )
        async_session.add(remote_app)
        await async_session.commit()

        import app.routes.apps as apps_mod
        state_mock, calls = self._container_state_mock("absent")
        self._patch_httpx(monkeypatch, _ErrorClient())
        monkeypatch.setattr(apps_mod, "get_container_state", state_mock)

        resp = await client.get("/api/apps/status", headers=get_auth_header(admin_token))

        app = self._find(resp.json(), "remote-app")
        assert app["status"] == "offline"
        assert calls["n"] == 0  # garde app distante : zéro appel Docker

    @pytest.mark.asyncio
    async def test_probe_healthy_path_skips_docker(self, client, admin_token, test_app, monkeypatch):
        """Chemin sain (ping OK) : zéro appel Docker."""
        from types import SimpleNamespace

        class _OkClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                return SimpleNamespace(status_code=200)

        import app.routes.apps as apps_mod
        state_mock, calls = self._container_state_mock("absent")
        self._patch_httpx(monkeypatch, _OkClient())
        monkeypatch.setattr(apps_mod, "get_container_state", state_mock)

        resp = await client.get("/api/apps/status", headers=get_auth_header(admin_token))

        app = self._find(resp.json(), "test-app")
        assert app["status"] == "online"
        assert calls["n"] == 0  # chemin sain : aucun appel Docker
