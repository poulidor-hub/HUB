# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Robustesse des requêtes de session quand plusieurs sessions ouvertes coexistent."""
import pytest
from sqlalchemy import select

from app.models import UserSession, utcnow
from app.routes.audit import end_session, update_session_activity
from tests.test_usage_segments import _make_session


@pytest.mark.asyncio
async def test_update_activity_with_duplicate_open_sessions(async_session, test_user):
    s_old = await _make_session(async_session, test_user)
    s_new = await _make_session(async_session, test_user)
    s_old.login_at = s_new.login_at.replace(year=2020)  # ordre explicite

    before = utcnow()
    updated = await update_session_activity(async_session, test_user.id)
    await async_session.commit()

    assert updated is not None
    assert updated.id == s_new.id
    assert updated.last_activity_at >= before


@pytest.mark.asyncio
async def test_end_session_with_duplicate_open_sessions(async_session, test_user):
    s_old = await _make_session(async_session, test_user)
    s_new = await _make_session(async_session, test_user)
    s_old.login_at = s_new.login_at.replace(year=2020)

    closed = await end_session(async_session, test_user.id, reason="explicit")
    await async_session.commit()

    assert closed.id == s_new.id
    assert closed.logout_at is not None
    # L'ancienne reste ouverte (fermée par le dédoublonnage cleanup, Task 1.7)
    result = await async_session.execute(
        select(UserSession).where(UserSession.logout_at == None)  # noqa: E711
    )
    assert [s.id for s in result.scalars().all()] == [s_old.id]
