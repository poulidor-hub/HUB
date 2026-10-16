"""Tests pour /oidc/authorize (endpoint standard OIDC Authorization).

Le flow : Vikunja redirige vers /oidc/authorize avec state/nonce/PKCE.
On lit le cookie hub_oidc_session (posé au login HUB) pour identifier l'user.
"""
import pytest
import time
import jwt as pyjwt
from sqlalchemy import select
from httpx import AsyncClient
from urllib.parse import parse_qs, urlparse
from app.models import OAuthClient, OAuthAuthorizationCode
from app.auth import get_password_hash
from app.oidc_helpers import create_oidc_session_cookie, decode_oidc_session_cookie


@pytest.fixture
async def vikunja_client(async_session):
    client = OAuthClient(
        client_id="vikunja",
        client_secret_hash=get_password_hash("vike-secret"),
        name="Vikunja",
        redirect_uris=["http://192.0.2.10:3457/auth/openid/hub/callback"],
        scopes=["openid", "profile", "email"],
    )
    async_session.add(client)
    await async_session.commit()
    return client


def _set_oidc_cookie(client: AsyncClient, username: str, settings) -> None:
    """Pose le cookie hub_oidc_session sur le client httpx."""
    settings.oidc_issuer = "http://192.0.2.10:8000"
    client.cookies.set("hub_oidc_session", create_oidc_session_cookie(username, settings))


