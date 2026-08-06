"""Tests intégration du login HUB avec propagation SLO poste partagé."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_password_hash
from app.config import Settings
from app.models import Application, OAuthClient, OAuthRefreshToken
from app.oidc_helpers import create_oidc_session_cookie


@pytest.fixture
async def vikunja_client(async_session: AsyncSession) -> OAuthClient:
    """Client OIDC de test 'vikunja' lié à une Application HUB (slug='vikunja').

    Le helper SLO ne remonte que les clients liés à une app avec frontend_url.
    Dupliquée depuis test_slo_helpers.py (fixtures non partagées entre modules).
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


def _login_payload(username: str, password: str) -> dict:
    return {
        "grant_type": "password",
        "username": username,
        "password": password,
    }


@pytest.mark.asyncio
async def test_login_no_remote_logout_when_no_previous_cookie(
    client: AsyncClient, test_user, settings: Settings
):
    """Premier login du poste (pas de cookie précédent) → pas de remote_logout."""
    settings.oidc_issuer = "http://test"
    response = await client.post(
        "/api/auth/token",
        data=_login_payload(test_user.username, "testpass123"),
    )
    assert response.status_code == 200
    body = response.json()
    # remote_logout absent OU vide
    assert body.get("remote_logout", []) == []


@pytest.mark.asyncio
async def test_login_returns_remote_logout_on_user_switch(
    client: AsyncClient, async_session: AsyncSession,
    test_user, admin_user, vikunja_client, settings: Settings
):
    """Login de admin_user alors qu'un cookie test_user est posé + test_user a un refresh OIDC actif."""
    settings.oidc_issuer = "http://test"
    settings.oidc_slo_enabled = True

    # 1. Poser le cookie test_user (simule session précédente)
    cookie = create_oidc_session_cookie(test_user.username, settings)
    client.cookies.set("hub_oidc_session", cookie)

    # 2. Créer un refresh token OIDC valide pour test_user
    refresh = OAuthRefreshToken(
        token_hash="hash_login_slo_1",
        client_id=vikunja_client.client_id,
        user_id=test_user.id,
        scope="openid profile email",
        expires_at=None,
        revoked_at=None,
    )
    async_session.add(refresh)
    await async_session.commit()

    # 3. Login en tant que admin_user
    response = await client.post(
        "/api/auth/token",
        data=_login_payload(admin_user.username, "admin123"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "remote_logout" in body
    assert len(body["remote_logout"]) == 1
    entry = body["remote_logout"][0]
    assert entry["slug"] == "vikunja"
    assert entry["logout_url"].endswith("/logout")
    assert entry["translator_slug"] == "exemple-app"


@pytest.mark.asyncio
async def test_login_no_remote_logout_when_slo_disabled(
    client: AsyncClient, async_session: AsyncSession,
    test_user, admin_user, vikunja_client, settings: Settings
):
    """Kill-switch OIDC_SLO_ENABLED=False → jamais de remote_logout."""
    settings.oidc_issuer = "http://test"
    settings.oidc_slo_enabled = False

    cookie = create_oidc_session_cookie(test_user.username, settings)
    client.cookies.set("hub_oidc_session", cookie)

    refresh = OAuthRefreshToken(
        token_hash="hash_login_slo_disabled",
        client_id=vikunja_client.client_id,
        user_id=test_user.id,
        scope="openid profile email",
        expires_at=None,
        revoked_at=None,
    )
    async_session.add(refresh)
    await async_session.commit()

    response = await client.post(
        "/api/auth/token",
        data=_login_payload(admin_user.username, "admin123"),
    )
    assert response.status_code == 200
    assert response.json().get("remote_logout", []) == []
