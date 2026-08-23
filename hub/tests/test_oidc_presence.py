"""Le passage SSO OIDC crée/maintient session + segment d'usage."""
import pytest
from sqlalchemy import select

from app.models import AppUsageSegment, UserSession
from app.routes.audit import ensure_session_presence
from tests.test_usage_segments import _make_session


@pytest.mark.asyncio
async def test_ensure_presence_without_session_creates_it(async_session, test_user, test_app):
    s = await ensure_session_presence(async_session, test_user.id, app_slug=test_app.slug)
    await async_session.commit()
    assert s.logout_at is None
    segs = (await async_session.execute(select(AppUsageSegment))).scalars().all()
    assert [x.app_slug for x in segs] == [test_app.slug]


@pytest.mark.asyncio
async def test_ensure_presence_with_session_reuses_it(async_session, test_user, test_app):
    existing = await _make_session(async_session, test_user)
    s = await ensure_session_presence(async_session, test_user.id, app_slug=test_app.slug)
    assert s.id == existing.id
    open_count = len((await async_session.execute(
        select(UserSession).where(UserSession.logout_at == None)  # noqa: E711
    )).scalars().all())
    assert open_count == 1
