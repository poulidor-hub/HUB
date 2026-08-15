"""Tests du modèle AppUsageSegment (tracking du temps par application)."""
import pytest
from sqlalchemy import select

from app.models import AppUsageSegment, UserSession, utcnow


@pytest.mark.asyncio
async def test_app_usage_segment_persistence(async_session, test_user):
    now = utcnow()
    s = UserSession(user_id=test_user.id, login_at=now, last_activity_at=now)
    async_session.add(s)
    await async_session.commit()
    await async_session.refresh(s)

    seg = AppUsageSegment(
        session_id=s.id, user_id=test_user.id,
        app_slug="test-app", started_at=now,
    )
    async_session.add(seg)
    await async_session.commit()
    await async_session.refresh(seg)

    assert seg.id is not None
    assert seg.ended_at is None       # segment ouvert
    assert seg.end_reason is None

    seg.ended_at = utcnow()
    seg.end_reason = "switch"
    await async_session.commit()

    refreshed = (await async_session.execute(
        select(AppUsageSegment).where(AppUsageSegment.id == seg.id)
    )).scalar_one()
    assert refreshed.end_reason == "switch"
    assert refreshed.ended_at is not None
