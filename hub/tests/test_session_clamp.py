"""Aucune fermeture de session ne doit poser un logout_at dans le futur."""
from datetime import timedelta

import pytest

from app.models import UserSession, utcnow
from app.routes.audit import start_session
from tests.test_usage_segments import _make_session


@pytest.mark.asyncio
async def test_start_session_clamps_orphan_logout_at(async_session, test_user):
    # Orpheline dont la dernière activité est RÉCENTE (pas stale)
    s = await _make_session(async_session, test_user)
    s.last_activity_at = utcnow() - timedelta(minutes=2)
    await async_session.commit()

    await start_session(async_session, test_user.id)
    await async_session.commit()

    await async_session.refresh(s)
    assert s.logout_at is not None
    assert s.logout_at <= utcnow() + timedelta(seconds=1), (
        "logout_at dans le futur : la fin virtuelle doit être clampée à now()"
    )
