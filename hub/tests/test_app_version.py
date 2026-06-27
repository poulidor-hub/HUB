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
