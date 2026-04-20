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
import csv
import io
import time
import logging

from app.database import get_db
from app.models import User, UserMessage, MessageReadReceipt, ServiceAccount, utcnow
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


def _message_to_brief(msg: UserMessage, is_read: bool = False) -> dict:
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
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Liste paginée des messages avec filtres (admin)."""
    query = select(UserMessage)
    count_query = select(func.count(UserMessage.id))

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
        since = utcnow() - timedelta(hours=hours)
        filters.append(UserMessage.created_at >= since)

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

    # Compter les lectures pour chaque message
    msg_ids = [m.id for m in messages]
    read_counts = {}
    if msg_ids:
        rc_result = await db.execute(
            select(
                MessageReadReceipt.message_id,
                func.count(MessageReadReceipt.id)
            ).where(MessageReadReceipt.message_id.in_(msg_ids))
            .group_by(MessageReadReceipt.message_id)
        )
        read_counts = {row[0]: row[1] for row in rc_result.all()}

    # Compter les cibles totales par message (broadcast = total users)
    total_users = await db.scalar(select(func.count(User.id))) or 0

    items = []
    for m in messages:
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
            read_count=read_counts.get(m.id, 0),
            total_targets=total_users if m.target_type == "broadcast" else 1,
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

    return {
        "period_hours": hours,
        "total": total,
        "by_priority": by_priority,
        "by_sender_type": by_sender_type,
        "by_app": by_app,
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Messages de l'utilisateur connecté (ciblés + broadcasts, non expirés)."""
    now = utcnow()

    # Messages ciblés pour cet utilisateur OU broadcasts
    query = (
        select(UserMessage)
        .where(
            or_(
                UserMessage.target_user_id == current_user.id,
                UserMessage.target_type == "broadcast",
            )
        )
        .where(
            or_(
                UserMessage.expires_at.is_(None),
                UserMessage.expires_at > now,
            )
        )
        .order_by(desc(UserMessage.created_at))
        .limit(100)
    )
    result = await db.execute(query)
    messages = result.scalars().all()

    # Récupérer les accusés de lecture de cet utilisateur
    msg_ids = [m.id for m in messages]
    read_msg_ids = set()
    if msg_ids:
        rr_result = await db.execute(
            select(MessageReadReceipt.message_id)
            .where(and_(
                MessageReadReceipt.message_id.in_(msg_ids),
                MessageReadReceipt.user_id == current_user.id,
            ))
        )
        read_msg_ids = {row[0] for row in rr_result.all()}

    return [
        MessageBrief(**_message_to_brief(m, is_read=(m.id in read_msg_ids)))
        for m in messages
    ]


@router.get("/me/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Nombre de messages non lus de l'utilisateur connecté."""
    now = utcnow()

    # Sous-requête : IDs des messages déjà lus
    read_subq = (
        select(MessageReadReceipt.message_id)
        .where(MessageReadReceipt.user_id == current_user.id)
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

    # Messages non lus de cet utilisateur
    read_subq = (
        select(MessageReadReceipt.message_id)
        .where(MessageReadReceipt.user_id == current_user.id)
        .scalar_subquery()
    )

    result = await db.execute(
        select(UserMessage.id)
        .where(
            or_(
                UserMessage.target_user_id == current_user.id,
                UserMessage.target_type == "broadcast",
            )
        )
        .where(
            or_(
                UserMessage.expires_at.is_(None),
                UserMessage.expires_at > now,
            )
        )
        .where(UserMessage.id.notin_(read_subq))
    )
    unread_ids = [row[0] for row in result.all()]

    if unread_ids:
        for mid in unread_ids:
            db.add(MessageReadReceipt(
                message_id=mid,
                user_id=current_user.id,
            ))
        await db.commit()

    return {"marked_read": len(unread_ids)}


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

    # Vérifier que l'utilisateur est bien destinataire
    if msg.target_type == "user" and msg.target_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Ce message ne vous est pas destiné")

    # Vérifier si déjà lu
    existing = await db.execute(
        select(MessageReadReceipt).where(and_(
            MessageReadReceipt.message_id == message_id,
            MessageReadReceipt.user_id == current_user.id,
        ))
    )
    if existing.scalar_one_or_none():
        return {"status": "already_read"}

    receipt = MessageReadReceipt(
        message_id=message_id,
        user_id=current_user.id,
    )
    db.add(receipt)
    await db.commit()

    return {"status": "read", "message_id": message_id}


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
            read_count=len(receipts),
            total_targets=total_users if msg.target_type == "broadcast" else 1,
        ),
        "read_receipts": [
            {
                "user_id": r.user_id,
                "username": users_map.get(r.user_id, "?"),
                "read_at": r.read_at.isoformat() if r.read_at else None,
            }
            for r in receipts
        ],
    }


