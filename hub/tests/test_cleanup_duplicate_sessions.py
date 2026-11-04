# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Le cleanup ferme les sessions ouvertes en surnombre (1 max par user)."""
import pytest
from sqlalchemy import select

from app.models import UserSession
from app.routes.audit import cleanup_stale_sessions
from tests.test_usage_segments import _make_session


@pytest.mark.asyncio
async def test_cleanup_closes_duplicate_open_sessions(async_session, test_user):
    s1 = await _make_session(async_session, test_user)
    s2 = await _make_session(async_session, test_user)
    s1.login_at = s2.login_at.replace(year=2020)  # s1 = plus ancienne
    await async_session.commit()

    closed = await cleanup_stale_sessions(async_session)

    assert closed >= 1
    result = await async_session.execute(
        select(UserSession).where(UserSession.logout_at == None)  # noqa: E711
    )
    remaining = result.scalars().all()
    assert [s.id for s in remaining] == [s2.id], "Seule la session la plus récente doit rester ouverte"
    assert s1.logout_reason == "timeout"