class TestAuthorizeEndpoint:

    @pytest.mark.asyncio
    async def test_authorize_with_valid_cookie_returns_302(
        self, client: AsyncClient, vikunja_client, test_user, settings
    ):
        _set_oidc_cookie(client, test_user.username, settings)
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "vikunja",
                "redirect_uri": "http://192.0.2.10:3457/auth/openid/hub/callback",
                "response_type": "code",
                "scope": "openid profile email",
                "state": "xyz-state-123",
                "nonce": "nonce-abc",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers["location"]
        assert "code=" in location
        assert "state=xyz-state-123" in location
        assert location.startswith("http://192.0.2.10:3457/auth/openid/hub/callback")

    @pytest.mark.asyncio
    async def test_authorize_persists_code_with_nonce(
        self, client: AsyncClient, vikunja_client, test_user, settings, async_session
    ):
        _set_oidc_cookie(client, test_user.username, settings)
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "vikunja",
                "redirect_uri": "http://192.0.2.10:3457/auth/openid/hub/callback",
                "response_type": "code",
                "scope": "openid",
                "state": "s1",
                "nonce": "n1",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        result = await async_session.execute(
            select(OAuthAuthorizationCode).where(
                OAuthAuthorizationCode.client_id == "vikunja"
            )
        )
        codes = result.scalars().all()
        assert len(codes) == 1
        assert codes[0].nonce == "n1"
        assert codes[0].used_at is None

    @pytest.mark.asyncio
    async def test_authorize_without_cookie_returns_401(
        self, client: AsyncClient, vikunja_client
    ):
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "vikunja",
                "redirect_uri": "http://192.0.2.10:3457/auth/openid/hub/callback",
                "response_type": "code",
                "scope": "openid",
            },
            follow_redirects=False,
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_authorize_invalid_cookie_returns_401(
        self, client: AsyncClient, vikunja_client
    ):
        client.cookies.set("hub_oidc_session", "invalid-jwt-token")
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "vikunja",
                "redirect_uri": "http://192.0.2.10:3457/auth/openid/hub/callback",
                "response_type": "code",
                "scope": "openid",
            },
            follow_redirects=False,
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_authorize_unknown_client_returns_404(
        self, client: AsyncClient, test_user, settings
    ):
        _set_oidc_cookie(client, test_user.username, settings)
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "unknown-client",
                "redirect_uri": "http://example.com/cb",
                "response_type": "code",
                "scope": "openid",
            },
            follow_redirects=False,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_authorize_invalid_redirect_uri_returns_400(
        self, client: AsyncClient, vikunja_client, test_user, settings
    ):
        _set_oidc_cookie(client, test_user.username, settings)
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "vikunja",
                "redirect_uri": "https://evil.example.com/cb",
                "response_type": "code",
                "scope": "openid",
            },
            follow_redirects=False,
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_authorize_unsupported_response_type_returns_400(
        self, client: AsyncClient, vikunja_client, test_user, settings
    ):
        _set_oidc_cookie(client, test_user.username, settings)
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "vikunja",
                "redirect_uri": "http://192.0.2.10:3457/auth/openid/hub/callback",
                "response_type": "token",
                "scope": "openid",
            },
            follow_redirects=False,
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_authorize_inactive_client_returns_403(
        self, client: AsyncClient, async_session, test_user, settings
    ):
        client_obj = OAuthClient(
            client_id="inactive",
            client_secret_hash=get_password_hash("x"),
            name="Inactive",
            redirect_uris=["http://example.com/cb"],
            is_active=False,
        )
        async_session.add(client_obj)
        await async_session.commit()

        _set_oidc_cookie(client, test_user.username, settings)
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "inactive",
                "redirect_uri": "http://example.com/cb",
                "response_type": "code",
                "scope": "openid",
            },
            follow_redirects=False,
        )
        assert response.status_code == 403

    # --- Redirect login (navigateur) : SSO transparent -------------------

    @pytest.mark.asyncio
    async def test_authorize_without_cookie_html_accept_redirects_to_login(
        self, client: AsyncClient, vikunja_client, settings
    ):
        """Sans cookie, un navigateur (Accept: text/html) est redirigé vers le
        login dashboard avec reprise du flow (callback=URL authorize encodée)."""
        settings.oidc_issuer = "http://192.0.2.10:8000"
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "vikunja",
                "redirect_uri": "http://192.0.2.10:3457/auth/openid/hub/callback",
                "response_type": "code",
                "scope": "openid",
                "state": "my-state-123",
                "nonce": "my-nonce-456",
            },
            headers={"accept": "text/html,application/xhtml+xml"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers["location"]
        assert location.startswith("http://test/?callback=")
        resume_url = parse_qs(urlparse(location).query)["callback"][0]
        assert resume_url.startswith("http://192.0.2.10:8000/oidc/authorize?")
        resume_qs = parse_qs(urlparse(resume_url).query)
        assert resume_qs["client_id"] == ["vikunja"]
        assert resume_qs["redirect_uri"] == [
            "http://192.0.2.10:3457/auth/openid/hub/callback"
        ]
        assert resume_qs["state"] == ["my-state-123"]
        assert resume_qs["nonce"] == ["my-nonce-456"]

    @pytest.mark.asyncio
    async def test_authorize_invalid_cookie_html_accept_redirects_to_login(
        self, client: AsyncClient, vikunja_client, settings
    ):
        """Cookie invalide + navigateur → 302 vers le login dashboard aussi."""
        settings.oidc_issuer = "http://192.0.2.10:8000"
        client.cookies.set("hub_oidc_session", "invalid-jwt-token")
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "vikunja",
                "redirect_uri": "http://192.0.2.10:3457/auth/openid/hub/callback",
                "response_type": "code",
                "scope": "openid",
                "state": "st",
            },
            headers={"accept": "text/html"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "callback=" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_authorize_without_cookie_no_html_keeps_401_json(
        self, client: AsyncClient, vikunja_client
    ):
        """Sans Accept: text/html (curl, client API, tests) → 401 JSON inchangé."""
        response = await client.get(
            "/oidc/authorize",
            params={
                "client_id": "vikunja",
                "redirect_uri": "http://192.0.2.10:3457/auth/openid/hub/callback",
                "response_type": "code",
                "scope": "openid",
            },
            headers={"accept": "application/json"},
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert "Session HUB requise" in response.json()["detail"]


class TestOidcSessionCookie:

    def test_create_and_decode_roundtrip(self, test_user, settings):
        settings.oidc_issuer = "http://192.0.2.10:8000"
        token = create_oidc_session_cookie(test_user.username, settings)
        assert decode_oidc_session_cookie(token, settings) == test_user.username

    def test_decode_rejects_wrong_type(self, settings):
        claims = {
            "sub": "alice", "exp": int(time.time()) + 3600,
            "iat": int(time.time()), "type": "user",
        }
        token = pyjwt.encode(
            claims, settings.jwt_private_key, algorithm="RS256",
            headers={"kid": "hub-jwt-key-1"}
        )
        assert decode_oidc_session_cookie(token, settings) is None

    def test_decode_rejects_expired(self, settings):
        claims = {
            "sub": "alice", "exp": int(time.time()) - 3600,
            "iat": int(time.time()) - 7200, "type": "oidc_session",
        }
        token = pyjwt.encode(
            claims, settings.jwt_private_key, algorithm="RS256",
            headers={"kid": "hub-jwt-key-1"}
        )
        assert decode_oidc_session_cookie(token, settings) is None

    def test_decode_rejects_bad_signature(self, settings):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_pem = other_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        claims = {
            "sub": "alice", "exp": int(time.time()) + 3600,
            "iat": int(time.time()), "type": "oidc_session",
        }
        token = pyjwt.encode(claims, other_pem, algorithm="RS256")
        assert decode_oidc_session_cookie(token, settings) is None
