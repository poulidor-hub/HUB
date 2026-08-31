"""Tests de la recréation des conteneurs d'une app (POST /api/apps/{slug}/recreate).

Opération tracée via le moteur workshop_operations (handler "recreate") :
docker compose -p <slug> up -d --force-recreate + health-check API.
"""
import asyncio

import pytest
from sqlalchemy import select

import app.workshop_service as ws
from app.models import WorkshopOperation
from tests.conftest import get_auth_header


# --- Auth / 4xx ---

async def test_recreate_requires_admin(client):
    assert (await client.post("/api/apps/x/recreate")).status_code == 401


async def test_recreate_unknown_app(client, admin_token):
    r = await client.post("/api/apps/inconnu/recreate",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 404


async def test_recreate_workshop_rejected(client, admin_token, async_session):
    from app.models import Application
    app = Application(name="Ws", slug="app-ws", api_url="http://127.0.0.1:1",
                      is_workshop=True)
    async_session.add(app)
    await async_session.commit()
    r = await client.post("/api/apps/app-ws/recreate",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 400
    assert "atelier" in r.json()["detail"].lower()


async def test_recreate_system_app_rejected(client, admin_token, async_session):
    from app.models import Application
    app = Application(name="Hub", slug="hub", api_url="http://127.0.0.1:1",
                      is_system_app=True)
    async_session.add(app)
    await async_session.commit()
    r = await client.post("/api/apps/hub/recreate",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 400


async def test_recreate_conflict_when_operation_running(client, admin_token,
                                                        async_session,
                                                        monkeypatch):
    from app.models import Application
    app = Application(name="A", slug="app-a", api_url="http://127.0.0.1:1")
    async_session.add(app)
    async_session.add(WorkshopOperation(app_slug="app-a", operation="recreate",
                                        status="running"))
    await async_session.commit()
    r = await client.post("/api/apps/app-a/recreate",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 409


# --- 202 + flow réel ---

async def test_recreate_accepted(client, admin_token, async_session,
                                 monkeypatch):
    from app.models import Application
    app = Application(name="A", slug="app-a", api_url="http://127.0.0.1:1")
    async_session.add(app)
    await async_session.commit()

    async def _fake_start(slug, operation, triggered_by):
        return 4242

    monkeypatch.setattr("app.routes.app_recreate.start_workshop_operation",
                        _fake_start)
    r = await client.post("/api/apps/app-a/recreate",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 202
    data = r.json()
    assert data == {"operation_id": 4242, "slug": "app-a",
                    "operation": "recreate", "status": "pending"}


async def test_op_recreate_runs_force_recreate_and_health(
        async_session, monkeypatch, tmp_path):
    """Le handler exécute up -d --force-recreate (projet = slug) puis le
    health-check sur API_PORT du .env, et pose status="success" en DB."""
    app_dir = tmp_path / "app-a"
    app_dir.mkdir()
    (app_dir / "docker-compose.yml").write_text("services: {}\n")
    (app_dir / ".env").write_text("APP_SLUG=app-a\nAPI_PORT=19001\n")

    op = WorkshopOperation(app_slug="app-a", operation="recreate",
                           status="running")
    async_session.add(op)
    await async_session.commit()

    calls: list[tuple[list, str | None]] = []

    async def _fake_run_cmd(cmd, cwd, timeout, env_extra=None):
        calls.append((cmd, str(cwd)))
        return 0

    async def _fake_wait_healthy(port, timeout):
        assert port == 19001
        return True

    monkeypatch.setattr(ws, "_run_cmd", _fake_run_cmd)
    monkeypatch.setattr(ws, "_wait_healthy", _fake_wait_healthy)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)

    await ws._op_recreate(op.id, "app-a")

    assert len(calls) == 1
    cmd, cwd = calls[0]
    assert cmd[:4] == ["docker", "compose", "-p", "app-a"]
    assert "--force-recreate" in cmd
    assert "up" in cmd
    assert cwd == str(app_dir)

    await async_session.refresh(op)
    assert op.status == "success"
    assert op.finished_at is not None


async def test_op_recreate_fails_when_unhealthy(async_session, monkeypatch,
                                                tmp_path):
    app_dir = tmp_path / "app-a"
    app_dir.mkdir()
    (app_dir / "docker-compose.yml").write_text("services: {}\n")
    (app_dir / ".env").write_text("API_PORT=19001\n")

    async def _fake_run_cmd(cmd, cwd, timeout, env_extra=None):
        return 0

    async def _fake_wait_healthy(port, timeout):
        return False

    monkeypatch.setattr(ws, "_run_cmd", _fake_run_cmd)
    monkeypatch.setattr(ws, "_wait_healthy", _fake_wait_healthy)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="pas healthy"):
        await ws._op_recreate(1, "app-a")


async def test_op_recreate_missing_compose(async_session, monkeypatch, tmp_path):
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="docker-compose.yml introuvable"):
        await ws._op_recreate(1, "app-inexistant")
