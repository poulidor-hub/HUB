# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests d'intégration — traçabilité du client_id TENTÉ dans error_logs sur échec M2M.

Contexte (incident 09/11/2026) : un SA désactivé martelé en boucle sur
POST /api/auth/token (186×403) sans qu'on puisse identifier QUI depuis
error_logs. On trace `attempted_client_id` (identifiant public du form,
jamais le client_secret) sur les échecs d'auth M2M :
1. 403 « Service account désactivé » → attempted_client_id tracé.
2. 401 « Identifiants service account invalides » (SA introuvable ou secret
   invalide) → attempted_client_id tracé.
3. 429 rate limit M2M → attempted_client_id tracé.
4. GET /api/audit/errors expose attempted_client_id.
5. Garde-fou : un échec de login utilisateur (grant password) ne trace PAS de
   attempted_client_id (colonne NULL).
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.auth import get_password_hash
from app.models import ErrorLog, ServiceAccount, User


async def _error_logs(async_session) -> list[ErrorLog]:
    result = await async_session.execute(select(ErrorLog))
    return list(result.scalars().all())


@pytest.fixture(autouse=True)
def _reset_m2m_rate_limit():
    """Isole les tests du compteur M2M global (state module-level partagé)."""
    from app.routes import auth as auth_module

    auth_module._m2m_attempts.clear()
    yield
    auth_module._m2m_attempts.clear()


@pytest.fixture
async def disabled_service_account(async_session) -> ServiceAccount:
    """Service account DÉSACTIVÉ (cas incident : martelé en boucle en 403)"""
    sa = ServiceAccount(
        name="Disabled Service Account",
        client_id="sa_disabled_abcdef",
        client_secret_hash=get_password_hash("secret123"),
        description="SA désactivé pour test",
        is_active=False,
    )
    async_session.add(sa)
    await async_session.commit()
    await async_session.refresh(sa)
    return sa


class TestM2MFailureTracesAttemptedClientId:
    @pytest.mark.asyncio
    async def test_disabled_sa_403_traces_attempted_client_id(
        self, client: AsyncClient, async_session, disabled_service_account: ServiceAccount
    ):
        """SA désactivé + secret valide : 403, attempted_client_id tracé"""
        resp = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "sa_disabled_abcdef",
                "client_secret": "secret123",
            },
        )
        assert resp.status_code == 403

        logs = await _error_logs(async_session)
        assert len(logs) == 1
        log = logs[0]
        assert log.error_code == 403
        assert log.attempted_client_id == "sa_disabled_abcdef"
        # Garde-fou : jamais le secret
        assert "secret123" not in (log.request_body or "")

    @pytest.mark.asyncio
    async def test_unknown_client_id_401_traces_attempted_client_id(
        self, client: AsyncClient, async_session
    ):
        """client_id inexistant : 401, attempted_client_id = valeur tentée"""
        resp = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "sa_ghost_999999",
                "client_secret": "whatever-secret",
            },
        )
        assert resp.status_code == 401

        logs = await _error_logs(async_session)
        assert len(logs) == 1
        log = logs[0]
        assert log.error_code == 401
        assert log.attempted_client_id == "sa_ghost_999999"

    @pytest.mark.asyncio
    async def test_rate_limited_m2m_429_traces_attempted_client_id(
        self, client: AsyncClient, async_session, monkeypatch
    ):
        """Rate limit M2M atteint : 429, attempted_client_id tracé"""
        from app.routes import auth as auth_module

        monkeypatch.setattr(auth_module, "_M2M_RATE_LIMIT_MAX_INTERNAL", 0)
        monkeypatch.setattr(auth_module, "_M2M_RATE_LIMIT_MAX_EXTERNAL", 0)

        resp = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "sa_burst_client",
                "client_secret": "whatever-secret",
            },
        )
        assert resp.status_code == 429

        logs = await _error_logs(async_session)
        assert len(logs) == 1
        log = logs[0]
        assert log.error_code == 429
        assert log.attempted_client_id == "sa_burst_client"


class TestAuditErrorsExposeAttemptedClientId:
    @pytest.mark.asyncio
    async def test_errors_list_exposes_attempted_client_id(
        self, client: AsyncClient, async_session, admin_token: str
    ):
        """GET /api/audit/errors expose attempted_client_id (None si absent)"""
        async_session.add_all(
            [
                ErrorLog(
                    error_code=403,
                    error_type="authorization",
                    attempted_client_id="sa_disabled_abcdef",
                    request_method="POST",
                    request_path="/api/auth/token",
                ),
                ErrorLog(
                    error_code=404,
                    error_type="not_found",
                    request_method="GET",
                    request_path="/api/nope",
                ),
            ]
        )
        await async_session.commit()

        resp = await client.get(
            "/api/audit/errors",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        by_code = {e["error_code"]: e for e in data["errors"]}
        assert by_code[403]["attempted_client_id"] == "sa_disabled_abcdef"
        assert by_code[404]["attempted_client_id"] is None


class TestUserLoginFailureDoesNotTraceAttemptedClientId:
    @pytest.mark.asyncio
    async def test_wrong_password_leaves_attempted_client_id_null(
        self, client: AsyncClient, async_session, test_user: User
    ):
        """Garde-fou : échec login utilisateur (grant password) → attempted_client_id NULL"""
        resp = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "testuser",
                "password": "wrongpassword",
            },
        )
        assert resp.status_code == 401

        logs = await _error_logs(async_session)
        assert len(logs) == 1
        log = logs[0]
        assert log.error_code == 401
        assert log.attempted_client_id is None
