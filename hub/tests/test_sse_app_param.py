# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Le param ?app= du flux SSE ouvre un segment d'usage pour l'app."""
import pytest

from sqlalchemy import select

from app.models import AppUsageSegment
from app.main import app
from app.routes.auth import generate_beacon_token
from tests.test_api_events_sse import _collecter_premier_ping


@pytest.mark.asyncio
@pytest.mark.slow
async def test_stream_with_app_opens_segment(async_engine, async_session, test_user, test_app):
    # Session ouverte + beacon valide
    from tests.test_usage_segments import _make_session
    await _make_session(async_session, test_user)
    beacon = generate_beacon_token(test_user.id)

    path = f"/api/events/stream?user_id={test_user.id}&beacon_token={beacon}&app=test-app"
    got_ping = await _collecter_premier_ping(path, timeout_s=22)
    assert got_ping, "aucun ping reçu en 22 s"

    segs = (await async_session.execute(select(AppUsageSegment))).scalars().all()
    assert any(s.app_slug == "test-app" and s.ended_at is None for s in segs), (
        "le heartbeat SSE doit ouvrir un segment pour l'app déclarée"
    )
