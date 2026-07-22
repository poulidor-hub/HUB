# HUB_base/hub/tests/test_oidc_launch.py
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from app.models import OAuthClient, OAuthAuthorizationCode
from app.auth import create_access_token, get_password_hash, hash_token
from tests.conftest import get_auth_header


@pytest.fixture
async def vikunja_client(async_session):
    client = OAuthClient(
        client_id="vikunja",
        client_secret_hash=get_password_hash("vike-secret"),
        name="Vikunja",
        redirect_uris=["https://vikunja.example.com/auth/openid/auth"],
        scopes=["openid", "profile", "email"],
    )
    async_session.add(client)
    await async_session.commit()
    return client


class TestLaunchShortcut:

    @pytest.mark.asyncio
    async def test_launch_returns_302_with_code(
        self, client: AsyncClient, vikunja_client, test_user
    ):
        # Créer une app HUB "vikunja" + UserAppRole pour test_user
        from app.models import Application, UserAppRole
        from tests.conftest import test_app
        # NOTE: test_app fixture existe déjà dans conftest, slug différent.
        # Pour ce test, on simule un user avec accès à l'app via token.
        token, _, _ = create_access_token(
            username=test_user.username, apps={"vikunja": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={
                "access_token": token,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "state": "xyz-state",
                "nonce": "nonce-123",
                "scope": "openid profile email",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers["location"]
        assert "code=" in location
        assert "state=xyz-state" in location
        assert location.startswith("https://vikunja.example.com/auth/openid/auth")

    @pytest.mark.asyncio
    async def test_launch_creates_authorization_code_in_db(
        self, client: AsyncClient, vikunja_client, test_user, async_session
    ):
        token, _, _ = create_access_token(
            username=test_user.username, apps={"vikunja": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={
                "access_token": token,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "state": "s1",
                "nonce": "n1",
                "scope": "openid",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        # Vérifier qu'un code a été persisté
        result = await async_session.execute(
            select(OAuthAuthorizationCode).where(
                OAuthAuthorizationCode.client_id == "vikunja"
            )
        )
        codes = result.scalars().all()
        assert len(codes) == 1
        assert codes[0].used_at is None
        assert codes[0].nonce == "n1"

    @pytest.mark.asyncio
    async def test_launch_rejects_invalid_jwt(self, client: AsyncClient, vikunja_client):
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={
                "access_token": "invalid-token",
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
            },
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
            params={
                "access_token": token,
                "redirect_uri": "https://example.com/cb",
            },
            follow_redirects=False,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_launch_rejects_invalid_redirect_uri(
        self, client: AsyncClient, vikunja_client, test_user
    ):
        token, _, _ = create_access_token(
            username=test_user.username, apps={"vikunja": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={
                "access_token": token,
                "redirect_uri": "https://evil.example.com/cb",  # pas dans la whitelist
            },
            follow_redirects=False,
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_launch_rejects_user_without_app_role(
        self, client: AsyncClient, vikunja_client, test_user
    ):
        # test_user n'a pas l'app "vikunja" dans son token
        token, _, _ = create_access_token(
            username=test_user.username, apps={"other-app": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={
                "access_token": token,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
            },
            follow_redirects=False,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_launch_rejects_inactive_client(
        self, client: AsyncClient, async_session, test_user
    ):
        client_obj = OAuthClient(
            client_id="inactive",
            client_secret_hash=get_password_hash("x"),
            name="Inactive",
            redirect_uris=["https://example.com/cb"],
            is_active=False,
        )
        async_session.add(client_obj)
        await async_session.commit()

        token, _, _ = create_access_token(
            username=test_user.username, apps={"inactive": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/inactive",
            params={
                "access_token": token,
                "redirect_uri": "https://example.com/cb",
            },
            follow_redirects=False,
        )
        assert response.status_code == 403
