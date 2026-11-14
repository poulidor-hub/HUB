# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Throttle du broadcast SSE `stats_updated` (anti-tempête heure de pointe).

Chaque login/logout/cleanup déclenche `notify_stats_update` → recalcul SQL +
broadcast `stats_updated` à tous les clients SSE. En rafale (série d'events),
cela générait autant de broadcasts : tempêtes SSE mesurées en heure de
pointe. Garanties après throttle (fenêtre glissante 5 s) :

- une rafale de N appels en <1 s → exactement 1 broadcast immédiat ;
- le DERNIER événement d'une rafale est TOUJOURS diffusé (flush final à la
  fin de la fenêtre) ;
- un appel hors fenêtre diffuse immédiatement (nouvelle fenêtre) ;
- les autres événements (broadcast_event global, send_to_user ciblé) ne
  sont PAS throttlés.

L'horloge du throttle (`events._now`) est monkeypatchée : la fenêtre de 5 s
est testée sans dormir 5 s réels.
"""
import asyncio
import time

import pytest

from app.routes import events


async def _wait_until(predicate, timeout_s: float = 2.0) -> bool:
    """Attend (boucle d'event loop) que le prédicat devienne vrai."""
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
    return predicate()


@pytest.fixture(autouse=True)
def reset_stats_throttle():
    """État du throttle propre avant/après chaque test (variables module)."""
    events._last_stats_broadcast = 0.0
    events._stats_pending = False
    events._stats_flush_task = None
    yield
    task = getattr(events, "_stats_flush_task", None)
    if task is not None and not task.done():
        task.cancel()
    events._last_stats_broadcast = 0.0
    events._stats_pending = False
    events._stats_flush_task = None


@pytest.fixture
def fake_clock_and_broadcast(monkeypatch):
    """Horloge factice (avance manuelle) + compteur de diffusions stats."""
    clock = {"t": 1000.0}
    broadcasts = {"n": 0}

    monkeypatch.setattr(events, "_now", lambda: clock["t"])

    async def fake_do_broadcast_stats():
        broadcasts["n"] += 1

    monkeypatch.setattr(events, "_do_broadcast_stats", fake_do_broadcast_stats)
    return clock, broadcasts


class TestStatsThrottle:
    @pytest.mark.asyncio
    async def test_burst_emits_one_immediate_and_one_final_flush(
        self, fake_clock_and_broadcast
    ):
        """10 notify_stats_update en <1 s → exactement 1 broadcast immédiat
        + 1 flush final dans la fenêtre (dernier événement jamais perdu)."""
        clock, broadcasts = fake_clock_and_broadcast

        # Rafale : 10 déclenchements quasi simultanés
        for _ in range(10):
            events.notify_stats_update()

        assert await _wait_until(lambda: broadcasts["n"] >= 1)
        # Laisser retomber la poudre (aucun autre broadcast ne doit arriver)
        await asyncio.sleep(0.1)

        assert broadcasts["n"] == 1, (
            f"rafale de 10 → {broadcasts['n']} broadcasts (attendu : 1 immédiat)"
        )

        # Fin de la fenêtre : le flush final re-diffuse
        clock["t"] += events.STATS_BROADCAST_WINDOW_S + 0.5
        assert await _wait_until(lambda: broadcasts["n"] == 2), (
            "le DERNIER événement de la rafale doit être diffusé (flush final)"
        )

        # Pas de diffusion en boucle après le flush
        await asyncio.sleep(0.1)
        assert broadcasts["n"] == 2

    @pytest.mark.asyncio
    async def test_call_outside_window_broadcasts_immediately(
        self, fake_clock_and_broadcast
    ):
        """Après la fenêtre, un nouvel appel diffuse immédiatement
        (fenêtre glissante, pas de throttle définitif)."""
        clock, broadcasts = fake_clock_and_broadcast

        # 1er appel : diffusion immédiate
        events.notify_stats_update()
        assert await _wait_until(lambda: broadcasts["n"] == 1)

        # Appel dans la fenêtre (t+2) : supprimé, flush final armé pour t+5
        clock["t"] += 2.0
        events.notify_stats_update()
        await asyncio.sleep(0.1)
        assert broadcasts["n"] == 1, "appel dans la fenêtre : doit être throttlé"

        # Fin de fenêtre (t+6) : le flush final passe
        clock["t"] += 4.0
        assert await _wait_until(lambda: broadcasts["n"] == 2)

        # Nouvel appel hors fenêtre (t+16) : diffusion immédiate
        clock["t"] += 10.0
        events.notify_stats_update()
        assert await _wait_until(lambda: broadcasts["n"] == 3)

        await asyncio.sleep(0.05)
        assert broadcasts["n"] == 3  # rien de plus


class TestTargetedEventsNotThrottled:
    @pytest.mark.asyncio
    async def test_broadcast_event_not_throttled(
        self, fake_clock_and_broadcast
    ):
        """Pendant une fenêtre throttle ouverte, `broadcast_event` (types non
        stats : users_updated, apps_updated...) reste diffusé immédiatement."""
        clock, broadcasts = fake_clock_and_broadcast

        events.notify_stats_update()  # ouvre la fenêtre throttle
        assert await _wait_until(lambda: broadcasts["n"] == 1)

        queue: asyncio.Queue = asyncio.Queue()
        events._connected_clients.add(queue)
        try:
            await events.broadcast_event("users_updated", {"foo": "bar"})
        finally:
            events._connected_clients.discard(queue)

        event = queue.get_nowait()
        assert event["type"] == "users_updated"
        assert event["data"] == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_send_to_user_not_throttled(self, fake_clock_and_broadcast):
        """Les événements ciblés par utilisateur (`send_to_user`, messages)
        ne passent JAMAIS par le throttle stats."""
        clock, broadcasts = fake_clock_and_broadcast

        events.notify_stats_update()  # ouvre la fenêtre throttle
        assert await _wait_until(lambda: broadcasts["n"] == 1)

        queue: asyncio.Queue = asyncio.Queue()
        events._user_connections[42] = {queue}
        try:
            await events.send_to_user(42, "message", {"id": 1})
        finally:
            events._user_connections.pop(42, None)

        event = queue.get_nowait()
        assert event["type"] == "message"
        assert event["data"] == {"id": 1}
