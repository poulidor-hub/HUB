# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Heartbeat SSE : cadence fixe indépendante du trafic, app_slug propagé."""
import pytest

from app.routes import events


@pytest.mark.asyncio
async def test_emit_heartbeat_calls_update_activity(monkeypatch):
    called = {}

    async def fake_update(db, user_id, app_slug=None):
        called["user_id"] = user_id
        called["app_slug"] = app_slug

    class FakeMaker:
        def __init__(self): self.db = object()
        async def __aenter__(self): return self.db
        async def __aexit__(self, *a): return False

    monkeypatch.setattr("app.routes.audit.update_session_activity", fake_update)
    # Le module importe async_session_maker localement ; on patche la source :
    import app.database as database
    monkeypatch.setattr(database, "async_session_maker", FakeMaker)

    await events._emit_heartbeat(42, "test-app")
    assert called == {"user_id": 42, "app_slug": "test-app"}


@pytest.mark.asyncio
async def test_emit_heartbeat_never_raises(monkeypatch):
    import app.database as database

    class Boom:
        async def __aenter__(self): raise RuntimeError("db down")
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(database, "async_session_maker", Boom)
    await events._emit_heartbeat(1)  # ne doit pas lever
