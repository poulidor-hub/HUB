"""
Routes de messagerie - Envoi, consultation et gestion des messages in-app.

Système complet qui permet :
- Admin : envoi ciblé ou broadcast, listing, détail, export CSV, purge
- Utilisateur : consultation de ses messages, accusé de lecture
- M2M : envoi depuis une application via service account
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, delete, desc, case
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Union
from urllib.parse import urlparse, urljoin
import csv
import io
import time
import logging

from app.database import get_db
from app.models import User, UserMessage, MessageReadReceipt, ServiceAccount, Application, utcnow
from app.auth import get_current_user, get_current_entity, get_current_admin, get_settings
from app.schemas import (
    MessageCreate, MessageM2MCreate, MessageResponse, MessageBrief,
    PaginatedMessagesResponse,
    MessageSyncItem, MessageSyncResponse,
    ReceiptSyncItem, ReceiptSyncResponse,
    MessageDetailSyncResponse, MessageReadReceiptResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/messages", tags=["Messages"])
settings = get_settings()


# Cooldown pour les notifications SSE de messages
_messages_last_notify = 0.0


def _notify_message(event_type: str = "messages_updated", data: dict = None):
    """Notifie les clients SSE d'une mise à jour de messages."""
    global _messages_last_notify
    current_time = time.time()
    if current_time - _messages_last_notify < 0.5:
        return
    _messages_last_notify = current_time
    try:
        from app.routes.events import notify_event
        notify_event(event_type, data or {})
    except ImportError:
        pass


async def _send_user_message_sse(user_id: int, message_data: dict):
    """Envoie un message SSE à un utilisateur spécifique."""
    try:
        from app.routes.events import send_to_user
        await send_to_user(user_id, "user_message", message_data)
    except ImportError:
        pass


def _message_to_brief(
    msg: UserMessage,
    is_read: bool = False,
    receipt: Optional["MessageReadReceipt"] = None,
) -> dict:
    """Convertit un UserMessage en dict pour MessageBrief / SSE."""
    return {
        "id": msg.id,
        "title": msg.title,
        "body": msg.body,
        "priority": msg.priority,
        "sender_type": msg.sender_type,
        "sender_app_slug": msg.sender_app_slug,
        "action_url": msg.action_url,
        "action_label": msg.action_label,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "expires_at": msg.expires_at.isoformat() if msg.expires_at else None,
        "is_read": is_read,
        "status": getattr(msg, "status", "active") or "active",
        "target_type": msg.target_type,
        "delivered_at": (receipt.delivered_at.isoformat() if (receipt and receipt.delivered_at) else None),
        "clicked_at": (receipt.clicked_at.isoformat() if (receipt and receipt.clicked_at) else None),
        "read_at": (receipt.read_at.isoformat() if (receipt and receipt.read_at) else None),
    }


async def _resolve_usernames(db: AsyncSession, usernames: list[str]) -> dict[str, int]:
    """Résout une liste de usernames en dict {username: user_id}."""
    result = await db.execute(
        select(User.id, User.username).where(User.username.in_(usernames))
    )
    return {row.username: row.id for row in result.all()}


async def _build_users_map(db: AsyncSession, user_ids: set[int]) -> dict[int, str]:
    """Résout une liste d'user_ids en dict {id: username}."""
    if not user_ids:
        return {}
    result = await db.execute(
        select(User.id, User.username).where(User.id.in_(user_ids))
    )
    return {row.id: row.username for row in result.all()}


def _resolve_app_action_url(action_url: Optional[str], frontend_url: Optional[str]) -> Optional[str]:
    """
    Résout une `action_url` envoyée par une application via M2M.

    - Si `action_url` est vide, retourne None.
    - Si `action_url` est absolue (scheme http/https), la retourne telle quelle.
    - Si `action_url` est relative et `frontend_url` est disponible, la préfixe avec
      `frontend_url` (en ajoutant `http://` par défaut si le scheme manque).
    - Si `action_url` est relative mais `frontend_url` indisponible, log un warning et
      retourne `action_url` telle quelle.

    Tronque le résultat à 500 caractères (limite colonne `action_url`).
    """
    if not action_url:
        return None

    parsed = urlparse(action_url)
    if parsed.scheme in ("http", "https"):
        return action_url[:500]

    if not frontend_url:
        logger.warning(
            "action_url relative '%s' mais frontend_url manquant pour l'app expéditrice — lien non résolu",
            action_url,
        )
        return action_url[:500]

    base = frontend_url.strip()
    if not urlparse(base).scheme:
        base = "http://" + base
    if not base.endswith("/"):
        base = base + "/"
    resolved = urljoin(base, action_url.lstrip("/"))
    return resolved[:500]


async def purge_old_messages(db: AsyncSession, retention_days: int) -> int:
    """Purge les messages plus anciens que retention_days jours."""
    if retention_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=retention_days)
    result = await db.execute(
        delete(UserMessage).where(UserMessage.created_at < cutoff)
    )
    await db.commit()
    return result.rowcount


# =============================================================================
# ENDPOINTS ADMIN
# =============================================================================

