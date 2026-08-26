"""Tests du service atelier (workshop_service) — cycle pilotable depuis l'UI HUB.

Pattern : subprocess mockés (_FakeProc/_install_fake_exec, cf. test_app_deploy.py),
DB sqlite in-memory (conftest). Le contrat .workshop.env (7 clés hubctl v0.6.1)
est figé ici : toute divergence doit faire crier ces tests.
"""
import pytest

import app.workshop_service as ws
from app.models import WorkshopOperation


@pytest.mark.asyncio
async def test_workshop_operation_model_persists(async_session):
    op = WorkshopOperation(
        app_slug="wstest", operation="update", status="pending",
        triggered_by="admin",
    )
    async_session.add(op)
    await async_session.commit()
    await async_session.refresh(op)
    assert op.id is not None
    assert op.status == "pending"
    assert op.current_step is None
    assert op.started_at is not None


@pytest.mark.asyncio
async def test_workshop_retention_setting_default():
    from app.config import get_settings
    assert get_settings().workshop_retention_days == 30


# ── Noyau : contrat .workshop.env, git, allocation ──


def test_ws_env_keys_contract():
    """Contrat de dualité : format exact hubctl v0.6.1 (7 clés, ordre canonique).
    Si hubctl évolue, ce test doit crier des deux côtés."""
    assert ws.WS_ENV_KEYS == [
        "APP_SLUG", "API_PORT", "FRONTEND_PORT", "FRONTEND_HTTPS_PORT",
        "WS_REF", "WS_COMMIT", "APP_SUBNET",
    ]


def test_read_env_file(tmp_path):
    f = tmp_path / "ws.env"
    f.write_text("APP_SLUG=x-ws\nAPI_PORT=10000\n# comment\n\nWS_REF=main\n")
    assert ws._read_env_file(f) == {"APP_SLUG": "x-ws", "API_PORT": "10000", "WS_REF": "main"}


def test_write_ws_env_full_order(tmp_path):
    f = tmp_path / ".workshop.env"
    ws._write_ws_env_full(f, {
        "WS_COMMIT": "abc", "APP_SLUG": "x-ws", "API_PORT": "10000",
        "FRONTEND_PORT": "5200", "FRONTEND_HTTPS_PORT": "5201",
        "WS_REF": "main", "APP_SUBNET": "10.210.0.0/24",
    })
    lines = f.read_text().strip().splitlines()
    keys = [l.split("=")[0] for l in lines]
    assert keys == ws.WS_ENV_KEYS


def test_get_workshop_state_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    st = ws.get_workshop_state("wstest")
    assert st == {"exists": False, "ws_slug": None, "ws_ref": None, "ws_commit": None,
                  "api_port": None, "frontend_port": None, "subnet": None,
                  "git_ok": False, "version": None}


def test_get_workshop_state_present(tmp_path, monkeypatch):
    app = tmp_path / "wstest"; app.mkdir()
    (app / ".workshop.env").write_text(
        "APP_SLUG=wstest-ws\nAPI_PORT=10000\nFRONTEND_PORT=5200\n"
        "FRONTEND_HTTPS_PORT=5201\nWS_REF=main\nWS_COMMIT=abc123def\n"
        "APP_SUBNET=10.210.0.0/24\n")
    (app / "VERSION").write_text("0.8.3\n")
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "_git_state", lambda d: {"ok": True, "branch": "main", "head": "abc"})
    st = ws.get_workshop_state("wstest")
    assert st["exists"] is True
    assert st["ws_slug"] == "wstest-ws"
    assert st["ws_ref"] == "main" and st["ws_commit"] == "abc123def"
    assert st["version"] == "0.8.3" and st["git_ok"] is True


def test_alloc_ports_skips_used():
    used = set(range(10000, 10010)) | set(range(5200, 5210))
    res = ws._alloc_ports(used)
    assert res == (10010, 5210)


def test_alloc_ports_exhausted(monkeypatch):
    monkeypatch.setattr(ws, "WS_API_PORT_RANGE", (10000, 10002))
    used = {10000, 10001, 10002}
    assert ws._alloc_ports(used) is None


def test_alloc_subnet_first_free(monkeypatch):
    monkeypatch.setattr(ws, "_docker_used_subnets", lambda: set())
    monkeypatch.setattr(ws, "_used_ws_subnets", lambda: set())
    assert ws._alloc_subnet() == "10.210.0.0/24"


def test_alloc_subnet_skips_used(monkeypatch):
    monkeypatch.setattr(ws, "_docker_used_subnets", lambda: {"10.210.0.0/24"})
    monkeypatch.setattr(ws, "_used_ws_subnets", lambda: {"10.210.1.0/24"})
    assert ws._alloc_subnet() == "10.210.2.0/24"


def test_alloc_subnet_overlap_broad(monkeypatch):
    monkeypatch.setattr(ws, "_docker_used_subnets", lambda: {"10.0.0.0/8"})
    monkeypatch.setattr(ws, "_used_ws_subnets", lambda: set())
    assert ws._alloc_subnet() is None


async def test_has_active_workshop_operation(async_session):
    from app.models import WorkshopOperation
    assert await ws.has_active_workshop_operation(async_session) is False
    async_session.add(WorkshopOperation(app_slug="a", operation="update", status="running"))
    await async_session.commit()
    assert await ws.has_active_workshop_operation(async_session) is True
    assert await ws.has_active_workshop_operation(async_session, slug="b") is False
