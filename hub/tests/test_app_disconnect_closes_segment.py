"""La déconnexion de la connexion SSE d'une app ferme SON segment, pas les autres."""
import asyncio

import pytest

from sqlalchemy import select

from app.models import AppUsageSegment, utcnow
from app.routes.events import handle_app_disconnect
from app.usage_tracking import close_app_segment, touch_app_presence
from tests.test_usage_segments import _make_session


@pytest.mark.asyncio
async def test_close_app_segment_targets_only_one_app(async_session, test_user):
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    await touch_app_presence(async_session, s, "other-app")
    await async_session.commit()

    closed = await close_app_segment(async_session, s.id, "test-app", utcnow(), "disconnect")
    await async_session.commit()

    assert closed == 1
    segs = {x.app_slug: x for x in (await async_session.execute(select(AppUsageSegment))).scalars().all()}
    assert segs["test-app"].end_reason == "disconnect"
    assert segs["other-app"].ended_at is None


@pytest.mark.asyncio
async def test_handle_app_disconnect_closes_and_clears_current(async_engine, async_session, test_user, monkeypatch):
    from app.database import async_session_maker
    import app.database as database
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_maker", maker)

    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    s.current_app_slug = "test-app"
    await async_session.commit()

    async def _fast_sleep(_s): return None
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    await handle_app_disconnect(test_user.id, "test-app")

    async with maker() as fresh:
        segs = (await fresh.execute(select(AppUsageSegment))).scalars().all()
        assert all(x.end_reason == "disconnect" for x in segs)
