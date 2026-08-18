"""Tests du déploiement automatique des apps (spec 2026-08-18-auto-deploy-apps)."""
import pytest
from sqlalchemy import select

from app.models import AppDeployment


@pytest.mark.asyncio
async def test_app_deployment_model_persists(async_session):
    """La table app_deployments existe et persiste (migration via Base.metadata en test)."""
    dep = AppDeployment(
        app_slug="exemple-app",
        status="running",
        current_step="clone",
        logs="clone…",
        triggered_by="admin",
    )
    async_session.add(dep)
    await async_session.commit()

    result = await async_session.execute(
        select(AppDeployment).where(AppDeployment.app_slug == "exemple-app")
    )
    fetched = result.scalar_one()
    assert fetched.id is not None
    assert fetched.status == "running"
    assert fetched.current_step == "clone"
    assert fetched.started_at is not None
    assert fetched.finished_at is None


@pytest.mark.asyncio
async def test_apps_host_path_setting_default(settings):
    """Le chemin APPS est configurable via APPS_HOST_PATH avec défaut utilisable."""
    from app.config import get_settings
    st = get_settings()
    assert hasattr(st, "apps_host_path")
    assert st.apps_host_path  # non vide par défaut
