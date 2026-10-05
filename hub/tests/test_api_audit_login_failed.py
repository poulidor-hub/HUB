"""
Tests d'intégration — audit des échecs de login + rattachement de l'appelant aux erreurs.

Couvre :
1. AuditLog(action="login_failed") avec l'identifiant tenté (utilisateur inconnu /
   mauvais mot de passe / compte désactivé), sur /api/auth/token et /api/auth/login.
2. error_logs.user_id / service_account_id rattachés via le JWT Bearer (y compris
   token expiré), NULL sans token.
3. Filtre username de GET /api/audit/logs élargi aux identifiants tentés (target).
"""
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.auth import create_access_token
from app.models import AuditLog, ErrorLog, ServiceAccount, User


async def _audit_entries(async_session, action: str) -> list[AuditLog]:
    result = await async_session.execute(
        select(AuditLog).where(AuditLog.action == action)
    )
    return list(result.scalars().all())


async def _error_logs(async_session) -> list[ErrorLog]:
    result = await async_session.execute(select(ErrorLog))
    return list(result.scalars().all())


# =============================================================================
# 1. Échecs de login tracés avec l'identifiant tenté
# =============================================================================


class TestLoginFailedAudit:
    @pytest.mark.asyncio
    async def test_unknown_user_token_creates_login_failed(
        self, client: AsyncClient, async_session
    ):
        """Utilisateur inconnu : login_failed tracé, user_id NULL, identifiant en target"""
        resp = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "ghostuser",
                "password": "whatever1",
            },
        )
        assert resp.status_code == 401

        entries = await _audit_entries(async_session, "login_failed")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.target == "ghostuser"
        assert entry.user_id is None
        assert entry.details == "Utilisateur inconnu"
        assert entry.response_status == 401
        assert entry.ip_address is not None

    @pytest.mark.asyncio
    async def test_wrong_password_creates_login_failed_with_user(
        self, client: AsyncClient, async_session, test_user: User
    ):
        """Mauvais mot de passe : login_failed rattaché à l'utilisateur existant"""
        resp = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "testuser",
                "password": "wrongpassword",
            },
        )
        assert resp.status_code == 401

        entries = await _audit_entries(async_session, "login_failed")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.target == "testuser"
        assert entry.user_id == test_user.id
        assert entry.details == "Mot de passe incorrect"
        assert entry.response_status == 401

    @pytest.mark.asyncio
    async def test_login_endpoint_unknown_user_creates_login_failed(
        self, client: AsyncClient, async_session
    ):
        """POST /api/auth/login (JSON) trace aussi la tentative"""
        resp = await client.post(
            "/api/auth/login",
            json={"username": "ghostuser", "password": "whatever1"},
        )
        assert resp.status_code == 401

        entries = await _audit_entries(async_session, "login_failed")
        assert len(entries) == 1
        assert entries[0].target == "ghostuser"
        assert entries[0].user_id is None

    @pytest.mark.asyncio
    async def test_inactive_user_creates_login_failed(
        self, client: AsyncClient, async_session
    ):
        """Compte désactivé : tentative tracée avec l'utilisateur rattaché"""
        from app.auth import get_password_hash

        inactive = User(
            username="inactiveuser",
            email="inactive@test.com",
            hashed_password=get_password_hash("password123"),
            is_active=False,
        )
        async_session.add(inactive)
        await async_session.commit()
        await async_session.refresh(inactive)

        resp = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "inactiveuser",
                "password": "password123",
            },
        )
        assert resp.status_code == 403

        entries = await _audit_entries(async_session, "login_failed")
        assert len(entries) == 1
        assert entries[0].target == "inactiveuser"
        assert entries[0].user_id == inactive.id
        assert entries[0].details == "Compte désactivé"
        assert entries[0].response_status == 403

    @pytest.mark.asyncio
    async def test_successful_login_does_not_create_login_failed(
        self, client: AsyncClient, async_session, test_user: User
    ):
        """Un login réussi ne crée que action='login', jamais login_failed"""
        resp = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "testuser",
                "password": "testpass123",
            },
        )
        assert resp.status_code == 200
        assert await _audit_entries(async_session, "login_failed") == []


