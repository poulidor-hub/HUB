# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests GET /api/messages/me enrichi (console notifications unifiée) :

- Réponse TOUJOURS paginée (PaginatedMyMessages : messages/total/page/page_size/pages/unread_total)
- Filtres : unread, priority, sender_app, search
- Exclusion des messages masqués (receipt.hidden_at) du résultat ET de unread_total
- group_similar : regroupement titre+priorité+app avec occurrences
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserMessage, MessageReadReceipt, utcnow
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


async def _set_receipt(session: AsyncSession, msg: UserMessage, user: User, **kw) -> MessageReadReceipt:
    """Insère et retourne un MessageReadReceipt pour (msg, user)."""
    r = MessageReadReceipt(message_id=msg.id, user_id=user.id, **kw)
    session.add(r)
    await session.commit()
    await session.refresh(r)
    return r


# -----------------------------------------------------------------------------
# Pagination — la réponse est TOUJOURS une enveloppe PaginatedMyMessages
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_me_pagination_envelope(client: AsyncClient, async_session: AsyncSession,
                                      admin_user, test_user, user_token):
    for i in range(3):
        await _make_broadcast(async_session, admin_user, title=f"pag-{i}")

    resp = await client.get("/api/messages/me", params={"page": 1, "page_size": 2},
                            headers=get_auth_header(user_token))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"messages", "total", "page", "page_size", "pages", "unread_total"}
    assert len(body["messages"]) == 2
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["pages"] == 2

    resp2 = await client.get("/api/messages/me", params={"page": 2, "page_size": 2},
                             headers=get_auth_header(user_token))
    body2 = resp2.json()
    assert len(body2["messages"]) == 1
    assert body2["total"] == 3
    assert body2["pages"] == 2


# -----------------------------------------------------------------------------
# Filtres : unread / priority / sender_app / search
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_me_unread_filter_returns_only_unread(client: AsyncClient, async_session: AsyncSession,
                                                    admin_user, test_user, user_token):
    m1 = await _make_broadcast(async_session, admin_user, title="unread-a")
    m2 = await _make_broadcast(async_session, admin_user, title="unread-b")
    await _set_receipt(async_session, m2, test_user, read_at=utcnow())

    resp = await client.get("/api/messages/me", params={"unread": "true"},
                            headers=get_auth_header(user_token))
    assert resp.status_code == 200
    body = resp.json()
    ids = {m["id"] for m in body["messages"]}
    assert ids == {m1.id}
    assert body["unread_total"] == 1


@pytest.mark.asyncio
async def test_me_priority_filter(client: AsyncClient, async_session: AsyncSession,
                                  admin_user, test_user, user_token):
    m_info = await _make_broadcast(async_session, admin_user, title="prio-info", priority="info")
    m_warn = await _make_broadcast(async_session, admin_user, title="prio-warn", priority="warning")

    resp = await client.get("/api/messages/me", params={"priority": "warning"},
                            headers=get_auth_header(user_token))
    assert resp.status_code == 200
    body = resp.json()
    ids = {m["id"] for m in body["messages"]}
    assert ids == {m_warn.id}
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_me_sender_app_filter(client: AsyncClient, async_session: AsyncSession,
                                    admin_user, test_user, user_token):
    m_app = await _make_broadcast(async_session, admin_user, title="from-app", app_slug="app-a")
    await _make_broadcast(async_session, admin_user, title="from-admin")

    resp = await client.get("/api/messages/me", params={"sender_app": "app-a"},
                            headers=get_auth_header(user_token))
    assert resp.status_code == 200
    body = resp.json()
    ids = {m["id"] for m in body["messages"]}
    assert ids == {m_app.id}
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_me_search_filter_title_and_body(client: AsyncClient, async_session: AsyncSession,
                                               admin_user, test_user, user_token):
    m_title = await _make_broadcast(async_session, admin_user, title="Maintenance planifiée")
    m_body = await _make_broadcast(async_session, admin_user, title="Info générale",
                                   body="Intervention maintenance disque")
    await _make_broadcast(async_session, admin_user, title="Sans rapport")

    resp = await client.get("/api/messages/me", params={"search": "maintenance"},
                            headers=get_auth_header(user_token))
    assert resp.status_code == 200
    body = resp.json()
    ids = {m["id"] for m in body["messages"]}
    assert ids == {m_title.id, m_body.id}
    assert body["total"] == 2


# -----------------------------------------------------------------------------
# Masqués (hidden_at) : exclus du résultat ET de unread_total
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_me_hidden_excluded_from_result_and_unread_total(client: AsyncClient,
                                                               async_session: AsyncSession,
                                                               admin_user, test_user, user_token):
    m_visible = await _make_broadcast(async_session, admin_user, title="visible")
    m_hidden = await _make_broadcast(async_session, admin_user, title="hidden")
    # hidden mais jamais lu : il NE doit PAS compter dans unread_total
    await _set_receipt(async_session, m_hidden, test_user, hidden_at=utcnow())

    resp = await client.get("/api/messages/me", headers=get_auth_header(user_token))
    assert resp.status_code == 200
    body = resp.json()
    ids = {m["id"] for m in body["messages"]}
    assert m_hidden.id not in ids
    assert m_visible.id in ids
    assert body["unread_total"] == 1


# -----------------------------------------------------------------------------
# group_similar : 1 ligne par (titre, priorité, app), occurrences = taille du groupe
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_me_group_similar_same_title_and_app(client: AsyncClient, async_session: AsyncSession,
                                                   admin_user, test_user, user_token):
    a1 = await _make_broadcast(async_session, admin_user, title="Alerte disque",
                               app_slug="app-a", priority="urgent")
    a2 = await _make_broadcast(async_session, admin_user, title="Alerte disque",
                               app_slug="app-a", priority="urgent")

    resp = await client.get("/api/messages/me", params={"group_similar": "true"},
                            headers=get_auth_header(user_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["messages"]) == 1
    assert body["messages"][0]["occurrences"] == 2
    assert body["messages"][0]["title"] == "Alerte disque"


@pytest.mark.asyncio
async def test_me_group_similar_same_title_diff_apps(client: AsyncClient, async_session: AsyncSession,
                                                     admin_user, test_user, user_token):
    a1 = await _make_broadcast(async_session, admin_user, title="Alerte commune", app_slug="app-a")
    a2 = await _make_broadcast(async_session, admin_user, title="Alerte commune", app_slug="app-b")

    resp = await client.get("/api/messages/me", params={"group_similar": "true"},
                            headers=get_auth_header(user_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    by_title_app = {(m["title"], m["sender_app_slug"]): m["occurrences"] for m in body["messages"]}
    assert by_title_app == {("Alerte commune", "app-a"): 1, ("Alerte commune", "app-b"): 1}
