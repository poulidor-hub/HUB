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


from app.usage_tracking import (
    close_open_segments, close_segments_for_session, record_app_switch,
)


async def _make_session(async_session, user):
    now = utcnow()
    s = UserSession(user_id=user.id, login_at=now, last_activity_at=now)
    async_session.add(s)
    await async_session.commit()
    await async_session.refresh(s)
    return s


@pytest.mark.asyncio
async def test_switch_creates_and_closes_segments(async_session, test_user):
    s = await _make_session(async_session, test_user)

    await record_app_switch(async_session, s, "test-app")
    await record_app_switch(async_session, s, "other-app")

    segs = (await async_session.execute(
        select(AppUsageSegment).order_by(AppUsageSegment.started_at, AppUsageSegment.id)
    )).scalars().all()
    assert len(segs) == 2
    assert segs[0].app_slug == "test-app"
    assert segs[0].end_reason == "switch"
    assert segs[0].ended_at is not None
    assert segs[1].app_slug == "other-app"
    assert segs[1].ended_at is None      # segment courant ouvert


@pytest.mark.asyncio
async def test_switch_same_app_does_not_duplicate(async_session, test_user):
    s = await _make_session(async_session, test_user)

    await record_app_switch(async_session, s, "test-app")
    await record_app_switch(async_session, s, "test-app")

    segs = (await async_session.execute(select(AppUsageSegment))).scalars().all()
    assert len(segs) == 1
    assert segs[0].ended_at is None


@pytest.mark.asyncio
async def test_close_segments_for_session(async_session, test_user):
    s = await _make_session(async_session, test_user)
    await record_app_switch(async_session, s, "test-app")

    await close_segments_for_session(async_session, s, "logout")

    seg = (await async_session.execute(select(AppUsageSegment))).scalar_one()
    assert seg.ended_at is not None
    assert seg.end_reason == "logout"


@pytest.mark.asyncio
async def test_update_session_activity_opens_segment(async_session, test_user):
    """Le hook update_session_activity doit créer un segment à la bascule d'app."""
    from app.routes.audit import update_session_activity
    s = await _make_session(async_session, test_user)

    await update_session_activity(async_session, test_user.id, app_slug="test-app")

    seg = (await async_session.execute(select(AppUsageSegment))).scalar_one()
    assert seg.app_slug == "test-app"
    assert seg.session_id == s.id
    assert seg.ended_at is None


@pytest.mark.asyncio
async def test_end_session_closes_open_segment(async_session, test_user):
    from app.routes.audit import end_session
    s = await _make_session(async_session, test_user)
    await record_app_switch(async_session, s, "test-app")

    await end_session(async_session, test_user.id, reason="explicit")

    seg = (await async_session.execute(select(AppUsageSegment))).scalar_one()
    assert seg.ended_at is not None
    assert seg.end_reason == "logout"   # "explicit" mappé vers "logout"
