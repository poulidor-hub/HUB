# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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
    close_open_segments, close_segments_for_session, touch_app_presence,
)


async def _make_session(async_session, user):
    now = utcnow()
    s = UserSession(user_id=user.id, login_at=now, last_activity_at=now)
    async_session.add(s)
    await async_session.commit()
    await async_session.refresh(s)
    return s


@pytest.mark.asyncio
async def test_parallel_apps_keep_both_segments_open(async_session, test_user):
    s = await _make_session(async_session, test_user)

    await touch_app_presence(async_session, s, "test-app")
    await touch_app_presence(async_session, s, "other-app")

    segs = (await async_session.execute(
        select(AppUsageSegment).order_by(AppUsageSegment.id)
    )).scalars().all()
    # Usage parallèle : les DEUX segments restent ouverts
    assert [x.app_slug for x in segs] == ["test-app", "other-app"]
    assert all(x.ended_at is None for x in segs)


@pytest.mark.asyncio
async def test_same_app_twice_opens_single_segment(async_session, test_user):
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    await touch_app_presence(async_session, s, "test-app")

    segs = (await async_session.execute(select(AppUsageSegment))).scalars().all()
    assert len(segs) == 1


@pytest.mark.asyncio
async def test_close_segments_for_session(async_session, test_user):
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")

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
    await touch_app_presence(async_session, s, "test-app")

    await end_session(async_session, test_user.id, reason="explicit")

    seg = (await async_session.execute(select(AppUsageSegment))).scalar_one()
    assert seg.ended_at is not None
    assert seg.end_reason == "logout"   # "explicit" mappé vers "logout"


@pytest.mark.asyncio
async def test_start_session_closes_orphan_open_segments(async_session, test_user):
    from app.routes.audit import start_session
    from app.usage_tracking import touch_app_presence

    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")

    await start_session(async_session, test_user.id)

    seg = (await async_session.execute(select(AppUsageSegment))).scalar_one()
    assert seg.ended_at is not None
    assert seg.end_reason == "timeout"
