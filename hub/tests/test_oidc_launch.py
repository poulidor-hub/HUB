"""Tests pour /api/oidc/launch/{client_id} (redirecteur vers page login app externe).

Le launch shortcut valide le JWT HUB du dashboard, vérifie l'accès user,
puis redirige vers le frontend_url de l'app HUB liée (page de login OIDC du
client externe, ex: Vikunja /auth/openid/auth). SANS access_token dans l'URL
finale (sinon l'app externe l'interprète comme un retour de flow OIDC).
"""
import pytest
from httpx import AsyncClient
from app.models import Application, OAuthClient
from app.auth import create_access_token, get_password_hash
from tests.conftest import get_auth_header


@pytest.fixture
async def vikunja_app_and_client(async_session, test_user):
    """Crée une app HUB 'vikunja' (avec frontend_url) + un client OIDC lié."""
    app = Application(
        name="Vikunja",
        slug="vikunja",
        api_url="http://192.0.2.10:3457",
        frontend_url="http://192.0.2.10:3457/auth/openid/auth",
        callback_url="http://192.0.2.10:8000/api/oidc/launch/vikunja",
        is_active=True,
        requires_auth=True,
    )
    async_session.add(app)
    await async_session.flush()

    client = OAuthClient(
        client_id="vikunja",
        client_secret_hash=get_password_hash("vike-secret"),
        name="Vikunja",
        redirect_uris=["http://192.0.2.10:3457/auth/openid/hub/callback"],
        scopes=["openid", "profile", "email"],
        app_id=app.id,
    )
    async_session.add(client)
    await async_session.commit()
    return app, client


class TestLaunchRedirector:

    @pytest.mark.asyncio
    async def test_launch_redirects_to_frontend_url(
        self, client: AsyncClient, vikunja_app_and_client, test_user
    ):
        """JWT valide + client lié à app → 302 vers frontend_url (sans access_token)."""
        token, _, _ = create_access_token(
            username=test_user.username, apps={"vikunja": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers["location"]
        assert location == "http://192.0.2.10:3457/auth/openid/auth"
        # L'access_token ne doit PAS être dans l'URL finale
        assert "access_token" not in location

    @pytest.mark.asyncio
    async def test_launch_rejects_invalid_jwt(self, client: AsyncClient, vikunja_app_and_client):
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={"access_token": "invalid-token"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_launch_rejects_unknown_client(self, client: AsyncClient, test_user):
        token, _, _ = create_access_token(
            username=test_user.username, apps={"unknown-app": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/unknown-client",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_launch_rejects_inactive_client(
        self, client: AsyncClient, async_session, test_user
    ):
        # App + client inactif
        app = Application(
            name="Inactive App", slug="inactive-app",
            api_url="http://example.com", frontend_url="http://example.com/login",
            is_active=True, requires_auth=True,
        )
        async_session.add(app)
        await async_session.flush()
        client_obj = OAuthClient(
            client_id="inactive", client_secret_hash=get_password_hash("x"),
            name="Inactive", redirect_uris=["http://example.com/cb"],
            is_active=False, app_id=app.id,
        )
        async_session.add(client_obj)
        await async_session.commit()

        token, _, _ = create_access_token(
            username=test_user.username, apps={"inactive-app": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/inactive",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_launch_rejects_client_without_app_id(
        self, client: AsyncClient, async_session, test_user
    ):
        """Client OIDC sans app_id → 400 (impossible de déterminer frontend_url)."""
        client_obj = OAuthClient(
            client_id="orphan", client_secret_hash=get_password_hash("x"),
            name="Orphan", redirect_uris=["http://example.com/cb"],
            app_id=None,
        )
        async_session.add(client_obj)
        await async_session.commit()

        token, _, _ = create_access_token(
            username=test_user.username, apps={"orphan": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/orphan",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_launch_rejects_user_without_app_role(
        self, client: AsyncClient, vikunja_app_and_client, test_user
    ):
        """User dont le JWT ne contient pas 'vikunja' dans apps → 403."""
        token, _, _ = create_access_token(
            username=test_user.username, apps={"other-app": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 403
