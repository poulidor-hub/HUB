# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Heartbeat SSE : cadence fixe indépendante du trafic, app_slug propagé."""
import pytest

from app.routes import events


@pytest.mark.asyncio
async def test_emit_heartbeat_updates_memory_not_db(monkeypatch):
    """Contrat anti-churn (08/11/2026) : le heartbeat ne touche plus la DB,
    il met à jour la présence mémoire (flush par le sweeper)."""
    calls = {"db": 0}

    async def fake_update(db, user_id, app_slug=None):
        calls["db"] += 1

    monkeypatch.setattr("app.routes.audit.update_session_activity", fake_update)
    events._last_seen.pop(42, None)
    events._last_app_slug.pop(42, None)
    try:
        await events._emit_heartbeat(42, "test-app")
        assert calls["db"] == 0
        assert 42 in events._last_seen
        assert events._last_app_slug[42] == "test-app"
    finally:
        events._last_seen.pop(42, None)
        events._last_app_slug.pop(42, None)


@pytest.mark.asyncio
async def test_emit_heartbeat_never_raises(monkeypatch):
    import app.database as database

    class Boom:
        async def __aenter__(self): raise RuntimeError("db down")
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(database, "async_session_maker", Boom)
    await events._emit_heartbeat(1)  # ne doit pas lever
