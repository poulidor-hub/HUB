"""Self-service OIDC : l'app (SA M2M) provisionne son propre client OIDC."""
import pytest
from httpx import AsyncClient

from app.auth import create_access_token, get_password_hash
from app.models import ServiceAccount, service_account_app_access
from tests.conftest import get_auth_header


async def _make_sa_for_app(async_session, app, name="sa_selfclient"):
    """Insère un SA dédié à `app` (une seule application accessible)."""
    sa = ServiceAccount(
        name=name,
        client_id=ServiceAccount.generate_client_id(),
        client_secret_hash=get_password_hash("secret123"),
        is_active=True,
    )
    async_session.add(sa)
    await async_session.flush()
    await async_session.execute(
        service_account_app_access.insert().values(
            service_account_id=sa.id, app_id=app.id
        )
    )
    await async_session.commit()
    return sa


def _sa_headers(sa, app) -> dict:
    token, _, _ = create_access_token(
        username=sa.client_id,
        apps={app.slug: "service"},
        is_service_account=True,
    )
    return get_auth_header(token)


@pytest.mark.asyncio
async def test_dependency_rejects_user_token(client: AsyncClient):
    """Un JWT utilisateur (non-SA) est rejeté sur /self-client (403)."""
    token, _, _ = create_access_token(username="admin", apps={})
    resp = await client.post("/api/oidc/self-client",
                             json={"redirect_uris": ["http://x.test/cb"]},
                             headers=get_auth_header(token))
    assert resp.status_code == 403


BASE = "http://vikunja.test:3456/auth/openid/"


@pytest.mark.asyncio
async def test_create_returns_secret_once_and_binds_app(
    client: AsyncClient, async_session, test_app
):
    sa = await _make_sa_for_app(async_session, test_app)
    resp = await client.post(
        "/api/oidc/self-client",
        json={"redirect_uris": [BASE]},
        headers=_sa_headers(sa, test_app),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["client_id"].startswith("oc_")
    assert data["app_id"] == test_app.id
    assert len(data["client_secret"]) >= 30          # UNE SEULE FOIS
    assert data["redirect_uris"] == [BASE]
    assert data["authorization_endpoint"].endswith("/oidc/authorize")
    assert data["token_endpoint"].endswith("/api/oidc/token")


@pytest.mark.asyncio
async def test_recreate_without_regenerate_returns_no_secret(
    client: AsyncClient, async_session, test_app
):
    sa = await _make_sa_for_app(async_session, test_app)
    headers = _sa_headers(sa, test_app)
    first = (await client.post("/api/oidc/self-client",
                               json={"redirect_uris": [BASE]},
                               headers=headers)).json()
    second = await client.post(
        "/api/oidc/self-client",
        json={"redirect_uris": [BASE], "regenerate": False},
        headers=headers,
    )
    assert second.status_code == 200
    data = second.json()
    assert data["client_id"] == first["client_id"]
    assert "client_secret" not in data               # jamais re-exposé


@pytest.mark.asyncio
async def test_regenerate_rotates_secret(
    client: AsyncClient, async_session, test_app
):
    sa = await _make_sa_for_app(async_session, test_app)
    headers = _sa_headers(sa, test_app)
    first = (await client.post("/api/oidc/self-client",
                               json={"redirect_uris": [BASE]},
                               headers=headers)).json()
    again = (await client.post("/api/oidc/self-client",
                               json={"redirect_uris": [BASE]},
                               headers=headers)).json()  # regenerate défaut=True
    assert again["client_id"] == first["client_id"]
    assert again["client_secret"] != first["client_secret"]


@pytest.mark.asyncio
async def test_invalid_redirect_uri_rejected(client: AsyncClient, async_session, test_app):
    sa = await _make_sa_for_app(async_session, test_app)
    resp = await client.post(
        "/api/oidc/self-client",
        json={"redirect_uris": ["ftp://vikunja.test/cb"]},
        headers=_sa_headers(sa, test_app),
    )
    assert resp.status_code == 422
