"""Tests propagation réseau : mapping slug→répertoire + apply-and-recreate.

Le slug registre d'une app peut différer du nom de son répertoire dans APPS/
(ex. `exemple-app` ↔ `exemple-app`) : le plan et l'apply doivent
résoudre le répertoire via APP_SLUG du .env, et l'endpoint batch
apply-and-recreate doit écrire les .env PUIS lancer les recréations tracées.
"""
import pytest

import app.workshop_service as ws
from app.models import Application
from app.routes import network as net
from tests.conftest import get_auth_header


async def _mk_app(async_session, slug, **kw):
    app_row = Application(slug=slug, name=f"App {slug}",
                          api_url="http://localhost:9100", is_active=True, **kw)
    async_session.add(app_row)
    await async_session.commit()
    return app_row


def _mk_app_dir(apps_dir, dirname, slug, hub_host):
    d = apps_dir / dirname
    d.mkdir(parents=True)
    (d / ".env").write_text(f"APP_SLUG={slug}\nHUB_HOST={hub_host}\n")
    return d


@pytest.fixture
def apps_dir(tmp_path, monkeypatch):
    root = tmp_path / "apps"
    root.mkdir()
    monkeypatch.setattr(net, "get_apps_dir", lambda: root)
    return root


@pytest.fixture
def effective_host(settings):
    settings.hub_host = "203.0.113.10"
    return "203.0.113.10"


# ==== Plan : mapping slug → répertoire ====

