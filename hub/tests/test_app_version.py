"""Tests de la version d'app capturée à l'enregistrement et affichée au dashboard."""
import pytest
from unittest.mock import patch
from sqlalchemy import select

from app.models import Application
from app.auth import create_access_token
from tests.conftest import get_auth_header


def _make_app(slug: str, version=None) -> Application:
    return Application(
        name=slug.replace("-", " ").title(),
        slug=slug,
        description="x",
        icon="bi-gear",
        color="#007bff",
        api_url="http://localhost:9100",
        frontend_url="http://localhost:9101",
        callback_url="http://localhost:9101/callback",
        is_active=True,
        requires_auth=True,
        version=version,
    )


@pytest.mark.asyncio
async def test_application_version_column_persists(async_session):
    async_session.add(_make_app("versioned-app", version="1.2.3"))
    await async_session.commit()
    result = await async_session.execute(
        select(Application).where(Application.slug == "versioned-app")
    )
    assert result.scalar_one().version == "1.2.3"


@pytest.mark.asyncio
async def test_application_version_defaults_to_none(async_session):
    async_session.add(_make_app("no-version-app"))
    await async_session.commit()
    result = await async_session.execute(
        select(Application).where(Application.slug == "no-version-app")
    )
    assert result.scalar_one().version is None


@pytest.mark.asyncio
async def test_register_new_app_stores_version(client, async_session):
    with patch("app.routes.apps.is_docker_network_ip", return_value=True):
        response = await client.post(
            "/api/apps/register",
            json={"slug": "verapp", "name": "Ver App", "api_port": 9200, "version": "2.0.1"},
        )
    assert response.status_code == 200
    result = await async_session.execute(
        select(Application).where(Application.slug == "verapp")
    )
    assert result.scalar_one().version == "2.0.1"


@pytest.mark.asyncio
async def test_register_without_version_stays_none(client, async_session):
    with patch("app.routes.apps.is_docker_network_ip", return_value=True):
        response = await client.post(
            "/api/apps/register",
            json={"slug": "verapp-none", "name": "No Ver", "api_port": 9201},
        )
    assert response.status_code == 200
    result = await async_session.execute(
        select(Application).where(Application.slug == "verapp-none")
    )
    assert result.scalar_one().version is None


@pytest.mark.asyncio
async def test_reregister_updates_version(client, async_session):
    async_session.add(_make_app("verapp-upd", version="1.0.0"))
    await async_session.commit()
    with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
         patch("app.routes.apps.settings.force_replace_app", False):
        response = await client.post(
            "/api/apps/register",
            json={"slug": "verapp-upd", "name": "Upd", "api_port": 9202, "version": "1.1.0"},
        )
    assert response.status_code == 200
    result = await async_session.execute(
        select(Application).where(Application.slug == "verapp-upd")
    )
    assert result.scalar_one().version == "1.1.0"


@pytest.mark.asyncio
async def test_dashboard_returns_app_version_from_app(client, async_session, admin_user, test_app):
    test_app.version = "3.3.3"
    async_session.add(test_app)
    await async_session.commit()
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    response = await client.get("/api/apps/", headers=get_auth_header(token))
    assert response.status_code == 200
    target = next(a for a in response.json() if a["slug"] == "test-app")
    assert target["app_version"] == "3.3.3"


@pytest.mark.asyncio
async def test_dashboard_app_version_null_when_unset(client, async_session, admin_user, test_app):
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    response = await client.get("/api/apps/", headers=get_auth_header(token))
    target = next(a for a in response.json() if a["slug"] == "test-app")
    assert target["app_version"] is None
