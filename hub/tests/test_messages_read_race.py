# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""POST /api/messages/{id}/read et /read-all : tolérants aux courses (incident 18/11 03:04).

La propagation de lecture (groupe dédupliqué) et le read-all insèrent les
receipts manquants au commit du POST. Si une requête concurrente (auto-delivery
du GET /me d'un autre onglet, refresh, second POST /read sur une autre
occurrence du groupe) crée le même receipt entre le SELECT de
_apply_read_receipts et le commit, l'INSERT lève UniqueViolationError sur
uq_message_read_receipts_message_user → 500 sur un simple clic « lu »
(messages.py:1328, user vpoulidor, messages 25125+25230+25335+25440+25545,
18/11 à 03:04).

Le fix : même remédiation que GET /me (incident 10/11 02:56,
test_messages_me_delivery_race.py) — un IntegrityError au commit est absorbé
(rollback best-effort) et l'endpoint répond quand même « déjà lu » / « 0
marqué » : la requête concurrente a de toute façon committé la lecture.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import get_auth_header


async def _mk_broadcast(async_session, admin_user, title):
    from app.models import UserMessage

    msg = UserMessage(
        sender_type="admin",
        sender_id=admin_user.id,
        target_type="broadcast",
        priority="info",
        title=title,
        body="body",
        status="active",
        total_targets=1,
    )
    async_session.add(msg)
    await async_session.commit()
    return msg


def _first_commit_raises_unique(monkeypatch):
    """Le 1er commit lève l'UniqueViolation de la course, les suivants passent."""
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
    return real_commit


@pytest.mark.asyncio
async def test_mark_read_race_returns_200_not_500(
        client: AsyncClient, async_session: AsyncSession,
        admin_user, test_user, user_token, monkeypatch):
    """Le commit du POST /{id}/read perd la course (receipt créé par une
    requête concurrente) → la lecture doit répondre 200 « déjà lu », pas 500."""
    msg = await _mk_broadcast(async_session, admin_user, "race-mark-read")
    # ID capturé AVANT le POST : le rollback absorbé par la route expire les
    # objets de la session partagée (override get_db) — tout accès ORM après
    # coup en contexte sync lèverait MissingGreenlet.
    msg_id = msg.id
    real_commit = _first_commit_raises_unique(monkeypatch)

    resp = await client.post(f"/api/messages/{msg_id}/read",
                             headers=get_auth_header(user_token))
    assert resp.status_code == 200, (
        f"la lecture doit répondre 200 même si une requête concurrente a "
        f"committé le receipt (reçu : {resp.status_code}) — {resp.text[:200]}")
    body = resp.json()
    assert body["status"] == "already_read"
    assert body["marked_read"] == 0

    # Un second POST (commit sain) reste nominal : dans cette simulation la
    # course est factice (aucun receipt concurrent inséré), donc le message
    # est réellement marqué lu — le try/except ne dégrade pas le flux normal.
    monkeypatch.setattr(AsyncSession, "commit", real_commit)
    resp2 = await client.post(f"/api/messages/{msg_id}/read",
                              headers=get_auth_header(user_token))
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "read"
    assert resp2.json()["marked_read"] == 1


@pytest.mark.asyncio
async def test_read_all_race_returns_200_not_500(
        client: AsyncClient, async_session: AsyncSession,
        admin_user, test_user, user_token, monkeypatch):
    """Le commit du POST /read-all perd la course → read-all répond 200 avec
    0 message marqué, pas 500."""
    await _mk_broadcast(async_session, admin_user, "race-read-all")
    real_commit = _first_commit_raises_unique(monkeypatch)

    resp = await client.post("/api/messages/read-all",
                             headers=get_auth_header(user_token))
    assert resp.status_code == 200, (
        f"read-all doit répondre 200 même si une requête concurrente a "
        f"committé un receipt (reçu : {resp.status_code}) — {resp.text[:200]}")
    assert resp.json()["marked_read"] == 0

    # Un read-all suivant (commit sain) marque normalement le message.
    monkeypatch.setattr(AsyncSession, "commit", real_commit)
    resp2 = await client.post("/api/messages/read-all",
                              headers=get_auth_header(user_token))
    assert resp2.status_code == 200
    assert resp2.json()["marked_read"] >= 1