@router.delete("/")
async def purge_messages(
    hours: Optional[int] = Query(None, ge=1, le=8760),
    priority: Optional[str] = Query(None),
    sender_app: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Purge des messages avec filtres optionnels (admin)."""
    from app.routes.audit import log_action

    stmt = delete(UserMessage)
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
        stmt = stmt.where(and_(*filters))

    result = await db.execute(stmt)
    deleted = result.rowcount
    await db.commit()

    await log_action(
        db, current_user.id, "purge_messages",
        details=f"deleted={deleted}, hours={hours}, priority={priority}, app={sender_app}",
        notify_sse=False
    )
    await db.commit()

    _notify_message("messages_updated", {"action": "purged", "count": deleted})

    return {"deleted": deleted, "message": f"{deleted} message(s) supprimé(s)"}


@router.delete("/batch")
async def delete_messages_batch(
    payload: dict,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Supprime une liste de messages par leurs IDs (admin)."""
    from app.routes.audit import log_action

    ids = payload.get("ids", [])
    if not ids or not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="Liste d'IDs requise")
    if len(ids) > 500:
        raise HTTPException(status_code=400, detail="Maximum 500 messages par requête")

    # Valider que ce sont bien des entiers
    try:
        ids = [int(i) for i in ids]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="IDs invalides")

    result = await db.execute(
        delete(UserMessage).where(UserMessage.id.in_(ids))
    )
    deleted = result.rowcount
    await db.commit()

    await log_action(
        db, current_user.id, "delete_messages_batch",
        details=f"deleted={deleted}, ids={ids[:10]}{'...' if len(ids) > 10 else ''}",
        notify_sse=False
    )
    await db.commit()

    _notify_message("messages_updated", {"action": "deleted", "count": deleted})

    return {"deleted": deleted, "message": f"{deleted} message(s) supprimé(s)"}


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
            action_url=payload.action_url,
            action_label=payload.action_label,
            expires_at=payload.expires_at,
            is_resend=getattr(payload, 'is_resend', False) or False,
            original_message_id=getattr(payload, 'original_hub_message_id', None),
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

    # Charger les read_receipts si demandé
    receipts_by_msg = {}
    if include_receipts and messages:
        msg_ids = [m.id for m in messages]
        rc_result = await db.execute(
            select(MessageReadReceipt)
            .where(MessageReadReceipt.message_id.in_(msg_ids))
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
                )
            )

    # Comptage des lectures par message
    read_counts = {}
    if messages:
        msg_ids = [m.id for m in messages]
        rc_count_result = await db.execute(
            select(
                MessageReadReceipt.message_id,
                func.count(MessageReadReceipt.id)
            ).where(MessageReadReceipt.message_id.in_(msg_ids))
            .group_by(MessageReadReceipt.message_id)
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
            total_targets=total_users if m.target_type == "broadcast" else 1,
            read_receipts=receipts_by_msg.get(m.id, []),
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

    count_query = select(func.count(MessageReadReceipt.id)).where(
        MessageReadReceipt.read_at > since_dt
    )
    total = await db.scalar(count_query) or 0
    pages = max(1, (total + per_page - 1) // per_page)

    offset = (page - 1) * per_page
    result = await db.execute(
        select(MessageReadReceipt)
        .where(MessageReadReceipt.read_at > since_dt)
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
        read_count=len(receipts),
        total_targets=total_users if msg.target_type == "broadcast" else 1,
    )

    receipt_items = [
        ReceiptSyncItem(
            message_id=r.message_id,
            user_id=r.user_id,
            username=users_map.get(r.user_id),
            read_at=r.read_at,
        )
        for r in receipts
    ]

    return MessageDetailSyncResponse(
        message=message_item,
        read_receipts=receipt_items,
    )
