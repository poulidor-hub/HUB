"""
Tests pour la résolution de `action_url` lors de l'envoi M2M de notifications.

Vérifie que :
- une URL relative envoyée par une app est préfixée avec son `frontend_url`
- une URL absolue est conservée telle quelle
- une `action_url` vide reste `None`
- une URL relative sans `frontend_url` configuré passe inchangée (best-effort)
- l'helper `_resolve_app_action_url` ajoute `http://` si `frontend_url` n'a pas
  de scheme
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, ServiceAccount, User, UserMessage
from app.routes.messages import _resolve_app_action_url
from .conftest import get_auth_header


# -----------------------------------------------------------------------------
# Tests unitaires de l'helper
# -----------------------------------------------------------------------------

class TestResolveAppActionUrl:

    def test_none_returns_none(self):
        assert _resolve_app_action_url(None, "http://app.example") is None

    def test_empty_returns_none(self):
        assert _resolve_app_action_url("", "http://app.example") is None

    def test_absolute_http_kept_as_is(self):
        url = "http://other.example/foo"
        assert _resolve_app_action_url(url, "http://app.example") == url

    def test_absolute_https_kept_as_is(self):
        url = "https://other.example/foo?x=1"
        assert _resolve_app_action_url(url, "http://app.example") == url

    def test_relative_prefixed_with_frontend_url(self):
        result = _resolve_app_action_url("/factures/1234", "http://app.example")
        assert result == "http://app.example/factures/1234"

    def test_relative_prefixed_when_frontend_url_has_trailing_slash(self):
        result = _resolve_app_action_url("/factures/1", "http://app.example/")
        assert result == "http://app.example/factures/1"

    def test_relative_prefixed_with_subpath_frontend_url(self):
        result = _resolve_app_action_url("/foo", "http://app.example/sub")
        # urljoin avec base normalisée + leading slash retiré
        assert result == "http://app.example/sub/foo"

    def test_relative_without_leading_slash(self):
        result = _resolve_app_action_url("factures/1", "http://app.example")
        assert result == "http://app.example/factures/1"

    def test_frontend_url_without_scheme_gets_http_prefix(self):
        result = _resolve_app_action_url("/foo", "192.168.1.10:4200")
        assert result == "http://192.168.1.10:4200/foo"

    def test_relative_without_frontend_url_passes_through(self):
        # Pas de frontend_url : on garde l'URL telle quelle (best-effort)
        assert _resolve_app_action_url("/foo", None) == "/foo"
        assert _resolve_app_action_url("/foo", "") == "/foo"

    def test_result_truncated_to_500_chars(self):
        long_path = "/" + ("a" * 600)
        result = _resolve_app_action_url(long_path, "http://app.example")
        assert result is not None
        assert len(result) == 500


# -----------------------------------------------------------------------------
# Tests d'intégration sur l'endpoint M2M
# -----------------------------------------------------------------------------

async def _get_sa_token(client: AsyncClient, sa: ServiceAccount) -> str:
    response = await client.post(
        "/api/auth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": sa.client_id,
            "client_secret": "secret123",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def _fetch_message(session: AsyncSession, mid: int) -> UserMessage:
    result = await session.execute(select(UserMessage).where(UserMessage.id == mid))
    return result.scalar_one()


class TestM2MSendActionUrlResolution:

    @pytest.mark.asyncio
    async def test_relative_url_is_prefixed_with_app_frontend_url(
        self, client, async_session, test_user, service_account, test_app
    ):
        token = await _get_sa_token(client, service_account)
        resp = await client.post(
            "/api/messages/m2m/send",
            headers=get_auth_header(token),
            json={
                "title": "Facture en attente",
                "body": "Cliquer pour ouvrir",
                "target_usernames": [test_user.username],
                "action_url": "/factures/1234",
                "action_label": "Voir",
            },
        )
        assert resp.status_code == 200, resp.text
        mid = resp.json()["message_ids"][0]
        msg = await _fetch_message(async_session, mid)
        # test_app.frontend_url == "http://localhost:3001"
        assert msg.action_url == "http://localhost:3001/factures/1234"

    @pytest.mark.asyncio
    async def test_absolute_url_is_kept_as_is(
        self, client, async_session, test_user, service_account, test_app
    ):
        token = await _get_sa_token(client, service_account)
        absolute = "https://external.example.com/path?x=1"
        resp = await client.post(
            "/api/messages/m2m/send",
            headers=get_auth_header(token),
            json={
                "title": "External link",
                "target_usernames": [test_user.username],
                "action_url": absolute,
            },
        )
        assert resp.status_code == 200, resp.text
        mid = resp.json()["message_ids"][0]
        msg = await _fetch_message(async_session, mid)
        assert msg.action_url == absolute

    @pytest.mark.asyncio
    async def test_no_action_url_stays_none(
        self, client, async_session, test_user, service_account, test_app
    ):
        token = await _get_sa_token(client, service_account)
        resp = await client.post(
            "/api/messages/m2m/send",
            headers=get_auth_header(token),
            json={
                "title": "No link",
                "target_usernames": [test_user.username],
            },
        )
        assert resp.status_code == 200, resp.text
        mid = resp.json()["message_ids"][0]
        msg = await _fetch_message(async_session, mid)
        assert msg.action_url is None

    @pytest.mark.asyncio
    async def test_relative_url_without_frontend_url_passes_through(
        self, client, async_session, test_user
    ):
        # App sans frontend_url
        from app.auth import get_password_hash
        from app.models import service_account_app_access

        app_no_front = Application(
            name="No Front",
            slug="no-front",
            api_url="http://localhost:9999",
            frontend_url=None,
            is_active=True,
        )
        async_session.add(app_no_front)
        await async_session.commit()
        await async_session.refresh(app_no_front)

        sa = ServiceAccount(
            name="SA No Front",
            client_id="sa_nofront_12345",
            client_secret_hash=get_password_hash("secret123"),
            is_active=True,
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)

        await async_session.execute(
            service_account_app_access.insert().values(
                service_account_id=sa.id,
                app_id=app_no_front.id,
            )
        )
        await async_session.commit()

        token = await _get_sa_token(client, sa)
        resp = await client.post(
            "/api/messages/m2m/send",
            headers=get_auth_header(token),
            json={
                "title": "Relative no front",
                "target_usernames": [test_user.username],
                "action_url": "/foo/1",
            },
        )
        assert resp.status_code == 200, resp.text
        mid = resp.json()["message_ids"][0]
        msg = await _fetch_message(async_session, mid)
        # Pas de frontend_url : URL passe inchangée
        assert msg.action_url == "/foo/1"
