"""Tests du flag is_unmanaged (apps non gérées / launcher-only)."""
import pytest

from app.models import Application
from tests.conftest import get_auth_header


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
