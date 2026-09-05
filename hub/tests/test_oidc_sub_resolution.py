# HUB_base/hub/tests/test_oidc_sub_resolution.py
"""Résolution OIDC par oidc_sub, fallback username pour sessions pré-déploiement.

Contrat Task 3 :
  - le cookie hub_oidc_session porte l'oidc_sub (posé au login/refresh) ;
  - les 3 résolutions OIDC (authorize, launch/re-auth, back-channel logout)
    retrouvent l'user par oidc_sub, avec fallback username pour les cookies
    et clients émis avant le déploiement (TTL cookie 8h).
"""
import datetime
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.auth import get_password_hash, hash_token, create_access_token
from app.config import get_settings
from app.models import OAuthClient, OAuthRefreshToken, User, utcnow
from app.oidc_helpers import create_oidc_session_cookie, decode_oidc_session_cookie
from tests.conftest import get_auth_header

SETTINGS = get_settings()


@pytest.fixture
async def renamed_user(async_session):
    """User renommé : username courant ≠ oidc_sub (immuable)."""
    user = User(
        username="jmartin",
        email="jmartin@example.com",
        hashed_password=get_password_hash("x"),
        oidc_sub="jdupont",
        is_active=True,
    )
    async_session.add(user)
    await async_session.commit()
    await async_session.refresh(user)
    return user


@pytest.fixture
async def vikunja_client(async_session):
    client = OAuthClient(
        client_id="vikunja",
        client_secret_hash=get_password_hash("vike-secret"),
        name="Vikunja",
        redirect_uris=["http://192.0.2.10:3457/auth/openid/hub/callback"],
        scopes=["openid", "profile", "email"],
        backchannel_logout_uri="http://192.0.2.10:3457/auth/openid/backchannel-logout",
    )
    async_session.add(client)
    await async_session.commit()
    return client


# ---------------------------------------------------------------------------
# Helper de résolution
# ---------------------------------------------------------------------------

def test_session_cookie_carries_value(settings):
    """Le cookie de session OIDC restitue la valeur passée (sub)."""
    token = create_oidc_session_cookie("jdupont", settings)
    assert decode_oidc_session_cookie(token, settings) == "jdupont"


@pytest.mark.asyncio
async def test_resolve_user_by_oidc_sub_with_username_fallback(async_session):
    """3 cas : résolution par oidc_sub, fallback username, introuvable."""
    from app.auth import resolve_user_by_oidc_sub

    user = User(
        username="jmartin",
        email="jmartin@example.com",
        hashed_password="x",
        oidc_sub="jdupont",
    )
    async_session.add(user)
    await async_session.commit()

    assert (await resolve_user_by_oidc_sub(async_session, "jdupont")) is not None   # par oidc_sub
    assert (await resolve_user_by_oidc_sub(async_session, "jmartin")) is not None   # fallback username
    assert (await resolve_user_by_oidc_sub(async_session, "inconnu")) is None


@pytest.mark.asyncio
async def test_resolve_prefers_oidc_sub_over_username(async_session):
    """oidc_sub d'abord : si un AUTRE user porte ce username, c'est le sub qui gagne."""
    from app.auth import resolve_user_by_oidc_sub

    renamed = User(
        username="jmartin", email="a@example.com", hashed_password="x",
        oidc_sub="jdupont",
    )
    recycler = User(
        username="jdupont", email="b@example.com", hashed_password="x",
        oidc_sub="recycled-uuid",
    )
    async_session.add_all([renamed, recycler])
    await async_session.commit()

    resolved = await resolve_user_by_oidc_sub(async_session, "jdupont")
    assert resolved.id == renamed.id


# ---------------------------------------------------------------------------
# /oidc/authorize : résolution via le cookie de session
# ---------------------------------------------------------------------------

def _set_oidc_cookie(client: AsyncClient, sub: str, settings) -> None:
    settings.oidc_issuer = "http://192.0.2.10:8000"
    client.cookies.set("hub_oidc_session", create_oidc_session_cookie(sub, settings))


class TestAuthorizeResolvesByOidcSub:

    @pytest.mark.asyncio
    async def test_cookie_with_oidc_sub_of_renamed_user(
        self, client: AsyncClient, vikunja_client, renamed_user, settings
    ):
        """Cookie portant l'oidc_sub d'un renommé → authorize résout le bon user."""
        _set_oidc_cookie(client, renamed_user.oidc_sub, settings)
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
        assert response.status_code == 302
        assert "code=" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_legacy_cookie_with_username(
        self, client: AsyncClient, vikunja_client, renamed_user, settings
    ):
        """Cookie legacy (pré-déploiement) portant le username → fallback OK."""
        _set_oidc_cookie(client, renamed_user.username, settings)
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
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_unknown_sub_returns_401(
        self, client: AsyncClient, vikunja_client, settings
    ):
        _set_oidc_cookie(client, "personne", settings)
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


# ---------------------------------------------------------------------------
# /api/oidc/backchannel-logout : résolution depuis payload.sub
# ---------------------------------------------------------------------------

@pytest.fixture
async def rt_for_renamed_user(async_session, vikunja_client, renamed_user):
    rt = OAuthRefreshToken(
        token_hash=hash_token("rt-renamed"),
        client_id=vikunja_client.client_id,
        user_id=renamed_user.id,
        scope="openid",
        expires_at=utcnow() + datetime.timedelta(days=30),
    )
    async_session.add(rt)
    await async_session.commit()
    return rt


class TestBackchannelResolvesByOidcSub:

    @pytest.mark.asyncio
    async def test_logout_by_oidc_sub_of_renamed_user(
        self, client: AsyncClient, vikunja_client, renamed_user,
        rt_for_renamed_user, admin_user, async_session,
    ):
        from unittest.mock import AsyncMock, MagicMock, patch

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock()
        mock_http.post.return_value.status_code = 200
        with patch("app.routes.oidc.httpx.AsyncClient", return_value=mock_http):
            response = await client.post(
                "/api/oidc/backchannel-logout/vikunja",
                json={"sub": renamed_user.oidc_sub},
                headers=get_auth_header(token),
            )
        assert response.status_code == 200
        await async_session.refresh(rt_for_renamed_user)
        assert rt_for_renamed_user.revoked_at is not None

    @pytest.mark.asyncio
    async def test_logout_by_legacy_username(
        self, client: AsyncClient, vikunja_client, renamed_user,
        admin_user, async_session,
    ):
        from unittest.mock import AsyncMock, MagicMock, patch

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock()
        mock_http.post.return_value.status_code = 200
        with patch("app.routes.oidc.httpx.AsyncClient", return_value=mock_http):
            response = await client.post(
                "/api/oidc/backchannel-logout/vikunja",
                json={"sub": renamed_user.username},
                headers=get_auth_header(token),
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_logout_unknown_sub_returns_404(
        self, client: AsyncClient, vikunja_client, admin_user,
    ):
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        response = await client.post(
            "/api/oidc/backchannel-logout/vikunja",
            json={"sub": "personne"},
            headers=get_auth_header(token),
        )
        assert response.status_code == 404
