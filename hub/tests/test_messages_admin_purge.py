# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests admin — console notifications unifiée (Task 4) :

- GET /api/messages?group_similar=true : regroupement (titre+priorité+app) avec occurrences
- GET /api/messages/purge-preview : comptes par cas de purge (lus / expirés / vieux / par app)
- DELETE /api/messages/ : read_only, expired_only, exclude_urgent_unread (combinables)
"""
import pytest
from datetime import timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserMessage, utcnow
from .conftest import get_auth_header


# -----------------------------------------------------------------------------
# Helpers locaux
# -----------------------------------------------------------------------------

async def _make_broadcast(session: AsyncSession, user: User, title: str,
                          app_slug: str = None, **kw) -> UserMessage:
    """Insère un UserMessage broadcast ; read_count/total_targets/expires_at/created_at via kw."""
    msg = UserMessage(
        sender_type="app" if app_slug else "admin",
        sender_id=user.id,
        sender_app_slug=app_slug,
        target_type="broadcast",
        priority=kw.pop("priority", "info"),
        title=title,
        body=kw.pop("body", "body"),
        status=kw.pop("status", "active"),
        total_targets=kw.pop("total_targets", 1),
        **kw,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


# -----------------------------------------------------------------------------
# GET /api/messages?group_similar=true
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_group_similar_admin(client: AsyncClient, async_session: AsyncSession,
                                   admin_user, admin_token):
    # 2 broadcasts même titre + même source (pas d'app → coalesce = sender_type "admin")
    a1 = await _make_broadcast(async_session, admin_user, title="Alerte disque")
    a2 = await _make_broadcast(async_session, admin_user, title="Alerte disque")
    # Même titre mais sender_app_slug différents → 2 groupes distincts
    b1 = await _make_broadcast(async_session, admin_user, title="Alerte commune", app_slug="app-a")
    b2 = await _make_broadcast(async_session, admin_user, title="Alerte commune", app_slug="app-b")

    resp = await client.get("/api/messages/", params={"group_similar": "true"},
                            headers=get_auth_header(admin_token))
    assert resp.status_code == 200
    body = resp.json()

    # 1 ligne pour "Alerte disque" (occurrences=2) + 2 lignes pour "Alerte commune"
    assert body["total"] == 3
    assert len(body["messages"]) == 3

    by_key = {(m["title"], m["sender_app_slug"]): m["occurrences"] for m in body["messages"]}
    assert by_key[("Alerte disque", None)] == 2
    assert by_key[("Alerte commune", "app-a")] == 1
    assert by_key[("Alerte commune", "app-b")] == 1

    # Le représentant du groupe "Alerte disque" est bien un des 2 messages
    rep_ids = {m["id"] for m in body["messages"]}
    assert rep_ids <= {a1.id, a2.id, b1.id, b2.id}


# -----------------------------------------------------------------------------
# GET /api/messages/purge-preview
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_preview_counts(client: AsyncClient, async_session: AsyncSession,
                                    admin_user, admin_token):
    # 1 msg lu par tous (total_targets=1, read_count=1)
    await _make_broadcast(async_session, admin_user, title="lu-par-tous",
                          read_count=1)
    # 1 expiré (expires_at passé, actif)
    await _make_broadcast(async_session, admin_user, title="expire",
                          expires_at=utcnow() - timedelta(hours=1))
    # 1 vieux de 100 jours (actif, jamais expiré)
    await _make_broadcast(async_session, admin_user, title="vieux",
                          created_at=utcnow() - timedelta(days=100))
    # 1 app X (ni lu, ni expiré, ni vieux)
    await _make_broadcast(async_session, admin_user, title="de-app-x",
                          app_slug="app-x", read_count=0)

    resp = await client.get("/api/messages/purge-preview", params={"older_than_days": 90},
                            headers=get_auth_header(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["read"] == 1
    assert body["expired"] == 1
    assert body["older_than_days"] == {"days": 90, "count": 1}
    assert {"sender_app_slug": "app-x", "count": 1} in body["per_app"]


# -----------------------------------------------------------------------------
# DELETE /api/messages/?read_only=true
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_read_only(client: AsyncClient, async_session: AsyncSession,
                               admin_user, admin_token):
    m_read = await _make_broadcast(async_session, admin_user, title="purge-lu", read_count=1)
    m_other = await _make_broadcast(async_session, admin_user, title="purge-non-lu", read_count=0)

    resp = await client.delete("/api/messages/", params={"read_only": "true"},
                               headers=get_auth_header(admin_token))
    assert resp.status_code == 200
    assert resp.json()["affected"] == 1

    await async_session.refresh(m_read)
    await async_session.refresh(m_other)
    assert m_read.status == "archived"
    assert m_other.status == "active"


# -----------------------------------------------------------------------------
# DELETE /api/messages/?expired_only=true&exclude_urgent_unread=true
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_expired_excludes_urgent_unread(client: AsyncClient, async_session: AsyncSession,
                                                    admin_user, admin_token):
    m_urgent = await _make_broadcast(async_session, admin_user, title="urgent-non-lu",
                                     priority="urgent", read_count=0,
                                     expires_at=utcnow() - timedelta(hours=1))
    m_info = await _make_broadcast(async_session, admin_user, title="info-expiree",
                                   priority="info", read_count=0,
                                   expires_at=utcnow() - timedelta(hours=1))

    resp = await client.delete("/api/messages/",
                               params={"expired_only": "true", "exclude_urgent_unread": "true"},
                               headers=get_auth_header(admin_token))
    assert resp.status_code == 200
    assert resp.json()["affected"] == 1

    await async_session.refresh(m_urgent)
    await async_session.refresh(m_info)
    # L'urgent jamais lu survit, l'expiré info est archivé
    assert m_urgent.status == "active"
    assert m_info.status == "archived"

# -----------------------------------------------------------------------------
# Revue finale — fixes Critical #1 + Important #4 :
#   - DELETE /api/messages/?older_than_days=N épargne les récents (l'ancien
#     param `hours` est une fenêtre RÉCENTE, l'option de purge doit viser le VIEUX)
#   - DELETE /api/messages/?status=active épargne les archivés (alignement
#     modal ↔ purge-preview, qui compte sur les actifs uniquement)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_older_than_days_spares_recent(client: AsyncClient, async_session: AsyncSession,
                                                   admin_user, admin_token):
    vieux = await _make_broadcast(async_session, admin_user, title="vieux-100j",
                                  created_at=utcnow() - timedelta(days=100))
    recent = await _make_broadcast(async_session, admin_user, title="recent-1j",
                                   created_at=utcnow() - timedelta(days=1))

    resp = await client.delete("/api/messages/",
                               params={"older_than_days": "90"},
                               headers=get_auth_header(admin_token))
    assert resp.status_code == 200
    assert resp.json()["affected"] == 1

    await async_session.refresh(vieux)
    await async_session.refresh(recent)
    assert vieux.status == "archived"
    assert recent.status == "active", "le param older_than_days ne doit PAS toucher les récents"


@pytest.mark.asyncio
async def test_purge_status_active_spares_archived(client: AsyncClient, async_session: AsyncSession,
                                                   admin_user, admin_token):
    actif = await _make_broadcast(async_session, admin_user, title="actif-app", app_slug="app-x")
    archive = await _make_broadcast(async_session, admin_user, title="archive-app", app_slug="app-x",
                                    status="archived")

    # Cas qui compte : hard + status=active → l'archivé doit SURVIVRE à la
    # suppression définitive (alignement modal ↔ purge-preview, compte actifs)
    resp = await client.delete("/api/messages/",
                               params={"sender_app": "app-x", "status": "active", "hard": "true"},
                               headers=get_auth_header(admin_token))
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1

    # L'actif a été supprimé en dur, l'archivé survit
    assert (await async_session.get(UserMessage, actif.id)) is None
    assert (await async_session.get(UserMessage, archive.id)) is not None, \
        "status=active doit épargner les archivés même en hard"

    # Sans le param : sémantique historique conservée (tous statuts)
    autre_actif = await _make_broadcast(async_session, admin_user, title="actif-app-2", app_slug="app-y")
    resp2 = await client.delete("/api/messages/",
                                params={"sender_app": "app-y", "hard": "true"},
                                headers=get_auth_header(admin_token))
    assert resp2.status_code == 200
    assert (await async_session.get(UserMessage, autre_actif.id)) is None

@pytest.mark.asyncio
async def test_purge_preview_exclude_urgent_unread(client: AsyncClient, async_session: AsyncSession,
                                                   admin_user, admin_token):
    """Le preview applique le même garde que la purge : urgent jamais lu exclu des comptes."""
    await _make_broadcast(async_session, admin_user, title="vieux-urgent-non-lu",
                          priority="urgent", read_count=0,
                          created_at=utcnow() - timedelta(days=100))

    resp_all = await client.get("/api/messages/purge-preview",
                                params={"older_than_days": "90"}, headers=get_auth_header(admin_token))
    assert resp_all.status_code == 200
    assert resp_all.json()["older_than_days"]["count"] == 1, "sans garde, l'urgent compte"

    resp_guarded = await client.get("/api/messages/purge-preview",
                                    params={"older_than_days": "90", "exclude_urgent_unread": "true"},
                                    headers=get_auth_header(admin_token))
    assert resp_guarded.status_code == 200
    assert resp_guarded.json()["older_than_days"]["count"] == 0, "avec garde, l'urgent ne compte pas"
