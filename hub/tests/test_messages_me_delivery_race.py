# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""GET /api/messages/me : livraison auto tolérante aux courses (incident 10/11 02:56).

L'auto-delivery de l'inbox insère les receipts manquants au commit du GET.
Si une requête concurrente (double onglet, refresh, POST /read simultané)
crée le même receipt entre le SELECT de page et le commit, l'INSERT en
masse lève UniqueViolationError sur uq_message_read_receipts_message_user
→ 500 sur un simple GET d'inbox (messages.py:914, 1 occurrence le
10/11 à 02:56, user vpoulidor, messages 917+922).

Le fix : la livraison est best-effort — un IntegrityError au commit est
absorbé (rollback) et l'inbox répond quand même.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import get_auth_header


@pytest.mark.asyncio
async def test_me_delivery_race_returns_200_not_500(
        client: AsyncClient, async_session: AsyncSession,
        admin_user, test_user, user_token, monkeypatch):
    """Le premier commit du GET /me lève IntegrityError (receipt créé par
    une requête concurrente) → l'inbox doit répondre 200 quand même."""
    from app.models import UserMessage

    msg = UserMessage(
        sender_type="admin",
        sender_id=admin_user.id,
        target_type="broadcast",
        priority="info",
        title="race-delivery",
        body="body",
        status="active",
        total_targets=1,
    )
    async_session.add(msg)
    await async_session.commit()

    real_commit = AsyncSession.commit
    state = {"commits": 0}

    async def flaky_commit(self):
        state["commits"] += 1
        if state["commits"] == 1:
            raise IntegrityError(
                "INSERT INTO message_read_receipts ...", {},
                Exception('duplicate key value violates unique constraint '
                          '"uq_message_read_receipts_message_user"'))
        return await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)

    resp = await client.get("/api/messages/me",
                            headers=get_auth_header(user_token))
    assert resp.status_code == 200, (
        f"l'inbox doit répondre 200 même si la livraison auto perd une "
        f"course (reçu : {resp.status_code}) — {resp.text[:200]}")
    body = resp.json()
    assert body["total"] >= 1
    assert any(m["title"] == "race-delivery" for m in body["messages"])

    # Un GET suivant (commit sain) reste nominal.
    monkeypatch.setattr(AsyncSession, "commit", real_commit)
    resp2 = await client.get("/api/messages/me",
                             headers=get_auth_header(user_token))
    assert resp2.status_code == 200
