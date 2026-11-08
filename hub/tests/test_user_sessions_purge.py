# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Purge des user_sessions anciennes (rétention 180 j par défaut).

Audit 08/11/2026 : user_sessions n'avait AUCUNE rétention (37,6 k lignes,
croissance perpétuelle du churn, sans index sur login_at).
"""
import pytest
from datetime import timedelta
from sqlalchemy import select

from app.models import UserSession, utcnow
from app.routes.audit import purge_old_sessions
from app.config import get_settings


async def _make_session_at(session, user, days_ago: int) -> int:
    s = UserSession(
        user_id=user.id,
        login_at=utcnow() - timedelta(days=days_ago),
        last_activity_at=utcnow() - timedelta(days=days_ago),
    )
    session.add(s)
    await session.flush()
    return s.id


@pytest.mark.asyncio
async def test_purge_old_sessions_keeps_recent(async_session, test_user):
    recent_id = await _make_session_at(async_session, test_user, days_ago=10)
    deleted = await purge_old_sessions(async_session, retention_days=180)
    await async_session.commit()

    row = (await async_session.execute(
        select(UserSession).where(UserSession.id == recent_id)
    )).scalar_one_or_none()
    assert row is not None, "une session récente ne doit jamais être purgée"
    assert deleted == 0


@pytest.mark.asyncio
async def test_purge_old_sessions_removes_expired(async_session, test_user):
    old_id = await _make_session_at(async_session, test_user, days_ago=200)
    recent_id = await _make_session_at(async_session, test_user, days_ago=5)
    deleted = await purge_old_sessions(async_session, retention_days=180)
    await async_session.commit()

    old = (await async_session.execute(
        select(UserSession).where(UserSession.id == old_id)
    )).scalar_one_or_none()
    recent = (await async_session.execute(
        select(UserSession).where(UserSession.id == recent_id)
    )).scalar_one_or_none()
    assert old is None, "une session de 200 jours doit être purgée"
    assert recent is not None
    assert deleted >= 1


@pytest.mark.asyncio
async def test_zero_retention_disables_purge(async_session, test_user):
    old_id = await _make_session_at(async_session, test_user, days_ago=400)
    deleted = await purge_old_sessions(async_session, retention_days=0)
    await async_session.commit()

    row = (await async_session.execute(
        select(UserSession).where(UserSession.id == old_id)
    )).scalar_one_or_none()
    assert row is not None, "rétention 0 = pas de purge (cohérent avec purge_old_logs)"


def test_setting_exists_with_default_180():
    assert get_settings().session_retention_days == 180
