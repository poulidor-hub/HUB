# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests suppression côté destinataire (console notifications unifiée).

Task 1 : round-trip de la colonne `hidden_at` sur MessageReadReceipt.
Task 3 : endpoints user — suppression réelle côté serveur
  (DELETE /me/{id}, POST /me/delete-batch, POST /me/purge-read).
"""
import pytest
from datetime import datetime, timezone
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, MessageReadReceipt, UserMessage, utcnow
from .conftest import get_auth_header


# -----------------------------------------------------------------------------
# Helpers locaux
# -----------------------------------------------------------------------------

async def _make_broadcast(session: AsyncSession, user: User, title: str,
                          app_slug: str = None, **kw) -> UserMessage:
    """Insère un UserMessage broadcast (visible de tous les utilisateurs)."""
    msg = UserMessage(
        sender_type="app" if app_slug else "admin",
        sender_id=user.id,
        sender_app_slug=app_slug,
        target_type="broadcast",
        priority=kw.pop("priority", "info"),
        title=title,
        body=kw.pop("body", "body"),
        status=kw.pop("status", "active"),
        total_targets=1,
        **kw,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


# -----------------------------------------------------------------------------
# Task 1 — round-trip hidden_at
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_receipt_hidden_at_roundtrip(async_session, admin_user):
    msg = UserMessage(sender_type="admin", target_type="user", target_user_id=admin_user.id,
                      priority="info", title="Hidden roundtrip")
    async_session.add(msg)
    await async_session.flush()
    r = MessageReadReceipt(message_id=msg.id, user_id=admin_user.id,
                           hidden_at=datetime.now(timezone.utc))
    async_session.add(r)
    await async_session.commit()
    await async_session.refresh(r)
    assert r.hidden_at is not None


# -----------------------------------------------------------------------------
# Task 3 — DELETE /api/messages/me/{message_id}
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_broadcast_is_per_user(client: AsyncClient, async_session: AsyncSession,
                                            admin_user, test_user, user_token, admin_token):
    """Un user masque un broadcast : lui ne le voit plus, l'admin le voit toujours."""
    msg = await _make_broadcast(async_session, admin_user, title="broadcast-a-cacher")

    # 1er appel : le user masque le broadcast
    resp = await client.delete(f"/api/messages/me/{msg.id}", headers=get_auth_header(user_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "hidden"
    assert body["message_id"] == msg.id

    # Le user ne le voit plus dans /me
    resp_user = await client.get("/api/messages/me", headers=get_auth_header(user_token))
    assert resp_user.status_code == 200
    user_ids = {m["id"] for m in resp_user.json()["messages"]}
    assert msg.id not in user_ids

    # L'admin (autre destinataire du broadcast) le voit ENCORE
    resp_admin = await client.get("/api/messages/me", headers=get_auth_header(admin_token))
    assert resp_admin.status_code == 200
    admin_ids = {m["id"] for m in resp_admin.json()["messages"]}
    assert msg.id in admin_ids

    # Idempotent : 2e appel → already_hidden
    resp2 = await client.delete(f"/api/messages/me/{msg.id}", headers=get_auth_header(user_token))
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "already_hidden"


@pytest.mark.asyncio
async def test_delete_unvisible_message_404(client: AsyncClient, async_session: AsyncSession,
                                            admin_user, test_user, user_token):
    """Message ciblé sur l'admin uniquement : le user ne peut pas le masquer → 404."""
    msg = UserMessage(
        sender_type="admin", sender_id=admin_user.id,
        target_type="user", target_user_id=admin_user.id,
        priority="info", title="prive-admin",
    )
    async_session.add(msg)
    await async_session.commit()
    await async_session.refresh(msg)

    resp = await client.delete(f"/api/messages/me/{msg.id}", headers=get_auth_header(user_token))
    assert resp.status_code == 404


# -----------------------------------------------------------------------------
# Task 3 — POST /api/messages/me/delete-batch
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_batch(client: AsyncClient, async_session: AsyncSession,
                            admin_user, test_user, user_token):
    m1 = await _make_broadcast(async_session, admin_user, title="batch-1")
    m2 = await _make_broadcast(async_session, admin_user, title="batch-2")
    m3 = await _make_broadcast(async_session, admin_user, title="batch-3")

    # 3 ids → hidden == 3
    resp = await client.post("/api/messages/me/delete-batch",
                             json={"ids": [m1.id, m2.id, m3.id]},
                             headers=get_auth_header(user_token))
    assert resp.status_code == 200
    assert resp.json() == {"hidden": 3}

    # Le user ne voit plus aucun des 3
    resp_me = await client.get("/api/messages/me", headers=get_auth_header(user_token))
    ids = {m["id"] for m in resp_me.json()["messages"]}
    assert ids.isdisjoint({m1.id, m2.id, m3.id})

    # > 500 ids → 400
    too_many = {"ids": list(range(501))}
    resp_many = await client.post("/api/messages/me/delete-batch", json=too_many,
                                  headers=get_auth_header(user_token))
    assert resp_many.status_code == 400

    # ids non-liste → 400
    resp_not_list = await client.post("/api/messages/me/delete-batch", json={"ids": "abc"},
                                      headers=get_auth_header(user_token))
    assert resp_not_list.status_code == 400


# -----------------------------------------------------------------------------
# Task 3 — POST /api/messages/me/purge-read
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_read(client: AsyncClient, async_session: AsyncSession,
                          admin_user, test_user, user_token):
    m_read = await _make_broadcast(async_session, admin_user, title="deja-lu")
    m_unread = await _make_broadcast(async_session, admin_user, title="pas-lu")
    # m_read : lu par le user
    async_session.add(MessageReadReceipt(message_id=m_read.id, user_id=test_user.id,
                                         read_at=utcnow()))
    await async_session.commit()

    # unread_total avant purge : seul m_unread compte
    resp_before = await client.get("/api/messages/me", headers=get_auth_header(user_token))
    assert resp_before.json()["unread_total"] == 1

    resp = await client.post("/api/messages/me/purge-read", headers=get_auth_header(user_token))
    assert resp.status_code == 200
    assert resp.json() == {"hidden": 1}

    # /me ne liste que le non-lu
    resp_after = await client.get("/api/messages/me", headers=get_auth_header(user_token))
    body = resp_after.json()
    ids = {m["id"] for m in body["messages"]}
    assert ids == {m_unread.id}
    assert body["total"] == 1

    # unread_total inchangé (la purge ne touche que les lus)
    assert body["unread_total"] == 1