@router.post("/send")
async def send_message(
    payload: MessageCreate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Envoie un message (admin). Ciblé à des utilisateurs ou broadcast.
    """
    from app.routes.audit import log_action

    if payload.target_type == "user":
        if not payload.target_user_ids or len(payload.target_user_ids) == 0:
            raise HTTPException(status_code=400, detail="target_user_ids requis pour un message ciblé")
        # Vérifier que les users existent
        result = await db.execute(
            select(User.id).where(User.id.in_(payload.target_user_ids))
        )
        valid_ids = {row[0] for row in result.all()}
        invalid_ids = set(payload.target_user_ids) - valid_ids
        if invalid_ids:
            raise HTTPException(status_code=400, detail=f"Utilisateurs introuvables: {sorted(invalid_ids)}")

    messages_created = []

    if payload.target_type == "broadcast":
        # Snapshot du nombre total de destinataires à l'envoi
        total_targets = await db.scalar(select(func.count(User.id))) or 0
        msg = UserMessage(
            sender_type="admin",
            sender_id=current_user.id,
            target_type="broadcast",
            priority=payload.priority.value,
            title=payload.title,
            body=payload.body,
            action_url=payload.action_url,
            action_label=payload.action_label,
            expires_at=payload.expires_at,
            status="active",
            total_targets=total_targets,
        )
        db.add(msg)
        await db.flush()
        messages_created.append(msg)

        # SSE broadcast
        from app.routes.events import broadcast_event
        await broadcast_event("user_message", _message_to_brief(msg))

    else:
        # Résoudre les usernames pour target_username
        users_map = await _build_users_map(db, set(payload.target_user_ids))
        for uid in payload.target_user_ids:
            msg = UserMessage(
                sender_type="admin",
                sender_id=current_user.id,
                target_type="user",
                target_user_id=uid,
                target_username=users_map.get(uid),
                priority=payload.priority.value,
                title=payload.title,
                body=payload.body,
                action_url=payload.action_url,
                action_label=payload.action_label,
                expires_at=payload.expires_at,
                status="active",
                total_targets=1,
            )
            db.add(msg)
            await db.flush()
            messages_created.append(msg)
            # SSE ciblé
            await _send_user_message_sse(uid, _message_to_brief(msg))

    await db.commit()

    await log_action(
        db, current_user.id, "send_message",
        target=f"{payload.target_type}:{len(messages_created)} message(s)",
        details=f"title={payload.title}, priority={payload.priority.value}",
        notify_sse=False
    )
    await db.commit()

    _notify_message("messages_updated", {"action": "sent", "count": len(messages_created)})

    return {
        "sent": len(messages_created),
        "message_ids": [m.id for m in messages_created],
        "target_type": payload.target_type.value,
    }


@router.get("/")
async def list_messages(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    priority: Optional[str] = Query(None),
    sender_type: Optional[str] = Query(None),
    sender_app: Optional[str] = Query(None),
    target_user_id: Optional[int] = Query(None),
    target_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    hours: Optional[int] = Query(None, ge=1, le=8760),
    status: Optional[str] = Query(None, description="Filtre cycle de vie : active | archived | expired | all (défaut: all)"),
    expiring_within_hours: Optional[int] = Query(None, ge=1, le=8760),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Liste paginée des messages avec filtres (admin)."""
    query = select(UserMessage)
    count_query = select(func.count(UserMessage.id))

    now = utcnow()
    filters = []
    if priority:
        filters.append(UserMessage.priority == priority)
    if sender_type:
        filters.append(UserMessage.sender_type == sender_type)
    if sender_app:
        filters.append(UserMessage.sender_app_slug == sender_app)
    if target_user_id:
        filters.append(UserMessage.target_user_id == target_user_id)
    if target_type:
        filters.append(UserMessage.target_type == target_type)
    if search:
        search_pattern = f"%{search}%"
        filters.append(or_(
            UserMessage.title.ilike(search_pattern),
            UserMessage.body.ilike(search_pattern),
        ))
    if hours:
        since = now - timedelta(hours=hours)
        filters.append(UserMessage.created_at >= since)
    if status and status != "all":
        if status == "active":
            filters.append(UserMessage.status == "active")
            filters.append(or_(UserMessage.expires_at.is_(None), UserMessage.expires_at > now))
        elif status == "archived":
            filters.append(UserMessage.status == "archived")
        elif status == "expired":
            filters.append(UserMessage.status == "active")
            filters.append(UserMessage.expires_at.isnot(None))
            filters.append(UserMessage.expires_at <= now)
    if expiring_within_hours:
        until = now + timedelta(hours=expiring_within_hours)
        filters.append(UserMessage.expires_at.isnot(None))
        filters.append(UserMessage.expires_at <= until)
        filters.append(UserMessage.expires_at > now)

    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    total = await db.scalar(count_query) or 0
    pages = max(1, (total + per_page - 1) // per_page)

    offset = (page - 1) * per_page
    result = await db.execute(
        query.order_by(desc(UserMessage.created_at))
        .offset(offset).limit(per_page)
    )
    messages = result.scalars().all()

    # Résoudre les usernames
    user_ids = set()
    for m in messages:
        if m.sender_id:
            user_ids.add(m.sender_id)
        if m.target_user_id:
            user_ids.add(m.target_user_id)
    users_map = await _build_users_map(db, user_ids)

    # Compter les lectures pour chaque message (read_at IS NOT NULL)
    msg_ids = [m.id for m in messages]
    read_counts = {}
    if msg_ids:
        rc_result = await db.execute(
            select(
                MessageReadReceipt.message_id,
                func.count(MessageReadReceipt.id)
            ).where(
                and_(
                    MessageReadReceipt.message_id.in_(msg_ids),
                    MessageReadReceipt.read_at.isnot(None),
                )
            ).group_by(MessageReadReceipt.message_id)
        )
        read_counts = {row[0]: row[1] for row in rc_result.all()}

    # Compter les cibles totales par message (broadcast = total users)
    total_users = await db.scalar(select(func.count(User.id))) or 0

    items = []
    for m in messages:
        rc = read_counts.get(m.id, m.read_count or 0)
        tt = m.total_targets if (m.total_targets and m.total_targets > 0) else (
            total_users if m.target_type == "broadcast" else 1
        )
        is_expired = bool(m.expires_at and m.expires_at <= now)
        items.append(MessageResponse(
            id=m.id,
            sender_type=m.sender_type,
            sender_id=m.sender_id,
            sender_app_slug=m.sender_app_slug,
            sender_username=users_map.get(m.sender_id),
            target_type=m.target_type,
            target_user_id=m.target_user_id,
            target_username=users_map.get(m.target_user_id),
            priority=m.priority,
            title=m.title,
            body=m.body,
            action_url=m.action_url,
            action_label=m.action_label,
            expires_at=m.expires_at,
            created_at=m.created_at,
            read_count=rc,
            total_targets=tt,
            status=m.status or "active",
            archived_at=m.archived_at,
            delivered_count=m.delivered_count or 0,
            click_count=m.click_count or 0,
            is_expired=is_expired,
            is_resend=m.is_resend or False,
            original_message_id=m.original_message_id,
        ))

    return PaginatedMessagesResponse(
        messages=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/stats")
async def get_message_stats(
    hours: Optional[int] = Query(None, ge=1, le=8760),
    target_type: Optional[str] = Query(None),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Statistiques agrégées des messages (admin)."""
    base_filter = []
    if hours:
        since = utcnow() - timedelta(hours=hours)
        base_filter.append(UserMessage.created_at >= since)
    if target_type:
        base_filter.append(UserMessage.target_type == target_type)

    where_clause = and_(*base_filter) if base_filter else True

    total = await db.scalar(select(func.count(UserMessage.id)).where(where_clause)) or 0

    # Par priorité
    prio_result = await db.execute(
        select(UserMessage.priority, func.count(UserMessage.id))
        .where(where_clause)
        .group_by(UserMessage.priority)
    )
    by_priority = {row[0]: row[1] for row in prio_result.all()}

    # Par sender_type
    st_result = await db.execute(
        select(UserMessage.sender_type, func.count(UserMessage.id))
        .where(where_clause)
        .group_by(UserMessage.sender_type)
    )
    by_sender_type = {row[0]: row[1] for row in st_result.all()}

    # Par app source
    app_result = await db.execute(
        select(UserMessage.sender_app_slug, func.count(UserMessage.id))
        .where(and_(where_clause, UserMessage.sender_app_slug.isnot(None)))
        .group_by(UserMessage.sender_app_slug)
    )
    by_app = {row[0]: row[1] for row in app_result.all()}

    # ===== Enrichissements (cycle de vie + engagement) =====
    now = utcnow()
    total_active = await db.scalar(
        select(func.count(UserMessage.id)).where(
            and_(where_clause,
                 UserMessage.status == "active",
                 or_(UserMessage.expires_at.is_(None), UserMessage.expires_at > now))
        )
    ) or 0
    total_archived = await db.scalar(
        select(func.count(UserMessage.id)).where(
            and_(where_clause, UserMessage.status == "archived")
        )
    ) or 0
    total_expired = await db.scalar(
        select(func.count(UserMessage.id)).where(
            and_(where_clause,
                 UserMessage.status == "active",
                 UserMessage.expires_at.isnot(None),
                 UserMessage.expires_at <= now)
        )
    ) or 0

    # Taux moyens (sur les messages avec total_targets > 0 pour éviter divisions par zéro)
    rates_row = await db.execute(
        select(
            func.coalesce(func.avg(
                case(
                    (UserMessage.total_targets > 0,
                     UserMessage.read_count * 1.0 / UserMessage.total_targets),
                    else_=None,
                )
            ), 0.0),
            func.coalesce(func.avg(
                case(
                    (UserMessage.total_targets > 0,
                     UserMessage.click_count * 1.0 / UserMessage.total_targets),
                    else_=None,
                )
            ), 0.0),
            func.coalesce(func.avg(
                case(
                    (UserMessage.total_targets > 0,
                     UserMessage.delivered_count * 1.0 / UserMessage.total_targets),
                    else_=None,
                )
            ), 0.0),
        ).where(where_clause)
    )
    avg_read, avg_click, avg_delivered = rates_row.one()

    # Top apps émettrices
    top_apps = [
        {"app": slug, "count": cnt}
        for slug, cnt in by_app.items()
    ]
    top_apps.sort(key=lambda x: x["count"], reverse=True)

    return {
        "period_hours": hours,
        "total": total,
        "by_priority": by_priority,
        "by_sender_type": by_sender_type,
        "by_app": by_app,
        # Ajouts (rétro-compat : nouveaux champs)
        "total_active": total_active,
        "total_archived": total_archived,
        "total_expired": total_expired,
        "avg_read_rate": round(float(avg_read or 0.0), 4),
        "avg_click_rate": round(float(avg_click or 0.0), 4),
        "avg_delivered_rate": round(float(avg_delivered or 0.0), 4),
        "apps_count": len(by_app),
        "top_apps": top_apps[:10],
    }


@router.get("/export")
async def export_messages_csv(
    hours: Optional[int] = Query(None, ge=1, le=8760),
    priority: Optional[str] = Query(None),
    sender_app: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Export CSV des messages (admin)."""
    query = select(UserMessage)
    filters = []
    if hours:
        since = utcnow() - timedelta(hours=hours)
        filters.append(UserMessage.created_at >= since)
    if priority:
        filters.append(UserMessage.priority == priority)
    if sender_app:
        filters.append(UserMessage.sender_app_slug == sender_app)
    if target_type:
        filters.append(UserMessage.target_type == target_type)

    if filters:
        query = query.where(and_(*filters))

    result = await db.execute(query.order_by(desc(UserMessage.created_at)))
    messages = result.scalars().all()

    # Résoudre les usernames
    user_ids = set()
    for m in messages:
        if m.sender_id:
            user_ids.add(m.sender_id)
        if m.target_user_id:
            user_ids.add(m.target_user_id)
    users_map = await _build_users_map(db, user_ids)

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "ID", "Date", "Priorité", "Expéditeur", "App source",
        "Type cible", "Destinataire", "Titre", "Corps",
        "URL action", "Expiration"
    ])
    for m in messages:
        sender = users_map.get(m.sender_id, m.sender_type)
        target = users_map.get(m.target_user_id, "broadcast" if m.target_type == "broadcast" else "")
        writer.writerow([
            m.id,
            m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else "",
            m.priority,
            sender,
            m.sender_app_slug or "",
            m.target_type,
            target,
            m.title,
            m.body or "",
            m.action_url or "",
            m.expires_at.strftime("%Y-%m-%d %H:%M:%S") if m.expires_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=messages_export.csv"},
    )


# =============================================================================
# ENDPOINTS UTILISATEUR (avant /{message_id} pour éviter conflit de route)
# =============================================================================

@router.get("/me")
async def get_my_messages(
    include_archived: bool = Query(False, description="Inclure les messages archivés par l'admin"),
    include_expired: bool = Query(True, description="Inclure les messages expirés"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Messages de l'utilisateur connecté (ciblés + broadcasts).

    Marque automatiquement les messages comme "livrés" (delivered_at) au passage,
    pour le tracking d'engagement (rentre dans la même transaction).
    """
    now = utcnow()

    msg_filters = [
        or_(
            UserMessage.target_user_id == current_user.id,
            UserMessage.target_type == "broadcast",
        )
    ]
    if not include_archived:
        msg_filters.append(UserMessage.status == "active")
    if not include_expired:
        msg_filters.append(or_(UserMessage.expires_at.is_(None), UserMessage.expires_at > now))

    query = (
        select(UserMessage)
        .where(and_(*msg_filters))
        .order_by(desc(UserMessage.created_at))
        .limit(200)
    )
    result = await db.execute(query)
    messages = result.scalars().all()

    # Récupérer les receipts existants de cet utilisateur pour ces messages
    msg_ids = [m.id for m in messages]
    receipts_by_msg: dict[int, MessageReadReceipt] = {}
    if msg_ids:
        rr_result = await db.execute(
            select(MessageReadReceipt).where(and_(
                MessageReadReceipt.message_id.in_(msg_ids),
                MessageReadReceipt.user_id == current_user.id,
            ))
        )
        for r in rr_result.scalars().all():
            receipts_by_msg[r.message_id] = r

    # Auto-delivery : pour chaque message visible non encore livré, créer ou mettre
    # à jour le receipt avec delivered_at + incrémenter delivered_count.
    new_deliveries = 0
    for m in messages:
        rcpt = receipts_by_msg.get(m.id)
        if rcpt is None:
            rcpt = MessageReadReceipt(
                message_id=m.id,
                user_id=current_user.id,
                delivered_at=now,
            )
            db.add(rcpt)
            receipts_by_msg[m.id] = rcpt
            m.delivered_count = (m.delivered_count or 0) + 1
            new_deliveries += 1
        elif rcpt.delivered_at is None:
            rcpt.delivered_at = now
            m.delivered_count = (m.delivered_count or 0) + 1
            new_deliveries += 1
    if new_deliveries:
        await db.commit()

    return [
        MessageBrief(**_message_to_brief(
            m,
            is_read=(receipts_by_msg.get(m.id) is not None and receipts_by_msg[m.id].read_at is not None),
            receipt=receipts_by_msg.get(m.id),
        ))
        for m in messages
    ]


@router.get("/me/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Nombre de messages non lus de l'utilisateur connecté."""
    now = utcnow()

    # Sous-requête : IDs des messages déjà lus (read_at IS NOT NULL)
    read_subq = (
        select(MessageReadReceipt.message_id)
        .where(and_(
            MessageReadReceipt.user_id == current_user.id,
            MessageReadReceipt.read_at.isnot(None),
        ))
        .scalar_subquery()
    )

    count = await db.scalar(
        select(func.count(UserMessage.id))
        .where(
            or_(
                UserMessage.target_user_id == current_user.id,
                UserMessage.target_type == "broadcast",
            )
        )
        .where(UserMessage.status == "active")
        .where(
            or_(
                UserMessage.expires_at.is_(None),
                UserMessage.expires_at > now,
            )
        )
        .where(UserMessage.id.notin_(read_subq))
    ) or 0

    return {"count": count}


@router.post("/read-all")
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Marquer tous les messages de l'utilisateur comme lus."""
    now = utcnow()

    # Messages visibles non encore lus
    read_subq = (
        select(MessageReadReceipt.message_id)
        .where(and_(
            MessageReadReceipt.user_id == current_user.id,
            MessageReadReceipt.read_at.isnot(None),
        ))
        .scalar_subquery()
    )

    result = await db.execute(
        select(UserMessage)
        .where(
            or_(
                UserMessage.target_user_id == current_user.id,
                UserMessage.target_type == "broadcast",
            )
        )
        .where(UserMessage.status == "active")
        .where(
            or_(
                UserMessage.expires_at.is_(None),
                UserMessage.expires_at > now,
            )
        )
        .where(UserMessage.id.notin_(read_subq))
    )
    msgs = result.scalars().all()

    if not msgs:
        return {"marked_read": 0}

    # Charger les receipts existants (delivered-only) pour ces messages
    msg_ids = [m.id for m in msgs]
    existing_receipts = await db.execute(
        select(MessageReadReceipt).where(and_(
            MessageReadReceipt.message_id.in_(msg_ids),
            MessageReadReceipt.user_id == current_user.id,
        ))
    )
    by_msg = {r.message_id: r for r in existing_receipts.scalars().all()}

    marked = 0
    for m in msgs:
        rcpt = by_msg.get(m.id)
        if rcpt is None:
            db.add(MessageReadReceipt(
                message_id=m.id,
                user_id=current_user.id,
                delivered_at=now,
                read_at=now,
            ))
            m.delivered_count = (m.delivered_count or 0) + 1
        elif rcpt.read_at is None:
            rcpt.read_at = now
            if rcpt.delivered_at is None:
                rcpt.delivered_at = now
                m.delivered_count = (m.delivered_count or 0) + 1
        else:
            continue
        m.read_count = (m.read_count or 0) + 1
        marked += 1

    await db.commit()
    return {"marked_read": marked}


@router.post("/{message_id}/read")
async def mark_message_read(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Marquer un message comme lu."""
    msg = await db.get(UserMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message introuvable")

    if msg.target_type == "user" and msg.target_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Ce message ne vous est pas destiné")

    now = utcnow()
    existing = await db.execute(
        select(MessageReadReceipt).where(and_(
            MessageReadReceipt.message_id == message_id,
            MessageReadReceipt.user_id == current_user.id,
        ))
    )
    receipt = existing.scalar_one_or_none()

    if receipt is None:
        receipt = MessageReadReceipt(
            message_id=message_id,
            user_id=current_user.id,
            delivered_at=now,
            read_at=now,
        )
        db.add(receipt)
        msg.delivered_count = (msg.delivered_count or 0) + 1
        msg.read_count = (msg.read_count or 0) + 1
        await db.commit()
        return {"status": "read", "message_id": message_id}

    if receipt.read_at is not None:
        return {"status": "already_read", "message_id": message_id}

    # Receipt existe (delivered-only) : on marque la lecture
    receipt.read_at = now
    if receipt.delivered_at is None:
        receipt.delivered_at = now
        msg.delivered_count = (msg.delivered_count or 0) + 1
    msg.read_count = (msg.read_count or 0) + 1
    await db.commit()
    return {"status": "read", "message_id": message_id}


@router.post("/{message_id}/delivered")
async def mark_message_delivered(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Marquer un message comme livré (idempotent).

    Appelé par le client au chargement initial de la boîte de réception ou à la réception
    SSE, pour permettre un tracking précis du taux de livraison vs lecture.
    """
    msg = await db.get(UserMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message introuvable")
    if msg.target_type == "user" and msg.target_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Ce message ne vous est pas destiné")

    now = utcnow()
    existing = await db.execute(
        select(MessageReadReceipt).where(and_(
            MessageReadReceipt.message_id == message_id,
            MessageReadReceipt.user_id == current_user.id,
        ))
    )
    receipt = existing.scalar_one_or_none()

    if receipt is None:
        db.add(MessageReadReceipt(
            message_id=message_id,
            user_id=current_user.id,
            delivered_at=now,
        ))
        msg.delivered_count = (msg.delivered_count or 0) + 1
        await db.commit()
        return {"status": "delivered", "message_id": message_id}

    if receipt.delivered_at is not None:
        return {"status": "already_delivered", "message_id": message_id}

    receipt.delivered_at = now
    msg.delivered_count = (msg.delivered_count or 0) + 1
    await db.commit()
    return {"status": "delivered", "message_id": message_id}


@router.post("/{message_id}/click")
async def mark_message_clicked(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Tracking du clic sur action_url. Idempotent (1 clic compté par utilisateur)."""
    msg = await db.get(UserMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message introuvable")
    if msg.target_type == "user" and msg.target_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Ce message ne vous est pas destiné")

    now = utcnow()
    existing = await db.execute(
        select(MessageReadReceipt).where(and_(
            MessageReadReceipt.message_id == message_id,
            MessageReadReceipt.user_id == current_user.id,
        ))
    )
    receipt = existing.scalar_one_or_none()

    if receipt is None:
        receipt = MessageReadReceipt(
            message_id=message_id,
            user_id=current_user.id,
            delivered_at=now,
            read_at=now,
            clicked_at=now,
        )
        db.add(receipt)
        msg.delivered_count = (msg.delivered_count or 0) + 1
        msg.read_count = (msg.read_count or 0) + 1
        msg.click_count = (msg.click_count or 0) + 1
        await db.commit()
        return {"status": "clicked", "message_id": message_id}

    if receipt.clicked_at is not None:
        return {"status": "already_clicked", "message_id": message_id}

    receipt.clicked_at = now
    if receipt.read_at is None:
        receipt.read_at = now
        msg.read_count = (msg.read_count or 0) + 1
    if receipt.delivered_at is None:
        receipt.delivered_at = now
        msg.delivered_count = (msg.delivered_count or 0) + 1
    msg.click_count = (msg.click_count or 0) + 1
    await db.commit()
    return {"status": "clicked", "message_id": message_id}


# =============================================================================
# ENDPOINTS ADMIN — Détail et purge (après /me pour éviter conflit de route)
# =============================================================================

@router.get("/{message_id}")
async def get_message_detail(
    message_id: int,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Détail d'un message + accusés de lecture (admin)."""
    msg = await db.get(UserMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message introuvable")

    # Accusés de lecture
    receipts_result = await db.execute(
        select(MessageReadReceipt)
        .where(MessageReadReceipt.message_id == message_id)
        .order_by(MessageReadReceipt.read_at.desc())
    )
    receipts = receipts_result.scalars().all()

    # Résoudre usernames
    user_ids = set()
    if msg.sender_id:
        user_ids.add(msg.sender_id)
    if msg.target_user_id:
        user_ids.add(msg.target_user_id)
    for r in receipts:
        user_ids.add(r.user_id)
    users_map = await _build_users_map(db, user_ids)

    total_users = await db.scalar(select(func.count(User.id))) or 0

    return {
        "message": MessageResponse(
            id=msg.id,
            sender_type=msg.sender_type,
            sender_id=msg.sender_id,
            sender_app_slug=msg.sender_app_slug,
            sender_username=users_map.get(msg.sender_id),
            target_type=msg.target_type,
            target_user_id=msg.target_user_id,
            target_username=users_map.get(msg.target_user_id),
            priority=msg.priority,
            title=msg.title,
            body=msg.body,
            action_url=msg.action_url,
            action_label=msg.action_label,
            expires_at=msg.expires_at,
            created_at=msg.created_at,
            read_count=len([r for r in receipts if r.read_at is not None]),
            total_targets=msg.total_targets if (msg.total_targets and msg.total_targets > 0) else (
                total_users if msg.target_type == "broadcast" else 1
            ),
            status=msg.status or "active",
            archived_at=msg.archived_at,
            delivered_count=msg.delivered_count or 0,
            click_count=msg.click_count or 0,
            is_expired=bool(msg.expires_at and msg.expires_at <= utcnow()),
            is_resend=msg.is_resend or False,
            original_message_id=msg.original_message_id,
        ),
        "read_receipts": [
            {
                "user_id": r.user_id,
                "username": users_map.get(r.user_id, "?"),
                "read_at": r.read_at.isoformat() if r.read_at else None,
                "delivered_at": r.delivered_at.isoformat() if r.delivered_at else None,
                "clicked_at": r.clicked_at.isoformat() if r.clicked_at else None,
            }
            for r in receipts
        ],
    }


@router.post("/{message_id}/archive")
async def archive_message(
    message_id: int,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Archive un message (soft-delete admin). Reste consultable côté user dans l'historique."""
    from app.routes.audit import log_action

    msg = await db.get(UserMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message introuvable")
    if msg.status == "archived":
        return {"status": "already_archived", "message_id": message_id}

    msg.status = "archived"
    msg.archived_at = utcnow()
    await db.commit()

    await log_action(
        db, current_user.id, "archive_message",
        target=f"message:{message_id}",
        details=f"title={msg.title}",
        notify_sse=False,
    )
    await db.commit()
    _notify_message("messages_updated", {"action": "archived", "id": message_id})
    return {"status": "archived", "message_id": message_id}


@router.post("/{message_id}/restore")
async def restore_message(
    message_id: int,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Restaure un message archivé (status -> active)."""
    from app.routes.audit import log_action

    msg = await db.get(UserMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message introuvable")
    if msg.status != "archived":
        return {"status": "not_archived", "message_id": message_id}

    msg.status = "active"
    msg.archived_at = None
    await db.commit()

    await log_action(
        db, current_user.id, "restore_message",
        target=f"message:{message_id}",
        details=f"title={msg.title}",
        notify_sse=False,
    )
    await db.commit()
    _notify_message("messages_updated", {"action": "restored", "id": message_id})
    return {"status": "restored", "message_id": message_id}


@router.delete("/{message_id}/hard")
async def hard_delete_message(
    message_id: int,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Suppression définitive d'un message (purge la DB, supprime les receipts)."""
    from app.routes.audit import log_action

    msg = await db.get(UserMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message introuvable")
    title = msg.title
    await db.delete(msg)
    await db.commit()

    await log_action(
        db, current_user.id, "hard_delete_message",
        target=f"message:{message_id}",
        details=f"title={title}",
        notify_sse=False,
    )
    await db.commit()
    _notify_message("messages_updated", {"action": "hard_deleted", "id": message_id})
    return {"status": "hard_deleted", "message_id": message_id}


@router.delete("/")
async def purge_messages(
    hours: Optional[int] = Query(None, ge=1, le=8760),
    priority: Optional[str] = Query(None),
    sender_app: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    hard: bool = Query(False, description="Si true, suppression définitive (au lieu de simple archivage)"),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Archive (soft-delete) ou supprime définitivement (?hard=true) les messages filtrés (admin)."""
    from app.routes.audit import log_action

    filters = []
    if hours:
        since = utcnow() - timedelta(hours=hours)
        filters.append(UserMessage.created_at >= since)
    if priority:
        filters.append(UserMessage.priority == priority)
    if sender_app:
        filters.append(UserMessage.sender_app_slug == sender_app)
    if target_type:
        filters.append(UserMessage.target_type == target_type)

    if hard:
        stmt = delete(UserMessage)
        if filters:
            stmt = stmt.where(and_(*filters))
        result = await db.execute(stmt)
        affected = result.rowcount
        await db.commit()
        action = "purge_messages_hard"
        msg_label = "supprimé(s) définitivement"
        sse_action = "purged"
    else:
        # Soft-archive : seuls les messages encore actifs sont archivables
        from sqlalchemy import update as sql_update
        stmt = sql_update(UserMessage).values(
            status="archived", archived_at=utcnow()
        ).where(UserMessage.status == "active")
        if filters:
            stmt = stmt.where(and_(*filters))
        result = await db.execute(stmt)
        affected = result.rowcount
        await db.commit()
        action = "archive_messages"
        msg_label = "archivé(s)"
        sse_action = "archived"

    await log_action(
        db, current_user.id, action,
        details=f"affected={affected}, hours={hours}, priority={priority}, app={sender_app}, hard={hard}",
        notify_sse=False
    )
    await db.commit()

    _notify_message("messages_updated", {"action": sse_action, "count": affected})

    return {"affected": affected, "deleted": affected, "message": f"{affected} message(s) {msg_label}", "hard": hard}


@router.delete("/batch")
async def delete_messages_batch(
    payload: dict,
    hard: bool = Query(False),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Archive (soft) ou supprime (?hard=true) une liste de messages par leurs IDs (admin)."""
    from app.routes.audit import log_action

    ids = payload.get("ids", [])
    if not ids or not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="Liste d'IDs requise")
    if len(ids) > 500:
        raise HTTPException(status_code=400, detail="Maximum 500 messages par requête")

    try:
        ids = [int(i) for i in ids]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="IDs invalides")

    if hard:
        result = await db.execute(
            delete(UserMessage).where(UserMessage.id.in_(ids))
        )
        affected = result.rowcount
        await db.commit()
        action = "delete_messages_batch_hard"
        msg_label = "supprimé(s) définitivement"
        sse_action = "deleted"
    else:
        from sqlalchemy import update as sql_update
        result = await db.execute(
            sql_update(UserMessage).values(
                status="archived", archived_at=utcnow()
            ).where(and_(
                UserMessage.id.in_(ids),
                UserMessage.status == "active",
            ))
        )
        affected = result.rowcount
        await db.commit()
        action = "archive_messages_batch"
        msg_label = "archivé(s)"
        sse_action = "archived"

    await log_action(
        db, current_user.id, action,
        details=f"affected={affected}, ids={ids[:10]}{'...' if len(ids) > 10 else ''}, hard={hard}",
        notify_sse=False
    )
    await db.commit()

    _notify_message("messages_updated", {"action": sse_action, "count": affected})

    return {"affected": affected, "deleted": affected, "message": f"{affected} message(s) {msg_label}", "hard": hard}


# =============================================================================
# ENDPOINT M2M (Apps → Hub)
# =============================================================================

@router.post("/m2m/send")
async def m2m_send_message(
    request: Request,
    payload: MessageM2MCreate,
    entity: Union[User, ServiceAccount] = Depends(get_current_entity),
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint M2M : une application envoie un message via son service account.
    Auth: service account token. Le sender_app_slug est résolu depuis le header
    X-Slug ou la première application associée au service account.
    """
    if not isinstance(entity, ServiceAccount):
        raise HTTPException(status_code=403, detail="Cet endpoint est réservé aux service accounts (M2M)")

    # Résoudre le app_slug : header X-Slug > première application associée > nom du SA
    sender_app_slug = request.headers.get("x-slug") or (
        entity.applications[0].slug if entity.applications else None
    ) or entity.name

    # Résoudre l'URL frontale de l'app expéditrice pour enrichir une `action_url` relative.
    sender_app_frontend_url: Optional[str] = None
    sender_app = next(
        (a for a in (entity.applications or []) if a.slug == sender_app_slug),
        None,
    )
    if sender_app is None:
        result = await db.execute(
            select(Application.frontend_url).where(Application.slug == sender_app_slug)
        )
        sender_app_frontend_url = result.scalar_one_or_none()
    else:
        sender_app_frontend_url = sender_app.frontend_url

    resolved_action_url = _resolve_app_action_url(payload.action_url, sender_app_frontend_url)

    # Résoudre les usernames
    username_map = await _resolve_usernames(db, payload.target_usernames)
    not_found = set(payload.target_usernames) - set(username_map.keys())
    if not_found:
        raise HTTPException(status_code=400, detail=f"Utilisateurs introuvables: {sorted(not_found)}")

    messages_created = []
    for username, uid in username_map.items():
        # Gérer le renvoi : préfixer le titre si is_resend
        title = payload.title
        if getattr(payload, 'is_resend', False) and not title.startswith("[Renvoi] "):
            title = f"[Renvoi] {title}"

        msg = UserMessage(
            sender_type="app",
            sender_id=entity.id,
            sender_app_slug=sender_app_slug,
            target_type="user",
            target_user_id=uid,
            target_username=username,
            priority=payload.priority.value,
            title=title,
            body=payload.body,
            action_url=resolved_action_url,
            action_label=payload.action_label,
            expires_at=payload.expires_at,
            is_resend=getattr(payload, 'is_resend', False) or False,
            original_message_id=getattr(payload, 'original_hub_message_id', None),
            status="active",
            total_targets=1,
        )
        db.add(msg)
        await db.flush()
        messages_created.append(msg)
        # SSE ciblé
        await _send_user_message_sse(uid, _message_to_brief(msg))

    await db.commit()

    _notify_message("messages_updated", {"action": "m2m_sent", "app": sender_app_slug, "count": len(messages_created)})

    return {
        "sent": len(messages_created),
        "message_ids": [m.id for m in messages_created],
        "sender_app": sender_app_slug,
    }


# =============================================================================
# ENDPOINTS M2M — Synchronisation (pour exemple-app)
# =============================================================================

@router.get("/m2m/sync", response_model=MessageSyncResponse)
async def m2m_sync_messages(
    since: str = Query(..., description="ISO datetime — messages créés après cette date"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=10, le=500),
    include_receipts: bool = Query(False),
    entity: Union[User, ServiceAccount] = Depends(get_current_entity),
    db: AsyncSession = Depends(get_db)
):
    """
    Synchronisation incrémentale des messages pour les service accounts.
    Retourne les messages créés après `since`, paginés.
    """
    if not isinstance(entity, ServiceAccount):
        raise HTTPException(status_code=403, detail="Cet endpoint est réservé aux service accounts (M2M)")

    # Parser la date since
    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        # Supprimer le tzinfo pour rester compatible avec les colonnes TIMESTAMP WITHOUT TIME ZONE
        since_dt = since_dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Format de date invalide (ISO 8601 attendu)")

    # Comptage total
    count_query = select(func.count(UserMessage.id)).where(UserMessage.created_at > since_dt)
    total = await db.scalar(count_query) or 0
    pages = max(1, (total + per_page - 1) // per_page)

    # Messages paginés
    offset = (page - 1) * per_page
    result = await db.execute(
        select(UserMessage)
        .where(UserMessage.created_at > since_dt)
        .order_by(UserMessage.created_at.asc())
        .offset(offset).limit(per_page)
    )
    messages = result.scalars().all()

    # Résoudre les usernames manquants (fallback pour messages pré-migration)
    user_ids = set()
    for m in messages:
        if m.sender_id:
            user_ids.add(m.sender_id)
        if m.target_user_id and not m.target_username:
            user_ids.add(m.target_user_id)
    users_map = await _build_users_map(db, user_ids)

    # Charger les read_receipts si demandé (read_at IS NOT NULL pour compat)
    receipts_by_msg = {}
    if include_receipts and messages:
        msg_ids = [m.id for m in messages]
        rc_result = await db.execute(
            select(MessageReadReceipt)
            .where(and_(
                MessageReadReceipt.message_id.in_(msg_ids),
                MessageReadReceipt.read_at.isnot(None),
            ))
        )
        all_receipts = rc_result.scalars().all()
        # Résoudre les usernames des receipts
        receipt_user_ids = {r.user_id for r in all_receipts}
        receipt_users_map = await _build_users_map(db, receipt_user_ids)
        for r in all_receipts:
            receipts_by_msg.setdefault(r.message_id, []).append(
                MessageReadReceiptResponse(
                    user_id=r.user_id,
                    username=receipt_users_map.get(r.user_id, "?"),
                    read_at=r.read_at,
                    delivered_at=r.delivered_at,
                    clicked_at=r.clicked_at,
                )
            )

    # Comptage des lectures par message (read_at IS NOT NULL)
    read_counts = {}
    if messages:
        msg_ids = [m.id for m in messages]
        rc_count_result = await db.execute(
            select(
                MessageReadReceipt.message_id,
                func.count(MessageReadReceipt.id)
            ).where(and_(
                MessageReadReceipt.message_id.in_(msg_ids),
                MessageReadReceipt.read_at.isnot(None),
            )).group_by(MessageReadReceipt.message_id)
        )
        read_counts = {row[0]: row[1] for row in rc_count_result.all()}

    total_users = await db.scalar(select(func.count(User.id))) or 0

    items = []
    for m in messages:
        items.append(MessageSyncItem(
            id=m.id,
            sender_type=m.sender_type,
            sender_id=m.sender_id,
            sender_app_slug=m.sender_app_slug,
            sender_username=users_map.get(m.sender_id),
            target_type=m.target_type,
            target_user_id=m.target_user_id,
            target_username=m.target_username or users_map.get(m.target_user_id),
            priority=m.priority,
            title=m.title,
            body=m.body,
            action_url=m.action_url,
            action_label=m.action_label,
            expires_at=m.expires_at,
            created_at=m.created_at,
            is_resend=m.is_resend or False,
            original_message_id=m.original_message_id,
            read_count=read_counts.get(m.id, 0),
            total_targets=m.total_targets if (m.total_targets and m.total_targets > 0) else (
                total_users if m.target_type == "broadcast" else 1
            ),
            read_receipts=receipts_by_msg.get(m.id, []),
            status=m.status or "active",
            archived_at=m.archived_at,
            delivered_count=m.delivered_count or 0,
            click_count=m.click_count or 0,
        ))

    return MessageSyncResponse(
        messages=items,
        total=total,
        page=page,
        pages=pages,
        sync_timestamp=utcnow().isoformat(),
    )


@router.get("/m2m/receipts", response_model=ReceiptSyncResponse)
async def m2m_sync_receipts(
    since: str = Query(..., description="ISO datetime — accusés créés après cette date"),
    page: int = Query(1, ge=1),
    per_page: int = Query(500, ge=10, le=500),
    entity: Union[User, ServiceAccount] = Depends(get_current_entity),
    db: AsyncSession = Depends(get_db)
):
    """
    Synchronisation incrémentale des accusés de lecture pour les service accounts.
    """
    if not isinstance(entity, ServiceAccount):
        raise HTTPException(status_code=403, detail="Cet endpoint est réservé aux service accounts (M2M)")

    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        # Supprimer le tzinfo pour rester compatible avec les colonnes TIMESTAMP WITHOUT TIME ZONE
        since_dt = since_dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Format de date invalide (ISO 8601 attendu)")

    count_query = select(func.count(MessageReadReceipt.id)).where(and_(
        MessageReadReceipt.read_at > since_dt,
        MessageReadReceipt.read_at.isnot(None),
    ))
    total = await db.scalar(count_query) or 0
    pages = max(1, (total + per_page - 1) // per_page)

    offset = (page - 1) * per_page
    result = await db.execute(
        select(MessageReadReceipt)
        .where(and_(
            MessageReadReceipt.read_at > since_dt,
            MessageReadReceipt.read_at.isnot(None),
        ))
        .order_by(MessageReadReceipt.read_at.asc())
        .offset(offset).limit(per_page)
    )
    receipts = result.scalars().all()

    # Résoudre les usernames
    receipt_user_ids = {r.user_id for r in receipts}
    users_map = await _build_users_map(db, receipt_user_ids)

    items = [
        ReceiptSyncItem(
            message_id=r.message_id,
            user_id=r.user_id,
            username=users_map.get(r.user_id),
            read_at=r.read_at,
            delivered_at=r.delivered_at,
            clicked_at=r.clicked_at,
        )
        for r in receipts
    ]

    return ReceiptSyncResponse(
        receipts=items,
        total=total,
        page=page,
        pages=pages,
        sync_timestamp=utcnow().isoformat(),
    )


@router.get("/m2m/stats")
async def m2m_message_stats(
    hours: int = Query(24, ge=1, le=8760),
    entity: Union[User, ServiceAccount] = Depends(get_current_entity),
    db: AsyncSession = Depends(get_db)
):
    """
    Statistiques agrégées des messages pour les service accounts.
    """
    if not isinstance(entity, ServiceAccount):
        raise HTTPException(status_code=403, detail="Cet endpoint est réservé aux service accounts (M2M)")

    since = utcnow() - timedelta(hours=hours)
    where_clause = UserMessage.created_at >= since

    total = await db.scalar(select(func.count(UserMessage.id)).where(where_clause)) or 0

    prio_result = await db.execute(
        select(UserMessage.priority, func.count(UserMessage.id))
        .where(where_clause)
        .group_by(UserMessage.priority)
    )
    by_priority = {row[0]: row[1] for row in prio_result.all()}

    st_result = await db.execute(
        select(UserMessage.sender_type, func.count(UserMessage.id))
        .where(where_clause)
        .group_by(UserMessage.sender_type)
    )
    by_sender_type = {row[0]: row[1] for row in st_result.all()}

    app_result = await db.execute(
        select(UserMessage.sender_app_slug, func.count(UserMessage.id))
        .where(and_(where_clause, UserMessage.sender_app_slug.isnot(None)))
        .group_by(UserMessage.sender_app_slug)
    )
    by_app = {row[0]: row[1] for row in app_result.all()}

    return {
        "period_hours": hours,
        "total": total,
        "by_priority": by_priority,
        "by_sender_type": by_sender_type,
        "by_app": by_app,
    }


@router.get("/m2m/{message_id}", response_model=MessageDetailSyncResponse)
async def m2m_get_message_detail(
    message_id: int,
    entity: Union[User, ServiceAccount] = Depends(get_current_entity),
    db: AsyncSession = Depends(get_db)
):
    """
    Détail complet d'un message + accusés de lecture pour les service accounts.
    """
    if not isinstance(entity, ServiceAccount):
        raise HTTPException(status_code=403, detail="Cet endpoint est réservé aux service accounts (M2M)")

    msg = await db.get(UserMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message introuvable")

    # Accusés de lecture
    receipts_result = await db.execute(
        select(MessageReadReceipt)
        .where(MessageReadReceipt.message_id == message_id)
        .order_by(MessageReadReceipt.read_at.desc())
    )
    receipts = receipts_result.scalars().all()

    # Résoudre usernames
    user_ids = set()
    if msg.sender_id:
        user_ids.add(msg.sender_id)
    if msg.target_user_id and not msg.target_username:
        user_ids.add(msg.target_user_id)
    for r in receipts:
        user_ids.add(r.user_id)
    users_map = await _build_users_map(db, user_ids)

    total_users = await db.scalar(select(func.count(User.id))) or 0

    message_item = MessageSyncItem(
        id=msg.id,
        sender_type=msg.sender_type,
        sender_id=msg.sender_id,
        sender_app_slug=msg.sender_app_slug,
        sender_username=users_map.get(msg.sender_id),
        target_type=msg.target_type,
        target_user_id=msg.target_user_id,
        target_username=msg.target_username or users_map.get(msg.target_user_id),
        priority=msg.priority,
        title=msg.title,
        body=msg.body,
        action_url=msg.action_url,
        action_label=msg.action_label,
        expires_at=msg.expires_at,
        created_at=msg.created_at,
        is_resend=msg.is_resend or False,
        original_message_id=msg.original_message_id,
        read_count=len([r for r in receipts if r.read_at is not None]),
        total_targets=msg.total_targets if (msg.total_targets and msg.total_targets > 0) else (
            total_users if msg.target_type == "broadcast" else 1
        ),
        status=msg.status or "active",
        archived_at=msg.archived_at,
        delivered_count=msg.delivered_count or 0,
        click_count=msg.click_count or 0,
    )

    receipt_items = [
        ReceiptSyncItem(
            message_id=r.message_id,
            user_id=r.user_id,
            username=users_map.get(r.user_id),
            read_at=r.read_at,
            delivered_at=r.delivered_at,
            clicked_at=r.clicked_at,
        )
        for r in receipts
        if r.read_at is not None
    ]

    return MessageDetailSyncResponse(
        message=message_item,
        read_receipts=receipt_items,
    )
