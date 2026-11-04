# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests du flag is_unmanaged (apps non gérées / launcher-only)."""
import pytest
from unittest.mock import patch

from app.models import Application
from app.routes import apps_health
from app.routes.apps import auto_sync_docker_applications
from tests.conftest import get_auth_header


class _MockResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {"status": "ok"}
        self.reason_phrase = "OK"

    def json(self):
        return self._json


class _MockClient:
    """Mock httpx : répond 200 sur /api/health pour toute URL."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return _MockResp(200, {"status": "ok", "version": "1.0"})


@pytest.mark.asyncio
async def test_application_model_has_is_unmanaged_default_false():
    """Le modèle Application expose is_unmanaged, défaut False."""
    app = Application(
        name="X", slug="x", api_url="http://h:8000", frontend_url="http://h", is_active=True
    )
    assert hasattr(app, "is_unmanaged")
    # default côté Python (la BDD impose NOT NULL DEFAULT FALSE via migration)
    assert app.is_unmanaged is False or app.is_unmanaged is None  # avant flush


@pytest.mark.asyncio
async def test_patch_sets_is_unmanaged(client, admin_token, test_app):
    """PATCH /api/apps/{id} avec is_unmanaged=True → renvoyé à True."""
    resp = await client.patch(
        f"/api/apps/{test_app.id}",
        json={"is_unmanaged": True},
        headers=get_auth_header(admin_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_unmanaged"] is True


@pytest.mark.asyncio
async def test_app_response_defaults_is_unmanaged_false(client, admin_token, test_app):
    """Une app normale expose is_unmanaged=False."""
    resp = await client.get("/api/apps/", headers=get_auth_header(admin_token))
    assert resp.status_code == 200
    mine = [a for a in resp.json() if a["slug"] == test_app.slug]
    assert mine, "app de test absente du listing"
    assert mine[0]["is_unmanaged"] is False


@pytest.mark.asyncio
async def test_health_excludes_unmanaged_apps(client, admin_token, async_session):
    """GET /api/apps/health ne sonde pas les apps is_unmanaged."""
    managed = Application(
        name="Managed", slug="managed-app", api_url="http://m:8000",
        frontend_url="http://m", is_active=True,
    )
    unmanaged = Application(
        name="Unmanaged", slug="unmanaged-app", api_url="http://u:8000",
        frontend_url="http://u", is_active=True, is_unmanaged=True,
    )
    async_session.add_all([managed, unmanaged])
    await async_session.commit()

    with patch.object(apps_health, "_get_httpx_client", lambda: _MockClient()):
        resp = await client.get("/api/apps/health", headers=get_auth_header(admin_token))

    assert resp.status_code == 200, resp.text
    data = resp.json()
    apps_list = data.get("apps") or data.get("items") or []
    slugs = [a.get("slug") for a in apps_list]
    assert "managed-app" in slugs
    assert "unmanaged-app" not in slugs


@pytest.mark.asyncio
async def test_backup_trigger_rejects_unmanaged(client, admin_token, async_session):
    """POST /api/apps/{slug}/backup/trigger → 400 pour une app is_unmanaged."""
    app = Application(
        name="Unmanaged", slug="unmanaged-bkp", api_url="http://u:8000",
        is_active=True, is_unmanaged=True,
    )
    async_session.add(app)
    await async_session.commit()

    resp = await client.post(
        "/api/apps/unmanaged-bkp/backup/trigger",
        headers=get_auth_header(admin_token),
    )
    assert resp.status_code == 400, resp.text
    assert "non gérée" in resp.json()["detail"].lower()


class _FakeScanner:
    """Scanner factif : aucun conteneur running (aucun port actif)."""
    is_available = True
    last_error = None

    def scan_containers(self, include_stopped=False):
        return []


@pytest.mark.asyncio
async def test_auto_sync_skips_unmanaged_apps(async_session, monkeypatch):
    """auto_sync ne désactive pas une app is_unmanaged même si son port n'est pas running."""
    unmanaged = Application(
        name="Unmanaged", slug="unmanaged-sync", api_url="http://h:3456",
        is_active=True, is_unmanaged=True,
    )
    async_session.add(unmanaged)
    await async_session.commit()

    import app.docker_scanner as ds
    monkeypatch.setattr(ds, "docker_scanner", _FakeScanner())

    await auto_sync_docker_applications(async_session)

    await async_session.refresh(unmanaged)
    assert unmanaged.is_active is True, "une app is_unmanaged ne doit pas être désactivée par la sync"
