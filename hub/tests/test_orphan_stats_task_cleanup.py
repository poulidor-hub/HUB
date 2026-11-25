# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""La tâche différée du throttle des stats SSE ne survit pas à un test.

La boucle d'événements de conftest est de session : laissée vivante, cette
tâche ouvre plus tard une session sur la connexion SQLite en mémoire du test
suivant et annule sa transaction en vol. Une fixture autouse de conftest
l'annule après chaque test ; ces deux tests, lus dans l'ordre, l'épinglent.
"""

from app.routes import events


async def test_orphan_stats_task_armed():
    """Amorce : une diffusion dans la fenêtre du throttle arme la tâche différée."""
    events._last_stats_broadcast = events._now()
    events._stats_pending = False
    events._stats_flush_task = None
    await events.broadcast_stats_update()
    assert events._stats_flush_task is not None
    assert not events._stats_flush_task.done()
    assert events._stats_pending is True


async def test_orphan_stats_task_is_gone_before_next_test():
    """Le test précédent a armé une tâche : la fixture de nettoyage l'a annulée."""
    assert events._stats_flush_task is None
    assert events._stats_pending is False


async def test_stats_broadcast_is_immediate_in_a_fresh_test(monkeypatch):
    """Le test d'amorce a ouvert la fenêtre du limiteur : le test suivant doit
    néanmoins diffuser immédiatement (fenêtre refermée entre deux tests)."""
    diffusés = []

    async def fake_diffusion():
        diffusés.append(True)

    monkeypatch.setattr(events, "_do_broadcast_stats", fake_diffusion)
    await events.broadcast_stats_update()
    assert diffusés == [True]
    assert events._stats_flush_task is None
