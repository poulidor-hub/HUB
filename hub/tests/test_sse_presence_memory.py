# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Présence SSE en mémoire : heartbeat sans DB, flush par le sweeper,
réutilisation de session au reconnect (tolérance au churn navigateur ~60 s).

Contexte : audit SSE du 08/11/2026 — 1583 fermetures/24h pour 1 utilisateur,
deadlocks PostgreSQL sur user_sessions/app_usage_segments causés par
(INSERT session + UPDATE fermeture + heartbeat SELECT/UPDATE) par cycle de
reconnexion. Fix : le churn ne coûte plus aucune écriture DB.
"""
import time

import pytest

from app.routes import events


@pytest.fixture(autouse=True)
def _clean_memory_state():
    """Isolation : mémoire de présence vide avant/après chaque test."""
    events._last_seen.clear()
    events._last_app_slug.clear()
    saved_conns = dict(events._user_connections)
    events._user_connections.clear()
    yield
    events._last_seen.clear()
    events._last_app_slug.clear()
    events._user_connections.clear()
    events._user_connections.update(saved_conns)


@pytest.mark.asyncio
async def test_emit_heartbeat_memory_only(monkeypatch):
    """Le heartbeat ne fait AUCUN accès DB : il met à jour la mémoire."""
    calls = {"db": 0}

    async def fake_update(db, user_id, app_slug=None):
        calls["db"] += 1

    monkeypatch.setattr("app.routes.audit.update_session_activity", fake_update)

    await events._emit_heartbeat(99, "app-x")
    await events._emit_heartbeat(99, None)  # heartbeat suivant sans app

    assert calls["db"] == 0
    assert events._last_seen[99] > 0
    assert events._last_app_slug[99] == "app-x"  # dernier slug connu conservé


@pytest.mark.asyncio
async def test_flush_presence_updates_connected_users(monkeypatch):
    """Le sweeper flush la présence mémoire : une transaction pour tous."""
    calls = {"ensure": [], "commits": 0}

    async def fake_ensure(db, user_id, app_slug=None, ip_address=None, user_agent=None):
        calls["ensure"].append((user_id, app_slug))

    class FakeDB:
        async def commit(self):
            calls["commits"] += 1

    monkeypatch.setattr("app.routes.audit.ensure_session_presence", fake_ensure)

    now = time.time()
    events._last_seen[7] = now
    events._last_seen[8] = now
    events._last_app_slug[7] = "app-a"
    # user 9 : entrée obsolète (départ réel), plus aucune connexion -> ignoré
    events._last_seen[9] = now - 3600

    events._user_connections[7] = {"q7"}
    events._user_connections[8] = {"q8"}

    flushed = await events._flush_presence_to_db(FakeDB())

    assert flushed == 2
    assert sorted(calls["ensure"]) == [(7, "app-a"), (8, None)]
    assert calls["commits"] == 1


def test_needs_db_session_touch_window():
    """(Re)connexion dans la fenêtre de churn -> pas d'écriture DB au connect,
    sauf changement d'app courante (le segment d'usage doit suivre)."""
    events._last_seen[42] = time.time() - 10  # churn récent
    events._last_app_slug[42] = "app-x"
    assert events._needs_db_session_touch(42, "app-x") is False   # même app
    assert events._needs_db_session_touch(42, None) is False      # sans app
    assert events._needs_db_session_touch(42, "app-y") is True    # app changée

    events._last_seen[42] = time.time() - events.RECONNECT_REUSE_WINDOW_S - 1
    assert events._needs_db_session_touch(42) is True  # vraie absence

    events._last_seen.pop(43, None)
    assert events._needs_db_session_touch(43) is True  # première connexion


def test_disconnect_grace_exceeds_churn():
    """La grâce de fermeture doit dépasser le délai de reconnexion client
    (5 s sse.js, 10 s AuthContext exemple-app) pour que le churn annule
    le end_session planifié."""
    assert events.DISCONNECT_GRACE_S >= 30
