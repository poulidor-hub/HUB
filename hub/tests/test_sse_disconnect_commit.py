# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""La fermeture de session sur déconnexion SSE doit être committée en base."""
import asyncio

import pytest

from app.models import UserSession
from app.routes.events import handle_user_disconnect
import app.database
from tests.test_usage_segments import _make_session


@pytest.mark.asyncio
async def test_sse_disconnect_persists_session_close(async_engine, async_session, test_user, monkeypatch):
    s = await _make_session(async_session, test_user)

    # Neutraliser le délai de reconnexion (5 s) pour le test
    async def _fast_sleep(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    # user sans connexions SSE restantes -> fermeture
    await handle_user_disconnect(test_user.id)

    # Vérifier depuis une NOUVELLE session DB (preuve du commit)
    # NB: accès attribut (et non `from app.database import`) pour résoudre
    # le maker AU MOMENT de l'appel — un import en tête de module capturerait
    # le maker PostgreSQL réel avant le patch de la fixture `_patch_session_maker`.
    async with app.database.async_session_maker() as fresh:
        result = await fresh.get(UserSession, s.id)
        assert result.logout_at is not None
        assert result.logout_reason == "sse_disconnect"
