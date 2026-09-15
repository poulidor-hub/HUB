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


@pytest.mark.asyncio
async def test_get_returns_metadata_without_secret(
    client: AsyncClient, async_session, test_app
):
    sa = await _make_sa_for_app(async_session, test_app)
    headers = _sa_headers(sa, test_app)
    created = (await client.post("/api/oidc/self-client",
                                 json={"redirect_uris": [BASE]},
                                 headers=headers)).json()
    got = await client.get("/api/oidc/self-client", headers=headers)
    assert got.status_code == 200
    data = got.json()
    assert data["client_id"] == created["client_id"]
    assert "client_secret" not in data


@pytest.mark.asyncio
async def test_get_404_when_no_client(client: AsyncClient, async_session, test_app):
    sa = await _make_sa_for_app(async_session, test_app)
    resp = await client.get("/api/oidc/self-client", headers=_sa_headers(sa, test_app))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_other_app_sa_cannot_see_client(
    client: AsyncClient, async_session, test_app, other_app
):
    sa_owner = await _make_sa_for_app(async_session, test_app, name="sa_owner")
    sa_other = await _make_sa_for_app(async_session, other_app, name="sa_other")
    await client.post("/api/oidc/self-client",
                      json={"redirect_uris": [BASE]},
                      headers=_sa_headers(sa_owner, test_app))
    resp = await client.get("/api/oidc/self-client",
                            headers=_sa_headers(sa_other, other_app))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_multi_app_sa_without_slug_conflict(client: AsyncClient, async_session, test_app, other_app):
    """SA multi-apps sans app_slug → 409 (ambiguïté)."""
    sa = ServiceAccount(
        name="sa_multi",
        client_id=ServiceAccount.generate_client_id(),
        client_secret_hash=get_password_hash("secret123"),
        is_active=True,
    )
    async_session.add(sa)
    await async_session.flush()
    await async_session.execute(service_account_app_access.insert().values([
        {"service_account_id": sa.id, "app_id": test_app.id},
        {"service_account_id": sa.id, "app_id": other_app.id},
    ]))
    await async_session.commit()
    token, _, _ = create_access_token(
        username=sa.client_id,
        apps={test_app.slug: "service", other_app.slug: "service"},
        is_service_account=True,
    )
    resp = await client.post("/api/oidc/self-client",
                             json={"redirect_uris": [BASE]},
                             headers=get_auth_header(token))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_multi_app_sa_with_app_slug_resolves(client: AsyncClient, async_session, test_app, other_app):
    """SA multi-apps + app_slug dans le périmètre → client lié à CETTE app."""
    sa = ServiceAccount(
        name="sa_multi2",
        client_id=ServiceAccount.generate_client_id(),
        client_secret_hash=get_password_hash("secret123"),
        is_active=True,
    )
    async_session.add(sa)
    await async_session.flush()
    await async_session.execute(service_account_app_access.insert().values([
        {"service_account_id": sa.id, "app_id": test_app.id},
        {"service_account_id": sa.id, "app_id": other_app.id},
    ]))
    await async_session.commit()
    token, _, _ = create_access_token(
        username=sa.client_id,
        apps={test_app.slug: "service", other_app.slug: "service"},
        is_service_account=True,
    )
    resp = await client.post("/api/oidc/self-client",
                             json={"redirect_uris": [BASE], "app_slug": test_app.slug},
                             headers=get_auth_header(token))
    assert resp.status_code == 201
    assert resp.json()["app_id"] == test_app.id


@pytest.mark.asyncio
async def test_app_slug_outside_sa_scope_rejected(client: AsyncClient, async_session, test_app, other_app):
    """app_slug hors du périmètre du SA → 403 (pas de provisioning pour autrui)."""
    sa = await _make_sa_for_app(async_session, test_app, name="sa_scope")
    resp = await client.post("/api/oidc/self-client",
                             json={"redirect_uris": [BASE], "app_slug": other_app.slug},
                             headers=_sa_headers(sa, test_app))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_post_rejects_user_jwt(client: AsyncClient, admin_token):
    """JWT admin utilisateur → 403 (route réservée aux SA)."""
    resp = await client.post("/api/oidc/self-client",
                             json={"redirect_uris": [BASE]},
                             headers=get_auth_header(admin_token))
    assert resp.status_code == 403
