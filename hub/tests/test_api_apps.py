# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests d'intégration pour les endpoints API des applications
"""
import logging

import pytest
from pathlib import Path
from unittest.mock import patch
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, get_password_hash
from app.models import (
    User,
    Application,
    UserAppRole,
    ServiceAccount,
    AuditLog,
    ApprovalStatus,
)
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


class TestRegisterIdempotent:
    """Task 6 (plan 2026-11-24-m2m-boot-identity-race) — register idempotent
    sur preuve M2M valide (anti-course multi-instances).

    Au boot simultané de N instances, chaque POST /api/apps/register rotait le
    secret du SA owner et invalidait mutuellement les instances qui bootent
    ensemble. Avec le marqueur `X-Hub-Register-Idempotent: 1` ET un token M2M
    valide du SA owner, le Hub conserve le secret (`secret_rotated=false`,
    sans `client_secret`). Sans marqueur, avec le token du SA owner → rotation
    préservée (compat ascendante avec les vieux socles).

    Tâche 3 (plan 2026-11-25-inscription-modele-confiance) : réinscrire une
    application existante exige le jeton de son compte de service propriétaire.
    Le réseau interne peut créer, seul le jeton du propriétaire peut modifier :
    sans jeton, ou avec le jeton d'un compte lié non propriétaire → 401, aucune
    rotation, aucune réécriture. Le compte propriétaire disparu reste le seul
    cas d'auto-réparation (cf. test_register_recreates_missing_sa.py).
    """

    @staticmethod
    async def _register_and_approve(
        client: AsyncClient,
        async_session: AsyncSession,
        tmp_path: Path,
        slug: str,
        name: str,
        api_port: int,
        version: str | None = None,
    ) -> dict:
        """Register initial + approbation admin. L'app créée via /register est
        pending avec un SA inactif : le mint client_credentials exige un SA
        actif, on simule donc l'approbation avant de mint."""
        payload = {
            "slug": slug,
            "name": name,
            "api_port": api_port,
            "frontend_port": api_port + 1,
        }
        if version is not None:
            payload["version"] = version
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post("/api/apps/register", json=payload)
        assert response.status_code == 200, f"{response.status_code}: {response.text}"
        creds = response.json()["credentials"]
        assert creds["client_secret"], "credentials incomplètes au register initial"

        result = await async_session.execute(
            select(Application).where(Application.slug == slug)
        )
        app = result.scalar_one()
        app.approval_status = "approved"
        app.is_active = True
        result_sa = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.client_id == creds["client_id"])
        )
        sa = result_sa.scalar_one()
        sa.is_active = True
        await async_session.commit()
        return creds

    @staticmethod
    async def _mint(client: AsyncClient, client_id: str, client_secret: str):
        return await client.post(
            "/api/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )

    @pytest.mark.asyncio
    async def test_register_idempotent_with_valid_token_keeps_secret(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """Marqueur + token M2M valide du SA owner → secret conservé."""
        creds = await self._register_and_approve(
            client, async_session, tmp_path, "idem-app", "Idem App", 9405
        )
        old_secret = creds["client_secret"]

        # Mint M2M avec les credentials initiales (comme une instance qui a booté)
        mint = await self._mint(client, creds["client_id"], old_secret)
        assert mint.status_code == 200, f"{mint.status_code}: {mint.text}"
        token = mint.json()["access_token"]

        # Re-register idempotent : header + Bearer du SA owner
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "idem-app",
                    "name": "Idem App",
                    "api_port": 9405,
                    "frontend_port": 9406,
                },
                headers={
                    "X-Hub-Register-Idempotent": "1",
                    "Authorization": f"Bearer {token}",
                },
            )

        assert response.status_code == 200, f"{response.status_code}: {response.text}"
        data = response.json()
        assert data["is_new"] is False
        assert data["secret_rotated"] is False
        assert data["credentials"]["client_id"] == creds["client_id"]
        assert "client_secret" not in data["credentials"], (
            "Le secret ne doit JAMAIS être retourné sur le chemin idempotent"
        )

        # Preuve que la rotation n'a PAS eu lieu : l'ANCIEN secret mint encore
        remint = await self._mint(client, creds["client_id"], old_secret)
        assert remint.status_code == 200, (
            f"L'ancien secret ne mint plus ({remint.status_code}) — rotation "
            f"indue sur le chemin idempotent : {remint.text}"
        )

    @pytest.mark.asyncio
    async def test_register_without_token_is_refused(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """Sans jeton (vieux socle ou appelant quelconque) → 401, secret conservé."""
        creds = await self._register_and_approve(
            client, async_session, tmp_path, "notoken-app", "Notoken App", 9415
        )
        old_secret = creds["client_secret"]

        mint = await self._mint(client, creds["client_id"], old_secret)
        assert mint.status_code == 200

        # Re-register SANS header NI token
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "notoken-app",
                    "name": "Notoken App",
                    "api_port": 9415,
                    "frontend_port": 9416,
                },
            )

        assert response.status_code == 401, (
            f"Réinscrire sans jeton doit être refusé : {response.status_code} reçu, 401 attendu"
        )
        assert response.json()["detail"] == "Token M2M requis pour réinscrire cette application"

        # Aucune rotation : l'ancien secret mint encore
        remint = await self._mint(client, creds["client_id"], old_secret)
        assert remint.status_code == 200, (
            f"L'ancien secret ne mint plus ({remint.status_code}) — rotation "
            f"indue malgré le refus"
        )

    @pytest.mark.asyncio
    async def test_register_with_marker_but_no_token_is_refused(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """Marqueur sans token M2M → 401 (pas de preuve d'identité), secret conservé."""
        creds = await self._register_and_approve(
            client, async_session, tmp_path, "marker-app", "Marker App", 9425
        )
        old_secret = creds["client_secret"]

        mint = await self._mint(client, creds["client_id"], old_secret)
        assert mint.status_code == 200

        # Header présent, MAIS pas de Bearer → pas de preuve → refus
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "marker-app",
                    "name": "Marker App",
                    "api_port": 9425,
                    "frontend_port": 9426,
                },
                headers={"X-Hub-Register-Idempotent": "1"},
            )

        assert response.status_code == 401, (
            f"Réinscrire avec le marqueur mais sans jeton doit être refusé : "
            f"{response.status_code} reçu, 401 attendu"
        )
        assert response.json()["detail"] == "Token M2M requis pour réinscrire cette application"

        # Aucune rotation : l'ancien secret mint encore
        remint = await self._mint(client, creds["client_id"], old_secret)
        assert remint.status_code == 200, (
            f"L'ancien secret ne mint plus ({remint.status_code}) — rotation "
            f"indue malgré le refus"
        )

    @pytest.mark.asyncio
    async def test_register_with_non_owner_token_is_refused_and_owner_secret_kept(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """Token M2M valide MAIS d'un SA NON-owner → 401, secret du owner intact.

        Garde-fou anti-appels croisés entre apps : un SA lié à l'app (accès
        type partagé) ne peut pas réinscrire l'app — son jeton vaut absence de
        jeton. Seul le jeton du SA owner est autorisé à réinscrire ; le secret
        du SA owner comme celui du SA appelant restent intacts.
        """
        from app.models import service_account_app_access

        creds = await self._register_and_approve(
            client, async_session, tmp_path, "cross-app", "Cross App", 9435
        )
        old_owner_secret = creds["client_secret"]

        # Second SA actif, LIÉ à l'app (sinon register → 403) mais NON-owner
        deputy = ServiceAccount(
            name="Deputy Service Account",
            client_id="sa_deputy_cross",
            client_secret_hash=get_password_hash("deputy_secret"),
            is_active=True,
        )
        async_session.add(deputy)
        await async_session.flush()
        result_app = await async_session.execute(
            select(Application).where(Application.slug == "cross-app")
        )
        app = result_app.scalar_one()
        await async_session.execute(
            service_account_app_access.insert().values(
                service_account_id=deputy.id,
                app_id=app.id,
            )
        )
        await async_session.commit()

        # Le SA non-owner mint son propre token M2M (valide)
        mint_deputy = await self._mint(client, "sa_deputy_cross", "deputy_secret")
        assert mint_deputy.status_code == 200, f"{mint_deputy.status_code}: {mint_deputy.text}"
        deputy_token = mint_deputy.json()["access_token"]

        # Re-register avec le token du NON-owner → refus (vaut absence de jeton)
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "cross-app",
                    "name": "Cross App",
                    "api_port": 9435,
                    "frontend_port": 9436,
                },
                headers={
                    "X-Hub-Register-Idempotent": "1",
                    "Authorization": f"Bearer {deputy_token}",
                },
            )

        assert response.status_code == 401, (
            f"Réinscrire avec le jeton d'un compte non propriétaire doit être refusé : "
            f"{response.status_code} reçu, 401 attendu"
        )
        assert response.json()["detail"] == "Token M2M requis pour réinscrire cette application"

        # Le secret du SA owner est INTACT : il mint toujours
        mint_owner = await self._mint(client, creds["client_id"], old_owner_secret)
        assert mint_owner.status_code == 200, (
            f"{mint_owner.status_code}: le secret du SA owner ne mint plus — "
            f"roté indûment malgré le refus"
        )

        # Le SA non-owner n'a PAS été touché : son secret mint toujours
        mint_deputy_after = await self._mint(client, "sa_deputy_cross", "deputy_secret")
        assert mint_deputy_after.status_code == 200, (
            f"{mint_deputy_after.status_code}: le secret du SA non-owner a été "
            f"roté indûment — {mint_deputy_after.text}"
        )

    @pytest.mark.asyncio
    async def test_register_idempotent_still_updates_app_fields(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """Idempotent avec champs modifiés : la mise à jour (version, api_url)
        s'applique AVANT le return « secret conservé » — le secret owner reste
        valide et `client_secret` reste absent de la réponse."""
        creds = await self._register_and_approve(
            client, async_session, tmp_path, "fields-app", "Fields App", 9445,
            version="0.1.0",
        )
        old_secret = creds["client_secret"]

        mint = await self._mint(client, creds["client_id"], old_secret)
        assert mint.status_code == 200
        token = mint.json()["access_token"]

        # Re-register idempotent avec version et port MODIFIÉS
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "fields-app",
                    "name": "Fields App",
                    "api_port": 9446,
                    "frontend_port": 9447,
                    "version": "0.2.0",
                },
                headers={
                    "X-Hub-Register-Idempotent": "1",
                    "Authorization": f"Bearer {token}",
                },
            )

        assert response.status_code == 200, f"{response.status_code}: {response.text}"
        data = response.json()
        assert data["is_new"] is False
        assert data["secret_rotated"] is False
        assert "client_secret" not in data["credentials"]

        # Champs mis à jour (réponse) — version n'est pas exposée par
        # AppResponse : la lecture en base ci-dessous fait foi.
        assert data["application"]["api_url"].endswith(":9446")
        result = await async_session.execute(
            select(Application).where(Application.slug == "fields-app")
        )
        app = result.scalar_one()
        assert app.version == "0.2.0", (
            f"version non mise à jour sur le chemin idempotent (DB: {app.version})"
        )
        assert app.api_url.endswith(":9446"), (
            f"api_url non mise à jour sur le chemin idempotent (DB: {app.api_url})"
        )

        # Audit discriminant : le chemin idempotent ne doit pas être tracé
        # comme une rotation — les details portent la mention « secret conservé »
        audits = await async_session.execute(
            select(AuditLog).where(AuditLog.action == "m2m_reregistration_enforced")
        )
        audit = audits.scalars().first()
        assert audit is not None, "audit m2m_reregistration_enforced manquant"
        assert "secret conservé (idempotent)" in (audit.details or ""), (
            f"details d'audit non discriminants pour le chemin idempotent: {audit.details!r}"
        )

        # Le secret owner est CONSERVÉ : l'ancien secret mint toujours
        remint = await self._mint(client, creds["client_id"], old_secret)
        assert remint.status_code == 200, (
            f"{remint.status_code}: l'ancien secret ne mint plus alors que le "
            f"chemin idempotent ne doit pas roté — {remint.text}"
        )

    @pytest.mark.asyncio
    async def test_register_with_owner_token_without_marker_still_rotates(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """Jeton du propriétaire SANS marqueur (socles 2.36 à 2.39) → rotation
        préservée, ancien secret invalidé."""
        creds = await self._register_and_approve(
            client, async_session, tmp_path, "oldsocle-app", "Oldsocle App", 9415
        )
        old_secret = creds["client_secret"]

        mint = await self._mint(client, creds["client_id"], old_secret)
        assert mint.status_code == 200
        token = mint.json()["access_token"]

        # Re-register SANS marqueur, AVEC le Bearer du SA owner
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "oldsocle-app",
                    "name": "Oldsocle App",
                    "api_port": 9415,
                    "frontend_port": 9416,
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200, f"{response.status_code}: {response.text}"
        data = response.json()
        assert data["is_new"] is False
        assert data["secret_rotated"] is True
        assert data["credentials"]["client_id"] == creds["client_id"]
        new_secret = data["credentials"]["client_secret"]
        assert new_secret and new_secret != old_secret, "Le secret n'a pas été roté"

        # L'ancien secret ne mint plus (invalidé par la rotation)
        remint_old = await self._mint(client, creds["client_id"], old_secret)
        assert remint_old.status_code == 401

        # Le nouveau secret est utilisable
        mint_new = await self._mint(client, creds["client_id"], new_secret)
        assert mint_new.status_code == 200, f"{mint_new.status_code}: {mint_new.text}"

    @pytest.mark.asyncio
    async def test_register_without_token_with_custom_host_does_not_rewrite_urls(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """Ré-inscription sans jeton avec un `custom_host` arbitraire → 401, et
        les adresses de l'application ne sont pas réécrites vers cet hôte."""
        await self._register_and_approve(
            client, async_session, tmp_path, "host-app", "Host App", 9455
        )

        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "host-app",
                    "name": "Host App",
                    "api_port": 9455,
                    "frontend_port": 9456,
                    "custom_host": "192.0.2.99",
                },
            )

        assert response.status_code == 401, (
            f"Réinscrire sans jeton doit être refusé : {response.status_code} reçu, 401 attendu"
        )

        # Adresses relues en base (colonnes : pas de relais par le cache de session)
        urls = (await async_session.execute(
            select(
                Application.api_url,
                Application.frontend_url,
                Application.callback_url,
            ).where(Application.slug == "host-app")
        )).one()
        for field, value in zip(("api_url", "frontend_url", "callback_url"), urls):
            assert "192.0.2.99" not in (value or ""), (
                f"{field} a été réécrit vers l'hôte arbitraire malgré le refus"
            )

    @pytest.mark.asyncio
    async def test_register_with_invalid_token_is_refused_and_secret_kept(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """`Authorization: Bearer not-a-jwt` → 401, l'ancien secret mint encore."""
        creds = await self._register_and_approve(
            client, async_session, tmp_path, "badtoken-app", "Badtoken App", 9465
        )
        old_secret = creds["client_secret"]

        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "badtoken-app",
                    "name": "Badtoken App",
                    "api_port": 9465,
                    "frontend_port": 9466,
                },
                headers={"Authorization": "Bearer not-a-jwt"},
            )

        assert response.status_code == 401, (
            f"Réinscrire avec un jeton invalide doit être refusé : "
            f"{response.status_code} reçu, 401 attendu"
        )

        remint = await self._mint(client, creds["client_id"], old_secret)
        assert remint.status_code == 200, (
            f"L'ancien secret ne mint plus ({remint.status_code}) — rotation "
            f"indue malgré le refus"
        )

    @pytest.mark.asyncio
    async def test_pending_app_reregistration_without_token_keeps_first_secret(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """Application en attente (compte de service inactif), réinscrite sans
        jeton : refusée sans rotation — son PREMIER secret doit encore
        fonctionner une fois l'application approuvée."""
        payload = {
            "slug": "pending-app",
            "name": "Pending App",
            "api_port": 9475,
            "frontend_port": 9476,
        }

        # Inscription initiale, SANS approbation : app en attente, SA inactif
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post("/api/apps/register", json=payload)
        assert response.status_code == 200, f"{response.status_code}: {response.text}"
        first_creds = response.json()["credentials"]
        assert first_creds["client_secret"], "credentials incomplètes au register initial"

        # Ré-inscription sans jeton → refus, aucune rotation
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post("/api/apps/register", json=payload)
        assert response.status_code == 401, (
            f"Réinscrire une application en attente sans jeton doit être refusé : "
            f"{response.status_code} reçu, 401 attendu"
        )

        # Approbation (même chemin que _register_and_approve)
        result = await async_session.execute(
            select(Application).where(Application.slug == "pending-app")
        )
        app = result.scalar_one()
        app.approval_status = "approved"
        app.is_active = True
        result_sa = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.client_id == first_creds["client_id"])
        )
        sa = result_sa.scalar_one()
        sa.is_active = True
        await async_session.commit()

        # Le PREMIER secret mint : il n'a jamais été roté
        mint = await self._mint(client, first_creds["client_id"], first_creds["client_secret"])
        assert mint.status_code == 200, (
            f"{mint.status_code}: le premier secret ne mint plus après approbation — "
            f"roté indûment par une ré-inscription refusée"
        )

    @pytest.mark.asyncio
    async def test_force_replace_without_token_is_refused_app_kept(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """`FORCE_REPLACE_APP=true` : le remplacement est une modification — sans
        jeton du propriétaire il est refusé et l'application existe toujours."""
        await self._register_and_approve(
            client, async_session, tmp_path, "forced-app", "Forced App", 9485
        )
        result = await async_session.execute(
            select(Application.id).where(Application.slug == "forced-app")
        )
        app_id_before = result.scalar_one()

        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path), \
             patch("app.routes.apps.settings.force_replace_app", True):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "forced-app",
                    "name": "Forced App",
                    "api_port": 9485,
                    "frontend_port": 9486,
                },
            )

        assert response.status_code == 401, (
            f"Le remplacement forcé sans jeton doit être refusé : "
            f"{response.status_code} reçu, 401 attendu"
        )

        # L'application existe toujours, avec le même id (ni supprimée ni recréée)
        app_ids = (await async_session.execute(
            select(Application.id).where(Application.slug == "forced-app")
        )).scalars().all()
        assert app_ids == [app_id_before], (
            f"l'application a été supprimée ou recréée (ids en base : {app_ids}, "
            f"attendu : {[app_id_before]})"
        )

    @pytest.mark.asyncio
    async def test_workshop_pending_with_owner_sa_is_not_auto_approved_on_reregistration(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """Atelier en attente QUI POSSÈDE un compte de service : sa ré-inscription
        sans jeton est refusée, il n'est pas auto-approuvé pour autant (cas
        limite documenté : l'administrateur l'approuve, ou recrée l'atelier)."""
        payload = {
            "slug": "wsinwait-ws",
            "name": "Wsinwait App",
            "api_port": 9495,
            "frontend_port": 9496,
        }

        # Inscription sans app de base approuvée → atelier en attente, avec son SA
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post("/api/apps/register", json=payload)
        assert response.status_code == 200, f"{response.status_code}: {response.text}"
        assert response.json()["application"]["approval_status"] == "pending", (
            "l'atelier doit être en attente pour que le test porte sur le bon cas"
        )

        # L'app de base est approuvée ensuite
        async_session.add(Application(
            name="Wsinwait App",
            slug="wsinwait",
            api_url="http://localhost:9494",
            is_active=True,
            approval_status="approved",
            requires_auth=True,
        ))
        await async_session.commit()

        # Ré-inscription sans jeton : refusée AVANT l'auto-approbation
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post("/api/apps/register", json=payload)
        assert response.status_code == 401, (
            f"Réinscrire l'atelier sans jeton doit être refusé : "
            f"{response.status_code} reçu, 401 attendu"
        )

        approval = await async_session.execute(
            select(Application.approval_status).where(Application.slug == "wsinwait-ws")
        )
        assert approval.scalar_one() is ApprovalStatus.pending, (
            "l'atelier en attente a été auto-approuvé par une ré-inscription sans jeton"
        )

    @pytest.mark.asyncio
    async def test_register_refusal_logs_warning(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path, caplog
    ):
        """Chaque refus écrit une ligne WARNING portant le slug et la mention du
        propriétaire."""
        await self._register_and_approve(
            client, async_session, tmp_path, "warned-app", "Warned App", 9505
        )

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app.routes.apps"), \
             patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "warned-app",
                    "name": "Warned App",
                    "api_port": 9505,
                    "frontend_port": 9506,
                },
            )

        assert response.status_code == 401, (
            f"Réinscrire sans jeton doit être refusé : {response.status_code} reçu, 401 attendu"
        )

        refusals = [
            rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.WARNING
            and rec.name == "app.routes.apps"
            and "warned-app" in rec.getMessage()
            and "propriétaire" in rec.getMessage()
        ]
        assert refusals, (
            "le refus de ré-inscription doit écrire une ligne WARNING portant le slug "
            f"et la mention « propriétaire » — WARNING capturés : "
            f"{[rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]}"
        )
