import pytest
from app.routes.events import compute_dashboard_stats


@pytest.mark.asyncio
async def test_compute_dashboard_stats(async_session, test_app, test_user, admin_user, service_account):
    stats = await compute_dashboard_stats(async_session)
    assert stats["total_apps"] == 1
    assert stats["active_apps"] == 1
    assert stats["total_users"] == 2
    assert stats["total_service_accounts"] == 1
    assert stats["active_sessions"] == 0
