"""Chantier B Task 5 — API /api/routing/* (admin).

regenerate() est monkeypatché dans tous les tests (aucun reload Docker réel) ;
_resolve_local est monkeypatché pour le diagnostic de résolution.
"""

import pytest
from sqlalchemy import select

from app.models import AppSetting, Application, PortAllocation, RoutedService

BASE = "/api/routing"


@pytest.fixture
def resolver_up(monkeypatch):
    monkeypatch.setattr("app.routes.routing._resolver_status", lambda: True)
    monkeypatch.setattr(
        "app.routes.routing._resolve_local",
        lambda fqdn, server: "192.0.2.10",
    )


def _auth(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


async def _seed_zone(db, zone="hub.example.lan"):
    db.add(AppSetting(key="hub_dns_zone", value=zone))
    await db.commit()


async def _seed_app(db, slug="exemple-app", dns_name=None, active=True):
    app = Application(name=slug, slug=slug, api_url="http://192.0.2.10:9001",
                      frontend_url="http://192.0.2.10:4201",
                      dns_name=dns_name or slug, is_active=active)
    db.add(app)
    await db.flush()
    db.add(PortAllocation(app_slug=slug, host="192.0.2.10", api_port=9001,
                          frontend_port=4201, frontend_https_port=4202))
    await db.commit()
    return app


# ------------------------------------------------------------------ auth


@pytest.mark.asyncio
async def test_state_requires_admin(client):
    resp = await client.get(f"{BASE}/state")
    assert resp.status_code in (401, 403)


# ------------------------------------------------------------------ state


@pytest.mark.asyncio
async def test_state_zone_not_configured(client, admin_token, async_session, resolver_up):
    resp = await client.get(f"{BASE}/state", headers=_auth(admin_token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False
    assert data["zone"] == ""


@pytest.mark.asyncio
async def test_state_shows_app_with_fallback(client, admin_token, async_session,
                                             resolver_up, no_publish):
    await _seed_zone(async_session)
    await _seed_app(async_session)
    resp = await client.get(f"{BASE}/state", headers=_auth(admin_token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["resolver"]["active"] is True
    assert data["resolver"]["count"] == 1
    entry = next(a for a in data["apps"] if a["slug"] == "exemple-app")
    assert entry["fqdn"] == "exemple-app.hub.example.lan"
    assert entry["published"] is True
    assert entry["fallback"] == {"api": 9001, "front": 4201, "https": 4202}
    assert data["wildcard"] is None  # pas de cert émis en GET (état lecture seule)


# ------------------------------------------------------------------ zone


@pytest.mark.asyncio
async def test_put_zone_configures(client, admin_token, async_session, no_publish):
    resp = await client.put(f"{BASE}/zone", json={"zone": "hub.example.lan"},
                            headers=_auth(admin_token))
    assert resp.status_code == 200
    row = await async_session.get(AppSetting, "hub_dns_zone")
    assert row.value == "hub.example.lan"
    assert no_publish["n"] == 1


@pytest.mark.asyncio
async def test_put_zone_invalid_returns_422(client, admin_token, no_publish):
    resp = await client.put(f"{BASE}/zone", json={"zone": "priv"},
                            headers=_auth(admin_token))
    assert resp.status_code == 422
    assert no_publish["n"] == 0


# ------------------------------------------------------------------ dns_name


@pytest.mark.asyncio
async def test_put_app_dns_name(client, admin_token, async_session, no_publish):
    await _seed_zone(async_session)
    app = await _seed_app(async_session)
    resp = await client.put(f"{BASE}/apps/exemple-app", json={"dns_name": "inventaire"},
                            headers=_auth(admin_token))
    assert resp.status_code == 200
    await async_session.refresh(app)
    assert app.dns_name == "inventaire"
    assert no_publish["n"] == 1


@pytest.mark.asyncio
async def test_put_app_dns_name_collision_with_service_returns_422(
        client, admin_token, async_session, no_publish):
    await _seed_zone(async_session)
    await _seed_app(async_session)
    async_session.add(RoutedService(name="Pris", dns_name="inventaire.hub.example.lan",
                                    target_url="http://x:80"))
    await async_session.commit()
    resp = await client.put(f"{BASE}/apps/exemple-app", json={"dns_name": "inventaire"},
                            headers=_auth(admin_token))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_app_dns_name_reserved_returns_422(client, admin_token,
                                                     async_session, no_publish):
    await _seed_zone(async_session)
    await _seed_app(async_session)
    resp = await client.put(f"{BASE}/apps/exemple-app", json={"dns_name": "hub"},
                            headers=_auth(admin_token))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_app_unknown_returns_404(client, admin_token, no_publish):
    resp = await client.put(f"{BASE}/apps/ghost", json={"dns_name": "ghost"},
                            headers=_auth(admin_token))
    assert resp.status_code == 404


# ------------------------------------------------------------------ services


@pytest.mark.asyncio
async def test_services_crud(client, admin_token, async_session, no_publish):
    await _seed_zone(async_session)
    headers = _auth(admin_token)
    resp = await client.post(f"{BASE}/services", json={
        "name": "PhiliaNG", "dns_name": "service.example.org",
        "target_url": "https://host.docker.internal:4443"}, headers=headers)
    assert resp.status_code in (200, 201)
    svc = (await async_session.execute(select(RoutedService))).scalars().one()
    assert svc.dns_name == "service.example.org"
    assert no_publish["n"] == 1

    resp = await client.put(f"{BASE}/services/{svc.id}", json={
        "name": "PhiliaNG", "dns_name": "service.example.org",
        "target_url": "https://host:4443", "is_enabled": False}, headers=headers)
    assert resp.status_code == 200
    await async_session.refresh(svc)
    assert svc.is_enabled is False

    resp = await client.delete(f"{BASE}/services/{svc.id}", headers=headers)
    assert resp.status_code == 200
    assert (await async_session.execute(select(RoutedService))).scalars().all() == []


@pytest.mark.asyncio
async def test_service_duplicate_dns_name_returns_422(client, admin_token,
                                                      async_session, no_publish):
    async_session.add(RoutedService(name="A", dns_name="x.hub.example.lan",
                                    target_url="http://a:80"))
    await async_session.commit()
    resp = await client.post(f"{BASE}/services", json={
        "name": "B", "dns_name": "x.hub.example.lan", "target_url": "http://b:80"},
        headers=_auth(admin_token))
    assert resp.status_code == 422


# ------------------------------------------------------------------ resolve


@pytest.mark.asyncio
async def test_test_resolve(client, admin_token, async_session, resolver_up):
    await _seed_zone(async_session)
    resp = await client.get(f"{BASE}/test-resolve/exemple-app", headers=_auth(admin_token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["fqdn"] == "exemple-app.hub.example.lan"
    assert data["resolved"] is True
    assert data["ip"] == "192.0.2.10"