# =============================================================================
# 2. Rattachement de l'appelant aux error_logs via le JWT Bearer
# =============================================================================


class TestErrorLogActor:
    async def _trigger_api_404(self, client: AsyncClient, headers: dict = None):
        """Un 404 sous /api/ est logué par http_exception_handler."""
        return await client.get("/api/no-such-route", headers=headers or {})

    @pytest.mark.asyncio
    async def test_error_attaches_user_from_bearer(
        self, client: AsyncClient, async_session, test_user: User
    ):
        """Erreur avec Bearer valide → error_logs.user_id renseigné"""
        token, _, _ = create_access_token(
            username="testuser", apps={}, uid=str(test_user.uuid)
        )
        resp = await self._trigger_api_404(client, {"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404

        logs = await _error_logs(async_session)
        assert len(logs) == 1
        assert logs[0].error_code == 404
        assert logs[0].user_id == test_user.id
        assert logs[0].service_account_id is None

    @pytest.mark.asyncio
    async def test_error_attaches_user_from_expired_token(
        self, client: AsyncClient, async_session, test_user: User
    ):
        """Token expiré : l'erreur reste rattachée à son propriétaire"""
        token, _, _ = create_access_token(
            username="testuser",
            apps={},
            uid=str(test_user.uuid),
            expires_delta=timedelta(minutes=-10),
        )
        resp = await self._trigger_api_404(client, {"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404

        logs = await _error_logs(async_session)
        assert len(logs) == 1
        assert logs[0].user_id == test_user.id

    @pytest.mark.asyncio
    async def test_error_without_token_has_null_actor(
        self, client: AsyncClient, async_session
    ):
        """Erreur sans Bearer → user_id et service_account_id NULL"""
        resp = await self._trigger_api_404(client)
        assert resp.status_code == 404

        logs = await _error_logs(async_session)
        assert len(logs) == 1
        assert logs[0].user_id is None
        assert logs[0].service_account_id is None

    @pytest.mark.asyncio
    async def test_error_attaches_service_account(
        self, client: AsyncClient, async_session, service_account: ServiceAccount
    ):
        """Token M2M → error_logs.service_account_id renseigné"""
        token, _, _ = create_access_token(
            username=service_account.client_id, apps={}, is_service_account=True
        )
        resp = await self._trigger_api_404(client, {"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404

        logs = await _error_logs(async_session)
        assert len(logs) == 1
        assert logs[0].service_account_id == service_account.id
        assert logs[0].user_id is None

    @pytest.mark.asyncio
    async def test_error_with_forged_token_has_null_actor(
        self, client: AsyncClient, async_session
    ):
        """Token forgé (signature invalide) → aucun rattachement, pas de crash"""
        resp = await self._trigger_api_404(
            client, {"Authorization": "Bearer not.a.jwt"}
        )
        assert resp.status_code == 404

        logs = await _error_logs(async_session)
        assert len(logs) == 1
        assert logs[0].user_id is None
        assert logs[0].service_account_id is None


# =============================================================================
# 3. Filtre username de /api/audit/logs élargi aux identifiants tentés
# =============================================================================


class TestUsernameFilterMatchesAttempted:
    @pytest.mark.asyncio
    async def test_filter_finds_login_failed_for_unknown_user(
        self, client: AsyncClient, async_session, admin_token: str
    ):
        """username=ghostuser retrouve le login_failed d'un utilisateur inexistant"""
        async_session.add(
            AuditLog(action="login_failed", target="ghostuser", user_id=None)
        )
        await async_session.commit()

        resp = await client.get(
            "/api/audit/logs?days=7&username=ghostuser",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 1
        assert data["logs"][0]["action"] == "login_failed"
        assert data["logs"][0]["target"] == "ghostuser"

    @pytest.mark.asyncio
    async def test_filter_still_matches_existing_users(
        self, client: AsyncClient, async_session, admin_token: str, test_user: User
    ):
        """Le filtre username classique (via user_id) continue de fonctionner"""
        async_session.add_all(
            [
                AuditLog(user_id=test_user.id, action="login", target="hub"),
                AuditLog(action="login_failed", target="ghostuser", user_id=None),
            ]
        )
        await async_session.commit()

        resp = await client.get(
            "/api/audit/logs?days=7&username=testuser",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 1
        assert data["logs"][0]["action"] == "login"


# =============================================================================
# 4. Acteur M2M exposé par la liste et le détail des erreurs
# =============================================================================


class TestErrorEndpointsExposeActor:
    @pytest.mark.asyncio
    async def test_errors_list_exposes_service_account(
        self,
        client: AsyncClient,
        async_session,
        admin_token: str,
        service_account: ServiceAccount,
    ):
        """GET /api/audit/errors expose le client_id du service account (acteur M2M)"""
        async_session.add(
            ErrorLog(
                error_code=403,
                error_type="authorization",
                service_account_id=service_account.id,
                request_method="GET",
                request_path="/api/apps",
            )
        )
        await async_session.commit()

        resp = await client.get(
            "/api/audit/errors",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        entry = data["errors"][0]
        assert entry["user_id"] is None
        assert entry["username"] is None
        assert entry["service_account_id"] == service_account.id
        assert entry["service_account_client_id"] == "sa_test123456789"

    @pytest.mark.asyncio
    async def test_errors_list_user_actor_unchanged(
        self,
        client: AsyncClient,
        async_session,
        admin_token: str,
        test_user: User,
    ):
        """Erreur rattachée à un utilisateur : username exposé, champs M2M NULL"""
        async_session.add(
            ErrorLog(
                error_code=404,
                error_type="not_found",
                user_id=test_user.id,
                request_method="GET",
                request_path="/api/nope",
            )
        )
        await async_session.commit()

        resp = await client.get(
            "/api/audit/errors",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        entry = resp.json()["errors"][0]
        assert entry["username"] == "testuser"
        assert entry["service_account_id"] is None
        assert entry["service_account_client_id"] is None

    @pytest.mark.asyncio
    async def test_errors_list_unknown_actor_ids_fallback(
        self,
        client: AsyncClient,
        async_session,
        admin_token: str,
    ):
        """Acteur supprimé entre-temps : ids bruts exposés, client_id NULL, pas de crash"""
        async_session.add(
            ErrorLog(
                error_code=500,
                error_type="internal",
                user_id=999999,
                service_account_id=999999,
                request_method="POST",
                request_path="/api/x",
            )
        )
        await async_session.commit()

        resp = await client.get(
            "/api/audit/errors",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        entry = resp.json()["errors"][0]
        assert entry["user_id"] == 999999
        assert entry["username"] is None
        assert entry["service_account_id"] == 999999
        assert entry["service_account_client_id"] is None

    @pytest.mark.asyncio
    async def test_error_detail_exposes_service_account_client_id(
        self,
        client: AsyncClient,
        async_session,
        admin_token: str,
        service_account: ServiceAccount,
    ):
        """GET /api/audit/errors/{id} expose aussi le client_id du service account"""
        entry = ErrorLog(
            error_code=500,
            error_type="internal",
            service_account_id=service_account.id,
            request_method="POST",
            request_path="/api/x",
        )
        async_session.add(entry)
        await async_session.commit()
        await async_session.refresh(entry)

        resp = await client.get(
            f"/api/audit/errors/{entry.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["service_account_id"] == service_account.id
        assert data["service_account_client_id"] == "sa_test123456789"
