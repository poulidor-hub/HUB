"""Tests des routes /api/apps/{slug}/workshop* (vue santé)."""
import pytest

import app.workshop_service as ws
import app.routes.workshop as route_mod
from app.models import Application
from tests.conftest import get_auth_header


async def _mk_app(async_session, slug="wstest", repo="https://git.example/x.git"):
    # Adaptation du brief : api_url est nullable=False sur le modèle Application.
    app_row = Application(slug=slug, name="Test", api_url="http://localhost:9100",
                          git_repo_url=repo, is_active=True, requires_auth=True)
    async_session.add(app_row)
    await async_session.commit()
    return app_row


def _async_ret(value):
    async def _f(*a, **k):
        return value
    return _f


async def test_get_workshop_404(client, admin_token):
    r = await client.get("/api/apps/nimporte/workshop",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 404


async def test_get_workshop_state(client, async_session, admin_token, tmp_path, monkeypatch):
    await _mk_app(async_session)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    r = await client.get("/api/apps/wstest/workshop",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["exists"] is False and data["git_ok"] in (True, False)
    assert data["active_operation"] is False


async def test_endpoints_require_admin(client):
    # sans token → 401 (le path 403 non-admin existe via get_current_admin)
    r = await client.post("/api/apps/wstest/workshop")
    assert r.status_code == 401


async def test_create_conflict_when_exists(client, async_session, admin_token,
                                           tmp_path, monkeypatch):
    await _mk_app(async_session)
    app = tmp_path / "wstest"; app.mkdir(parents=True)
    ws._write_ws_env_full(app / ".workshop.env", {
        "APP_SLUG": "wstest-ws", "API_PORT": "10000", "FRONTEND_PORT": "5200",
        "FRONTEND_HTTPS_PORT": "5201", "WS_REF": "main", "WS_COMMIT": "x",
        "APP_SUBNET": "10.210.0.0/24"})
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    r = await client.post("/api/apps/wstest/workshop",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 409


async def test_update_conflict_when_absent(client, async_session, admin_token,
                                           tmp_path, monkeypatch):
    await _mk_app(async_session)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    r = await client.post("/api/apps/wstest/workshop/update",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 409


async def test_operation_conflict_409(client, async_session, admin_token,
                                      tmp_path, monkeypatch):
    from app.models import WorkshopOperation
    await _mk_app(async_session)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    async_session.add(WorkshopOperation(app_slug="autre", operation="update",
                                        status="running"))
    await async_session.commit()
    r = await client.post("/api/apps/wstest/workshop/update",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 409
    assert "en cours" in r.json()["detail"]


async def test_create_ok(client, async_session, admin_token, tmp_path, monkeypatch):
    await _mk_app(async_session)
    app = tmp_path / "wstest"; app.mkdir(parents=True)
    (app / "docker-compose.yml").write_text("services: {}\n")
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    monkeypatch.setattr(route_mod, "_git_state",
                        _async_ret({"ok": True, "branch": "main", "head": "a"}))
    monkeypatch.setattr(route_mod, "has_active_workshop_operation", _async_ret(False))
    started = {"called": False}
    async def _fake_start(slug, operation, triggered_by):
        started["called"] = True
        assert operation == "create"
        return 42
    monkeypatch.setattr(route_mod, "start_workshop_operation", _fake_start)
    r = await client.post("/api/apps/wstest/workshop",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 201
    assert r.json()["operation_id"] == 42 and started["called"]


async def test_status_shape(client, async_session, admin_token):
    await _mk_app(async_session)
    r = await client.get("/api/apps/wstest/workshop/status",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert "current" in data and "history" in data
    assert data["current"] is None
