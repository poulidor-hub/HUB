# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Visibilité des 401 SSE : compteur mémoire (fin du trou noir).

Audit 08/11/2026 : les 401 SSE sont exclus d'uvicorn.access ET d'error_logs
(bruit legacy ~14k/jour) — un storm d'échecs d'auth réel devenait invisible.
Le compteur doit les compter (fenêtre glissante 1 h) et être exposé dans
les stats diffusées.
"""
import time

from app.routes import events


def setup_function(_):
    events._sse_auth_failures.clear()


def teardown_function(_):
    events._sse_auth_failures.clear()


def test_counts_recent_failures():
    now = time.time()
    events._sse_auth_failures.extend([now - 10, now - 20, now - 30])
    assert events.sse_auth_failures_last_hour() == 3


def test_ignores_failures_older_than_one_hour():
    now = time.time()
    events._sse_auth_failures.extend([now - 3601, now - 7200, now - 10])
    assert events.sse_auth_failures_last_hour() == 1


def test_record_appends_timestamp():
    events.record_sse_auth_failure()
    events.record_sse_auth_failure()
    assert len(events._sse_auth_failures) == 2
    assert events.sse_auth_failures_last_hour() == 2


def test_stats_include_sse_auth_failures():
    """broadcast_stats_update doit exposer sse_auth_failures_1h."""
    events._sse_auth_failures.extend([time.time()] * 4)

    captured = {}

    async def fake_broadcast(event_type, data):
        captured["event"] = event_type
        captured["data"] = data

    async def fake_compute(db):
        return {"total_apps": 1}

    async def fake_sessions():
        class _CM:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *a):
                return False

        return _CM()

    def fake_maker():
        class _CM:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *a):
                return False

        return _CM()

    import app.database as database
    import app.routes.events as ev

    original_broadcast = ev.broadcast_event
    original_compute = ev.compute_dashboard_stats
    original_maker = database.async_session_maker
    ev.broadcast_event = fake_broadcast
    ev.compute_dashboard_stats = fake_compute
    database.async_session_maker = fake_maker
    try:
        import asyncio

        asyncio.run(ev.broadcast_stats_update())
    finally:
        ev.broadcast_event = original_broadcast
        ev.compute_dashboard_stats = original_compute
        database.async_session_maker = original_maker

    assert captured["data"]["sse_auth_failures_1h"] == 4
    assert "connected_sse_clients" in captured["data"]
