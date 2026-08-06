"""Tests unitaires du helper SLO (détection de changement d'user au login)."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_password_hash
from app.config import Settings
from app.models import Application, OAuthClient, OAuthRefreshToken
from app.oidc_helpers import create_oidc_session_cookie
from app.slo_helpers import (
    detect_user_switch_and_collect_logouts,
    lookup_translator_slug,
    APP_TRANSLATORS,
)


@pytest.fixture
async def vikunja_client(async_session: AsyncSession) -> OAuthClient:
    """Client OIDC de test 'vikunja' lié à une Application HUB (slug='vikunja').

    Contrairement à la fixture homonyme de test_oidc_authorize.py (qui laisse
    app_id=None), celle-ci crée une Application liée car le helper SLO ne
    remonte que les clients liés à une app avec frontend_url.
    """
    app = Application(
        name="Vikunja",
        slug="vikunja",
        description="Tasks",
        api_url="http://vikunja-api.test",
        frontend_url="http://vikunja.test",
        callback_url="http://vikunja.test/callback",
        is_active=True,
        requires_auth=True,
    )
    async_session.add(app)
    await async_session.commit()
    await async_session.refresh(app)

    client = OAuthClient(
        client_id="vikunja",
        client_secret_hash=get_password_hash("vike-secret"),
        name="Vikunja",
        redirect_uris=["http://vikunja.test/auth/openid/hub/callback"],
        scopes=["openid", "profile", "email"],
        app_id=app.id,
    )
    async_session.add(client)
    await async_session.commit()
    await async_session.refresh(client)
    return client


@pytest.mark.asyncio
async def test_no_switch_when_no_cookie(
    client: AsyncClient, async_session: AsyncSession, test_user, settings: Settings
):
    """Pas de cookie précédent → pas de SLO (premier login du poste)."""
    # Simuler une request sans cookie hub_oidc_session
    class FakeRequest:
        cookies = {}
    result = await detect_user_switch_and_collect_logouts(
        FakeRequest(), async_session, test_user.username, settings
    )
    assert result == []


@pytest.mark.asyncio
async def test_no_switch_when_same_user(
    client: AsyncClient, async_session: AsyncSession, test_user, settings: Settings
):
    """Cookie précédent = même user → pas de SLO (renouvellement de login)."""
    settings.oidc_issuer = "http://test"
    cookie = create_oidc_session_cookie(test_user.username, settings)

    class FakeRequest:
        cookies = {"hub_oidc_session": cookie}
    result = await detect_user_switch_and_collect_logouts(
        FakeRequest(), async_session, test_user.username, settings
    )
    assert result == []


@pytest.mark.asyncio
async def test_switch_detected_when_different_user_no_oauth_token(
    client: AsyncClient, async_session: AsyncSession, test_user, admin_user, settings: Settings
):
    """Cookie A, login B mais A n'a aucun refresh token OIDC → [] (rien à purger)."""
    settings.oidc_issuer = "http://test"
    cookie = create_oidc_session_cookie(test_user.username, settings)

    class FakeRequest:
        cookies = {"hub_oidc_session": cookie}
    result = await detect_user_switch_and_collect_logouts(
        FakeRequest(), async_session, admin_user.username, settings
    )
    assert result == []


@pytest.mark.asyncio
async def test_switch_returns_logout_for_active_refresh_token(
    client: AsyncClient, async_session: AsyncSession, test_user, admin_user,
    vikunja_client, settings: Settings
):
    """Cookie A, login B, A a un refresh token valide sur vikunja → renvoie l'app."""
    settings.oidc_slo_enabled = True  # activer explicitement (défaut False depuis 3.26.0)
    settings.oidc_issuer = "http://test"
    cookie = create_oidc_session_cookie(test_user.username, settings)

    # Créer un refresh token OIDC valide pour test_user sur le client vikunja
    refresh = OAuthRefreshToken(
        token_hash="hash_test_slo_1",
        client_id=vikunja_client.client_id,
        user_id=test_user.id,
        scope="openid profile email",
        expires_at=None,  # pas d'expiration
        revoked_at=None,
    )
    async_session.add(refresh)
    await async_session.commit()

    class FakeRequest:
        cookies = {"hub_oidc_session": cookie}
    result = await detect_user_switch_and_collect_logouts(
        FakeRequest(), async_session, admin_user.username, settings
    )
    assert len(result) == 1
    entry = result[0]
    assert entry["slug"] == "vikunja"
    assert entry["logout_url"].endswith("/logout")
    assert entry["translator_slug"] == "exemple-app"


@pytest.mark.asyncio
async def test_switch_skips_revoked_refresh_token(
    client: AsyncClient, async_session: AsyncSession, test_user, admin_user,
    vikunja_client, settings: Settings
):
    """Un refresh token révoqué ne doit pas être remonté."""
    from datetime import timedelta
    from app.auth import utcnow
    settings.oidc_slo_enabled = True  # activer explicitement (défaut False depuis 3.26.0)
    settings.oidc_issuer = "http://test"
    cookie = create_oidc_session_cookie(test_user.username, settings)

    refresh = OAuthRefreshToken(
        token_hash="hash_test_slo_revoked",
        client_id=vikunja_client.client_id,
        user_id=test_user.id,
        scope="openid profile email",
        expires_at=None,
        revoked_at=utcnow() - timedelta(hours=1),  # révoqué
    )
    async_session.add(refresh)
    await async_session.commit()

    class FakeRequest:
        cookies = {"hub_oidc_session": cookie}
    result = await detect_user_switch_and_collect_logouts(
        FakeRequest(), async_session, admin_user.username, settings
    )
    assert result == []


@pytest.mark.asyncio
async def test_kill_switch_disabled_returns_empty(
    client: AsyncClient, async_session: AsyncSession, test_user, admin_user, settings: Settings
):
    """OIDC_SLO_ENABLED=False → toujours [] (kill-switch)."""
    settings.oidc_slo_enabled = False
    settings.oidc_issuer = "http://test"
    cookie = create_oidc_session_cookie(test_user.username, settings)

    class FakeRequest:
        cookies = {"hub_oidc_session": cookie}
    result = await detect_user_switch_and_collect_logouts(
        FakeRequest(), async_session, admin_user.username, settings
    )
    assert result == []


def test_lookup_translator_slug_known():
    assert lookup_translator_slug("vikunja") == "exemple-app"


def test_lookup_translator_slug_unknown():
    assert lookup_translator_slug("unknown-app") is None
