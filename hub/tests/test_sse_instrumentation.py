# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Instrumentation SSE : cause de sortie du générateur toujours journalisée."""
import asyncio
import logging

import pytest

from app.routes import events


class FakeRequest:
    """Duck-typing minimal : event_generator n'appelle que is_disconnected()."""

    def __init__(self, disconnect: bool = True, raise_error: Exception | None = None):
        self._disconnect = disconnect
        self._raise_error = raise_error

    async def is_disconnected(self) -> bool:
        if self._raise_error is not None:
            raise self._raise_error
        return self._disconnect


async def _drain(gen):
    """Consomme le générateur jusqu'à StopAsyncIteration (le finally doit passer).

    NB : `async for` avale StopAsyncIteration par protocole — on utilise
    __anext__ explicite pour pouvoir l'observer."""
    first = await gen.__anext__()
    assert "connected" in first
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


@pytest.mark.asyncio
async def test_generator_logs_exit_cause_client_disconnect(caplog):
    caplog.set_level(logging.INFO, logger="app.routes.events")
    gen = events.event_generator(FakeRequest(disconnect=True), asyncio.Queue())
    await _drain(gen)
    assert any("cause=client_disconnect" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_generator_logs_error_cause_without_raising(caplog):
    caplog.set_level(logging.INFO, logger="app.routes.events")
    gen = events.event_generator(
        FakeRequest(raise_error=RuntimeError("socket hs")), asyncio.Queue(), user_id=None
    )
    await _drain(gen)  # ne doit pas propager l'exception
    assert any("cause=error" in r.message for r in caplog.records)
    assert any("SSE erreur générateur" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_generator_logs_duration(caplog):
    caplog.set_level(logging.INFO, logger="app.routes.events")
    gen = events.event_generator(FakeRequest(disconnect=True), asyncio.Queue())
    await _drain(gen)
    assert any("durée=" in r.message for r in caplog.records)
