# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests pour la refonte du Notification Center :
- Cycle de vie : active / archived / expired
- Engagement : delivered / read / clicked
- Rétro-compatibilité M2M (sync, receipts, /me)
- Soft-delete admin (archivage par défaut, hard delete optionnel)
"""
import pytest
from datetime import timedelta
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserMessage, MessageReadReceipt, ServiceAccount
from app.routes.messages import utcnow
from .conftest import get_auth_header


# -----------------------------------------------------------------------------
# Helpers locaux
# -----------------------------------------------------------------------------

async def _get_sa_token(client: AsyncClient, service_account: ServiceAccount) -> str:
    response = await client.post(
        "/api/auth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": service_account.client_id,
            "client_secret": "secret123",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def _make_broadcast(session: AsyncSession, admin: User, *, title="Hello", priority="info",
                          expires_in_hours=None, status="active", total_targets=None) -> UserMessage:
    if total_targets is None:
        from sqlalchemy import func
        total_targets = await session.scalar(select(func.count(User.id))) or 0
    expires_at = None
    if expires_in_hours is not None:
        expires_at = utcnow() + timedelta(hours=expires_in_hours)
    msg = UserMessage(
        sender_type="admin",
        sender_id=admin.id,
        target_type="broadcast",
        priority=priority,
        title=title,
        body="body",
        status=status,
        total_targets=total_targets,
        expires_at=expires_at,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def _make_user_message(session: AsyncSession, admin: User, target: User, **kw) -> UserMessage:
    msg = UserMessage(
        sender_type="admin",
        sender_id=admin.id,
        target_type="user",
        target_user_id=target.id,
        target_username=target.username,
        priority=kw.get("priority", "info"),
        title=kw.get("title", "Hi"),
        body=kw.get("body", "body"),
        status=kw.get("status", "active"),
        total_targets=1,
        expires_at=kw.get("expires_at"),
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


# =============================================================================
# CYCLE DE VIE — archive / restore / hard-delete
# =============================================================================

class TestMessageLifecycle:

    @pytest.mark.asyncio
    async def test_archive_sets_status_and_archived_at(self, client, async_session, admin_user, admin_token):
        msg = await _make_broadcast(async_session, admin_user, title="To archive")

        resp = await client.post(f"/api/messages/{msg.id}/archive", headers=get_auth_header(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "archived"
        assert body["message_id"] == msg.id

        await async_session.refresh(msg)
        assert msg.status == "archived"
        assert msg.archived_at is not None

    @pytest.mark.asyncio
    async def test_archive_idempotent(self, client, async_session, admin_user, admin_token):
        msg = await _make_broadcast(async_session, admin_user, status="archived")
        msg.archived_at = utcnow()
        await async_session.commit()

        resp = await client.post(f"/api/messages/{msg.id}/archive", headers=get_auth_header(admin_token))
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_archived"

    @pytest.mark.asyncio
    async def test_restore_brings_back_to_active(self, client, async_session, admin_user, admin_token):
        msg = await _make_broadcast(async_session, admin_user, status="archived")
        msg.archived_at = utcnow()
        await async_session.commit()

        resp = await client.post(f"/api/messages/{msg.id}/restore", headers=get_auth_header(admin_token))
        assert resp.status_code == 200
        assert resp.json()["status"] == "restored"

        await async_session.refresh(msg)
        assert msg.status == "active"
        assert msg.archived_at is None

    @pytest.mark.asyncio
    async def test_restore_no_op_if_already_active(self, client, async_session, admin_user, admin_token):
        msg = await _make_broadcast(async_session, admin_user)
        resp = await client.post(f"/api/messages/{msg.id}/restore", headers=get_auth_header(admin_token))
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_archived"

    @pytest.mark.asyncio
    async def test_hard_delete_removes_message(self, client, async_session, admin_user, admin_token):
        msg = await _make_broadcast(async_session, admin_user, title="Doomed")
        mid = msg.id

        resp = await client.delete(f"/api/messages/{mid}/hard", headers=get_auth_header(admin_token))
        assert resp.status_code == 200
        assert resp.json()["status"] == "hard_deleted"

        gone = await async_session.get(UserMessage, mid)
        assert gone is None

    @pytest.mark.asyncio
    async def test_lifecycle_endpoints_require_admin(self, client, async_session, admin_user, user_token):
        msg = await _make_broadcast(async_session, admin_user)
        h = get_auth_header(user_token)
        assert (await client.post(f"/api/messages/{msg.id}/archive", headers=h)).status_code == 403
        assert (await client.post(f"/api/messages/{msg.id}/restore", headers=h)).status_code == 403
        assert (await client.delete(f"/api/messages/{msg.id}/hard", headers=h)).status_code == 403


# =============================================================================
# DELETE / BATCH — soft-archive par défaut, hard avec ?hard=true
# =============================================================================

class TestSoftDeleteDefault:

    @pytest.mark.asyncio
    async def test_delete_filtered_archives_by_default(self, client, async_session, admin_user, admin_token):
        m1 = await _make_broadcast(async_session, admin_user, title="A", priority="info")
        m2 = await _make_broadcast(async_session, admin_user, title="B", priority="info")

        resp = await client.delete("/api/messages/?priority=info", headers=get_auth_header(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["hard"] is False
        assert body["affected"] >= 2

        await async_session.refresh(m1)
        await async_session.refresh(m2)
        assert m1.status == "archived"
        assert m2.status == "archived"
        # Doit toujours exister en DB
        assert await async_session.get(UserMessage, m1.id) is not None

    @pytest.mark.asyncio
    async def test_delete_filtered_hard_purges(self, client, async_session, admin_user, admin_token):
        m1 = await _make_broadcast(async_session, admin_user, title="X", priority="warning")
        mid = m1.id

        resp = await client.delete(
            "/api/messages/?priority=warning&hard=true",
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["hard"] is True
        assert await async_session.get(UserMessage, mid) is None

    @pytest.mark.asyncio
    async def test_batch_archive_default(self, client, async_session, admin_user, admin_token):
        m1 = await _make_broadcast(async_session, admin_user, title="b1")
        m2 = await _make_broadcast(async_session, admin_user, title="b2")

        resp = await client.request(
            "DELETE",
            "/api/messages/batch",
            headers=get_auth_header(admin_token),
            json={"ids": [m1.id, m2.id]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["hard"] is False
        assert body["affected"] == 2
        await async_session.refresh(m1)
        await async_session.refresh(m2)
        assert m1.status == "archived"
        assert m2.status == "archived"

    @pytest.mark.asyncio
    async def test_batch_hard_delete(self, client, async_session, admin_user, admin_token):
        m1 = await _make_broadcast(async_session, admin_user, title="h1")
        mid = m1.id
        resp = await client.request(
            "DELETE",
            "/api/messages/batch?hard=true",
            headers=get_auth_header(admin_token),
            json={"ids": [mid]},
        )
        assert resp.status_code == 200
        assert resp.json()["hard"] is True
        assert await async_session.get(UserMessage, mid) is None


# =============================================================================
# ENGAGEMENT — delivered / read / clicked + compteurs agrégés
# =============================================================================

class TestEngagementTracking:

    @pytest.mark.asyncio
    async def test_mark_delivered_creates_receipt(self, client, async_session, admin_user, test_user, user_token):
        msg = await _make_user_message(async_session, admin_user, test_user, title="ping")

        resp = await client.post(f"/api/messages/{msg.id}/delivered", headers=get_auth_header(user_token))
        assert resp.status_code == 200
        assert resp.json()["status"] == "delivered"

        await async_session.refresh(msg)
        assert msg.delivered_count == 1
        assert msg.read_count == 0

        rc = await async_session.scalar(
            select(MessageReadReceipt).where(
                MessageReadReceipt.message_id == msg.id,
                MessageReadReceipt.user_id == test_user.id,
            )
        )
        assert rc is not None
        assert rc.delivered_at is not None
        assert rc.read_at is None

    @pytest.mark.asyncio
    async def test_mark_delivered_idempotent(self, client, async_session, admin_user, test_user, user_token):
        msg = await _make_user_message(async_session, admin_user, test_user)
        await client.post(f"/api/messages/{msg.id}/delivered", headers=get_auth_header(user_token))
        resp = await client.post(f"/api/messages/{msg.id}/delivered", headers=get_auth_header(user_token))
        assert resp.json()["status"] == "already_delivered"

        await async_session.refresh(msg)
        assert msg.delivered_count == 1

    @pytest.mark.asyncio
    async def test_mark_read_promotes_existing_delivered_receipt(self, client, async_session, admin_user, test_user, user_token):
        msg = await _make_user_message(async_session, admin_user, test_user)
        # delivered d'abord
        await client.post(f"/api/messages/{msg.id}/delivered", headers=get_auth_header(user_token))
        await async_session.refresh(msg)
        assert msg.delivered_count == 1
        assert msg.read_count == 0

        # puis read
        resp = await client.post(f"/api/messages/{msg.id}/read", headers=get_auth_header(user_token))
        assert resp.status_code == 200
        assert resp.json()["status"] == "read"

        await async_session.refresh(msg)
        # delivered_count NE DOIT PAS être incrémenté à nouveau
        assert msg.delivered_count == 1
        assert msg.read_count == 1

        rc = await async_session.scalar(
            select(MessageReadReceipt).where(
                MessageReadReceipt.message_id == msg.id,
                MessageReadReceipt.user_id == test_user.id,
            )
        )
        assert rc.delivered_at is not None
        assert rc.read_at is not None

    @pytest.mark.asyncio
    async def test_mark_read_creates_full_receipt_if_none(self, client, async_session, admin_user, test_user, user_token):
        msg = await _make_user_message(async_session, admin_user, test_user)
        resp = await client.post(f"/api/messages/{msg.id}/read", headers=get_auth_header(user_token))
        assert resp.status_code == 200

        await async_session.refresh(msg)
        # Un receipt fraichement créé via read marque AUSSI delivered
        assert msg.delivered_count == 1
        assert msg.read_count == 1

    @pytest.mark.asyncio
    async def test_mark_clicked_increments_all_counters(self, client, async_session, admin_user, test_user, user_token):
        msg = await _make_user_message(async_session, admin_user, test_user)
        resp = await client.post(f"/api/messages/{msg.id}/click", headers=get_auth_header(user_token))
        assert resp.status_code == 200
        assert resp.json()["status"] == "clicked"

        await async_session.refresh(msg)
        assert msg.delivered_count == 1
        assert msg.read_count == 1
        assert msg.click_count == 1

    @pytest.mark.asyncio
    async def test_mark_clicked_idempotent(self, client, async_session, admin_user, test_user, user_token):
        msg = await _make_user_message(async_session, admin_user, test_user)
        await client.post(f"/api/messages/{msg.id}/click", headers=get_auth_header(user_token))
        resp = await client.post(f"/api/messages/{msg.id}/click", headers=get_auth_header(user_token))
        assert resp.json()["status"] == "already_clicked"
        await async_session.refresh(msg)
        assert msg.click_count == 1

    @pytest.mark.asyncio
    async def test_engagement_blocks_wrong_target(self, client, async_session, admin_user, test_user, user_token):
        # Message ciblé à l'admin (pas test_user)
        msg = await _make_user_message(async_session, admin_user, admin_user)
        for path in ("delivered", "read", "click"):
            resp = await client.post(f"/api/messages/{msg.id}/{path}", headers=get_auth_header(user_token))
            assert resp.status_code == 403, f"{path} should be forbidden"


# =============================================================================
# GET /me — auto-création du receipt delivered
# =============================================================================

class TestAutoDeliveryOnMe:

    @pytest.mark.asyncio
    async def test_me_creates_delivered_receipts_silently(self, client, async_session, admin_user, test_user, user_token):
        # 1 message broadcast + 1 message ciblé
        b = await _make_broadcast(async_session, admin_user, title="bcast")
        u = await _make_user_message(async_session, admin_user, test_user, title="targeted")

        resp = await client.get("/api/messages/me", headers=get_auth_header(user_token))
        assert resp.status_code == 200
        items = resp.json()["messages"]  # /me est désormais toujours paginé (enveloppe)
        assert len(items) >= 2
        # is_read doit rester False (delivered != read)
        ids = {m["id"]: m for m in items}
        assert ids[b.id]["is_read"] is False
        assert ids[u.id]["is_read"] is False

        await async_session.refresh(b)
        await async_session.refresh(u)
        # delivered_count est incrémenté
        assert b.delivered_count >= 1
        assert u.delivered_count == 1
        # mais read_count reste à 0
        assert b.read_count == 0
        assert u.read_count == 0


# =============================================================================
# RÉTRO-COMPATIBILITÉ — endpoints existants doivent garder leur contrat
# =============================================================================

class TestBackwardCompatibility:

    @pytest.mark.asyncio
    async def test_me_response_has_legacy_fields(self, client, async_session, admin_user, test_user, user_token):
        await _make_broadcast(async_session, admin_user, title="legacy")
        resp = await client.get("/api/messages/me", headers=get_auth_header(user_token))
        assert resp.status_code == 200
        items = resp.json()["messages"]  # /me est désormais toujours paginé (enveloppe)
        assert len(items) >= 1
        m = items[0]
        # Champs historiquement attendus par les apps clientes
        for key in ("id", "title", "priority", "sender_type", "target_type",
                    "created_at", "is_read", "body"):
            assert key in m, f"missing legacy field: {key}"

    @pytest.mark.asyncio
    async def test_unread_count_endpoint_still_works(self, client, async_session, admin_user, test_user, user_token):
        await _make_user_message(async_session, admin_user, test_user)
        resp = await client.get("/api/messages/me/unread-count", headers=get_auth_header(user_token))
        assert resp.status_code == 200
        body = resp.json()
        assert "count" in body
        assert body["count"] >= 1

    @pytest.mark.asyncio
    async def test_read_all_marks_unread_only(self, client, async_session, admin_user, test_user, user_token):
        m1 = await _make_user_message(async_session, admin_user, test_user, title="m1")
        m2 = await _make_user_message(async_session, admin_user, test_user, title="m2")
        resp = await client.post("/api/messages/read-all", headers=get_auth_header(user_token))
        assert resp.status_code == 200

        await async_session.refresh(m1)
        await async_session.refresh(m2)
        assert m1.read_count == 1
        assert m2.read_count == 1

    @pytest.mark.asyncio
    async def test_m2m_sync_excludes_delivered_only_receipts(self, client, async_session, admin_user, test_user, user_token, service_account):
        """Vérifie que /m2m/sync (avec include_receipts) ne renvoie QUE les receipts effectivement lus,
        même si on a ajouté du tracking 'delivered_at' qui ne devrait pas casser les apps existantes."""
        msg = await _make_user_message(async_session, admin_user, test_user, title="msg-sync")

        # Créer un receipt delivered-only (pas encore lu)
        await client.post(f"/api/messages/{msg.id}/delivered", headers=get_auth_header(user_token))

        token = await _get_sa_token(client, service_account)
        # since très ancien
        resp = await client.get(
            "/api/messages/m2m/sync?since=2020-01-01T00:00:00&include_receipts=true",
            headers=get_auth_header(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        sync_msg = next((m for m in body["messages"] if m["id"] == msg.id), None)
        assert sync_msg is not None
        # read_count = 0 (delivered uniquement, pas lu)
        assert sync_msg["read_count"] == 0
        # read_receipts ne contient PAS le receipt delivered-only
        assert sync_msg["read_receipts"] == []

        # Maintenant on lit, le receipt doit apparaitre
        await client.post(f"/api/messages/{msg.id}/read", headers=get_auth_header(user_token))
        resp2 = await client.get(
            "/api/messages/m2m/sync?since=2020-01-01T00:00:00&include_receipts=true",
            headers=get_auth_header(token),
        )
        sync_msg2 = next((m for m in resp2.json()["messages"] if m["id"] == msg.id), None)
        assert sync_msg2["read_count"] == 1
        assert len(sync_msg2["read_receipts"]) == 1

    @pytest.mark.asyncio
    async def test_m2m_receipts_excludes_delivered_only(self, client, async_session, admin_user, test_user, user_token, service_account):
        msg = await _make_user_message(async_session, admin_user, test_user, title="rcpt")
        await client.post(f"/api/messages/{msg.id}/delivered", headers=get_auth_header(user_token))

        token = await _get_sa_token(client, service_account)
        resp = await client.get(
            "/api/messages/m2m/receipts?since=2020-01-01T00:00:00",
            headers=get_auth_header(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        # Ne contient QUE les receipts read_at IS NOT NULL
        for r in body["receipts"]:
            assert r["read_at"] is not None

    @pytest.mark.asyncio
    async def test_m2m_sync_response_has_legacy_fields(self, client, async_session, admin_user, service_account):
        msg = await _make_broadcast(async_session, admin_user, title="m2m-legacy")

        token = await _get_sa_token(client, service_account)
        resp = await client.get(
            "/api/messages/m2m/sync?since=2020-01-01T00:00:00",
            headers=get_auth_header(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        # Top-level (snapshot du contrat M2M)
        for key in ("messages", "total", "page", "pages", "sync_timestamp"):
            assert key in body
        item = next(m for m in body["messages"] if m["id"] == msg.id)
        for key in ("id", "sender_type", "target_type", "priority", "title", "body",
                    "created_at", "read_count", "total_targets"):
            assert key in item, f"missing legacy field in M2M sync item: {key}"


# =============================================================================
# STATS — nouveaux compteurs
# =============================================================================

class TestStatsEnrichment:

    @pytest.mark.asyncio
    async def test_stats_includes_lifecycle_and_engagement(self, client, async_session, admin_user, admin_token):
        # 1 active sans expiration
        await _make_broadcast(async_session, admin_user, title="active1")
        # 1 active expirée
        await _make_broadcast(async_session, admin_user, title="exp", expires_in_hours=-1)
        # 1 archivée
        m = await _make_broadcast(async_session, admin_user, title="arch")
        m.status = "archived"
        m.archived_at = utcnow()
        await async_session.commit()

        resp = await client.get("/api/messages/stats", headers=get_auth_header(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        # Anciennes clés
        assert "total" in body
        assert "by_priority" in body
        # Nouvelles clés
        assert body["total_active"] >= 1
        assert body["total_expired"] >= 1
        assert body["total_archived"] >= 1
        assert "avg_read_rate" in body
        assert "avg_click_rate" in body
        assert "avg_delivered_rate" in body
        assert "apps_count" in body
        assert "top_apps" in body


# =============================================================================
# FILTRES — ?status=active|archived|expired
# =============================================================================

class TestStatusFilter:

    @pytest.mark.asyncio
    async def test_filter_status_active_excludes_archived(self, client, async_session, admin_user, admin_token):
        a = await _make_broadcast(async_session, admin_user, title="active")
        arch = await _make_broadcast(async_session, admin_user, title="arch")
        arch.status = "archived"
        arch.archived_at = utcnow()
        await async_session.commit()

        resp = await client.get("/api/messages/?status=active", headers=get_auth_header(admin_token))
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["messages"]]
        assert a.id in ids
        assert arch.id not in ids

    @pytest.mark.asyncio
    async def test_filter_status_archived_only(self, client, async_session, admin_user, admin_token):
        a = await _make_broadcast(async_session, admin_user, title="active")
        arch = await _make_broadcast(async_session, admin_user, title="arch")
        arch.status = "archived"
        arch.archived_at = utcnow()
        await async_session.commit()

        resp = await client.get("/api/messages/?status=archived", headers=get_auth_header(admin_token))
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["messages"]]
        assert arch.id in ids
        assert a.id not in ids

    @pytest.mark.asyncio
    async def test_filter_status_expired(self, client, async_session, admin_user, admin_token):
        active = await _make_broadcast(async_session, admin_user, title="ok")
        expired = await _make_broadcast(async_session, admin_user, title="exp", expires_in_hours=-1)

        resp = await client.get("/api/messages/?status=expired", headers=get_auth_header(admin_token))
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["messages"]]
        assert expired.id in ids
        assert active.id not in ids


class TestReceiptUniqueness:
    """Unicité (message_id, user_id) des read receipts.

    Incident 09/11/2026 : la table n'avait AUCUNE contrainte effective (l'UNIQUE
    de la migration 014 est un no-op : create_all depuis un modèle sans
    contrainte crée la table avant apply_migrations) → des doublons réels ont
    fait planter scalar_one_or_none() (« Multiple rows were found ») sur
    /api/messages/{id}/read en boucle.
    """

    @pytest.mark.asyncio
    async def test_duplicate_receipt_rejected_by_unique_constraint(
        self, async_session, admin_user
    ):
        from sqlalchemy.exc import IntegrityError

        msg = await _make_user_message(async_session, admin_user, admin_user)
        async_session.add(MessageReadReceipt(
            message_id=msg.id, user_id=admin_user.id, delivered_at=utcnow(),
        ))
        await async_session.commit()

        # Second receipt pour la même paire : doit être rejeté par la base.
        async_session.add(MessageReadReceipt(
            message_id=msg.id, user_id=admin_user.id, delivered_at=utcnow(),
        ))
        with pytest.raises(IntegrityError):
            await async_session.commit()


# =============================================================================
# MARK READ — propagation au groupe dédupliqué (bug « la ligne ×N reste Non lu »)
# =============================================================================

class TestMarkReadGroupPropagation:
    """Marquer lu une notification groupée doit marquer TOUTES les occurrences
    visibles du groupe (même titre + priorité + expéditeur) pour l'utilisateur.

    Symptôme corrigé (console « Mes notifications », group_similar=true) : la
    liste envoie POST /{id}/read sur le représentant du groupe (occurrence la
    plus récente) ; seul CE message passait lu, l'occurrence suivante devenait
    le nouveau représentant → badge « Non lu », ×N→×N-1, horodatage qui recule,
    et unread_total qui ne baissait que de 1 par groupe malgré le toast « N
    notification(s) marquée(s) comme lu(s) ».
    """

    @pytest.mark.asyncio
    async def test_mark_read_propagates_to_group_occurrences(
        self, client, async_session, admin_user, admin_token
    ):
        group = [
            await _make_broadcast(async_session, admin_user,
                                  title="App offline : nominatim", priority="urgent")
            for _ in range(3)
        ]
        other = await _make_broadcast(async_session, admin_user,
                                      title="Autre notif", priority="info")
        h = get_auth_header(admin_token)

        # L'utilisateur marque lu l'occurrence la plus récente du groupe
        resp = await client.post(f"/api/messages/{group[-1].id}/read", headers=h)
        assert resp.status_code == 200, resp.text

        # Vue groupée « non lus » : le groupe ne doit plus apparaître du tout
        resp = await client.get(
            "/api/messages/me",
            params={"group_similar": "true", "unread": "true"},
            headers=h,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["unread_total"] == 1  # seul « Autre notif » reste non lu
        assert [m["title"] for m in data["messages"]] == ["Autre notif"]

        # Vue groupée complète : représentant lu, occurrences intactes (×3)
        resp = await client.get(
            "/api/messages/me", params={"group_similar": "true"}, headers=h
        )
        assert resp.status_code == 200
        rows = {m["title"]: m for m in resp.json()["messages"]}
        assert rows["App offline : nominatim"]["is_read"] is True
        assert rows["App offline : nominatim"]["occurrences"] == 3

    @pytest.mark.asyncio
    async def test_group_propagation_is_per_user(
        self, client, async_session, admin_user, admin_token, test_user, user_token
    ):
        """La propagation ne crée des receipts que pour l'utilisateur qui marque :
        lire un groupe de broadcasts ne doit pas le lire pour les autres users."""
        b1 = await _make_broadcast(async_session, admin_user,
                                   title="Pic d'erreurs Hub", priority="warning")
        b2 = await _make_broadcast(async_session, admin_user,
                                   title="Pic d'erreurs Hub", priority="warning")

        # admin lit UNE occurrence du groupe
        resp = await client.post(f"/api/messages/{b2.id}/read",
                                 headers=get_auth_header(admin_token))
        assert resp.status_code == 200, resp.text

        # Le groupe est lu pour admin (propagation sur les 2 occurrences)…
        admin_receipts = (await async_session.execute(
            select(MessageReadReceipt).where(
                MessageReadReceipt.user_id == admin_user.id)
        )).scalars().all()
        assert {r.message_id for r in admin_receipts} == {b1.id, b2.id}

        # …mais AUCUN receipt n'a été créé pour test_user
        test_receipts = (await async_session.execute(
            select(MessageReadReceipt).where(
                MessageReadReceipt.user_id == test_user.id)
        )).scalars().all()
        assert test_receipts == []

        # et le groupe reste entièrement non lu pour test_user
        resp = await client.get(
            "/api/messages/me", params={"group_similar": "true"},
            headers=get_auth_header(user_token),
        )
        assert resp.status_code == 200
        assert resp.json()["unread_total"] == 2