async def test_plan_maps_slug_to_directory(client, async_session, admin_token,
                                           apps_dir, effective_host):
    await _mk_app(async_session, "exemple-app")
    _mk_app_dir(apps_dir, "exemple-app", "exemple-app", "203.0.113.9")

    r = await client.get("/api/config/network/propagation-plan",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    apps = {a["slug"]: a for a in r.json()["apps"]}
    a = apps["exemple-app"]
    assert a["status"] == "pending"
    assert a["current_hub_host"] == "203.0.113.9"
    assert a["env_dir"] == "exemple-app"


async def test_plan_direct_directory_omits_env_dir(client, async_session,
                                                   admin_token, apps_dir,
                                                   effective_host):
    await _mk_app(async_session, "exemple-app")
    _mk_app_dir(apps_dir, "exemple-app", "exemple-app", "203.0.113.10")

    r = await client.get("/api/config/network/propagation-plan",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    apps = {a["slug"]: a for a in r.json()["apps"]}
    a = apps["exemple-app"]
    assert a["status"] == "up_to_date"
    assert a["env_dir"] is None


async def test_plan_no_env_when_no_directory_matches(client, async_session,
                                                     admin_token, apps_dir,
                                                     effective_host):
    await _mk_app(async_session, "orpheline")

    r = await client.get("/api/config/network/propagation-plan",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    apps = {a["slug"]: a for a in r.json()["apps"]}
    assert apps["orpheline"]["status"] == "no_env"


# ==== Apply : écriture dans le répertoire mappé ====

async def test_apply_writes_mapped_directory(client, async_session, admin_token,
                                             apps_dir, effective_host):
    await _mk_app(async_session, "exemple-app")
    d = _mk_app_dir(apps_dir, "exemple-app", "exemple-app", "203.0.113.9")

    r = await client.post("/api/config/network/propagation-plan/apply",
                          headers=get_auth_header(admin_token),
                          json={"slugs": ["exemple-app"]})
    assert r.status_code == 200
    applied = {a["slug"]: a for a in r.json()["applied"]}
    assert applied["exemple-app"]["status"] == "written"
    assert f"HUB_HOST={effective_host}" in (d / ".env").read_text()


# ==== Batch apply-and-recreate ====

async def test_apply_and_recreate_batch(client, async_session, admin_token,
                                        apps_dir, effective_host, monkeypatch):
    await _mk_app(async_session, "app-a")
    await _mk_app(async_session, "exemple-app")
    d1 = _mk_app_dir(apps_dir, "app-a", "app-a", "203.0.113.9")
    d2 = _mk_app_dir(apps_dir, "exemple-app", "exemple-app", "203.0.113.9")

    started = []

    async def _fake_start(slug, operation, triggered_by, ref=None):
        started.append((slug, operation))
        return 77 + len(started)

    async def _no_active(db, slug):
        return False

    monkeypatch.setattr(ws, "start_workshop_operation", _fake_start)
    monkeypatch.setattr(ws, "has_active_workshop_operation", _no_active)

    r = await client.post(
        "/api/config/network/propagation-plan/apply-and-recreate",
        headers=get_auth_header(admin_token),
        json={"slugs": ["app-a", "exemple-app"]})
    assert r.status_code == 202
    body = r.json()
    assert body["hub_host"] == effective_host
    results = {x["slug"]: x for x in body["results"]}
    assert results["app-a"]["status"] == "written"
    assert results["app-a"]["recreate"] == "started"
    assert results["app-a"]["operation_id"] == 78
    assert results["exemple-app"]["env_dir"] == "exemple-app"
    assert results["exemple-app"]["recreate"] == "started"
    assert started == [("app-a", "recreate"), ("exemple-app", "recreate")]
    assert f"HUB_HOST={effective_host}" in (d1 / ".env").read_text()
    assert f"HUB_HOST={effective_host}" in (d2 / ".env").read_text()


async def test_apply_and_recreate_skips_active_operation(
        client, async_session, admin_token, apps_dir, effective_host,
        monkeypatch):
    await _mk_app(async_session, "app-b")
    d = _mk_app_dir(apps_dir, "app-b", "app-b", "203.0.113.9")

    async def _active(db, slug):
        return True

    async def _fail_start(*a, **k):
        raise AssertionError("start_workshop_operation ne doit pas être appelé")

    monkeypatch.setattr(ws, "has_active_workshop_operation", _active)
    monkeypatch.setattr(ws, "start_workshop_operation", _fail_start)

    r = await client.post(
        "/api/config/network/propagation-plan/apply-and-recreate",
        headers=get_auth_header(admin_token),
        json={"slugs": ["app-b"]})
    assert r.status_code == 202
    entry = r.json()["results"][0]
    assert entry["status"] == "written"
    assert entry["recreate"] == "skipped_operation_active"
    assert "operation_id" not in entry
    # le .env est écrit même si la recréation est sautée
    assert f"HUB_HOST={effective_host}" in (d / ".env").read_text()


async def test_apply_and_recreate_no_env_and_excluded(
        client, async_session, admin_token, apps_dir, effective_host,
        monkeypatch):
    await _mk_app(async_session, "sans-rep")
    await _mk_app(async_session, "atelier-x", is_workshop=True)

    async def _fail_start(*a, **k):
        raise AssertionError("aucune recréation ne doit être lancée")

    async def _no_active(db, slug):
        return False

    monkeypatch.setattr(ws, "start_workshop_operation", _fail_start)
    monkeypatch.setattr(ws, "has_active_workshop_operation", _no_active)

    r = await client.post(
        "/api/config/network/propagation-plan/apply-and-recreate",
        headers=get_auth_header(admin_token),
        json={"slugs": ["sans-rep", "atelier-x"]})
    assert r.status_code == 202
    results = {x["slug"]: x for x in r.json()["results"]}
    assert results["sans-rep"]["status"] == "no_env"
    assert results["atelier-x"]["status"] == "excluded"


async def test_apply_and_recreate_requires_admin(client):
    r = await client.post(
        "/api/config/network/propagation-plan/apply-and-recreate",
        json={"slugs": ["x"]})
    assert r.status_code == 401


async def test_apply_and_recreate_bad_payload(client, admin_token):
    headers = get_auth_header(admin_token)
    for payload in ({}, {"slugs": []}, {"slugs": [""]}, {"slugs": "x"}):
        r = await client.post(
            "/api/config/network/propagation-plan/apply-and-recreate",
            headers=headers, json=payload)
        assert r.status_code == 400, payload
