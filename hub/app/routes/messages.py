# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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
from sqlalchemy.orm import selectinload, aliased
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Union
from urllib.parse import urlparse, urljoin
import csv
import io
import json
import time
import logging

from app.database import get_db
from app.models import User, UserMessage, MessageReadReceipt, MessageTemplate, ServiceAccount, Application, AppSetting, utcnow
from app.auth import get_current_user, get_current_entity, get_current_admin, get_admin_or_admin_sa, get_settings
from app.schemas import (
    MessageCreate, MessageM2MCreate, MessageBadgePrioritiesUpdate, MessageResponse, MessageBrief,
    PaginatedMessagesResponse, PaginatedMyMessages,
    MessageSyncItem, MessageSyncResponse,
    ReceiptSyncItem, ReceiptSyncResponse,
    MessageDetailSyncResponse, MessageReadReceiptResponse,
    MessageTemplateCreate, MessageTemplateUpdate, MessageTemplateResponse,
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
    Envoie un message (admin). Cible a des utilisateurs ou broadcast.
    Supporte template_id (pre-remplissage) et scheduled_at (envoi differe).
    """
    from app.routes.audit import log_action

    # Pre-remplissage depuis template si fourni
    if payload.template_id:
        template = await db.get(MessageTemplate, payload.template_id)
        if template:
            if not payload.body and template.body_template:
                payload.body = template.body_template
            if not payload.action_url and template.default_action_url:
                payload.action_url = template.default_action_url
            if not payload.action_label and template.default_action_label:
                payload.action_label = template.default_action_label

    if payload.target_type == "user":
        if not payload.target_user_ids or len(payload.target_user_ids) == 0:
            raise HTTPException(status_code=400, detail="target_user_ids requis pour un message cible")
        result = await db.execute(
            select(User.id).where(User.id.in_(payload.target_user_ids))
        )
        valid_ids = {row[0] for row in result.all()}
        invalid_ids = set(payload.target_user_ids) - valid_ids
        if invalid_ids:
            raise HTTPException(status_code=400, detail=f"Utilisateurs introuvables: {sorted(invalid_ids)}")

    # Determiner le statut initial : scheduled si envoi differe
    now = utcnow()
    is_scheduled = False
    if payload.scheduled_at is not None:
        scheduled_dt = payload.scheduled_at.replace(tzinfo=None) if payload.scheduled_at.tzinfo else payload.scheduled_at
        is_scheduled = scheduled_dt > now
    initial_status = "scheduled" if is_scheduled else "active"

    messages_created = []

    if payload.target_type == "broadcast":
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
            scheduled_at=payload.scheduled_at,
            status=initial_status,
            total_targets=total_targets if not is_scheduled else 0,
        )
        db.add(msg)
        await db.flush()
        messages_created.append(msg)

        if not is_scheduled:
            from app.routes.events import broadcast_event
            await broadcast_event("user_message", _message_to_brief(msg))

    else:
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
                scheduled_at=payload.scheduled_at,
                status=initial_status,
                total_targets=1 if not is_scheduled else 0,
            )
            db.add(msg)
            await db.flush()
            messages_created.append(msg)
            if not is_scheduled:
                await _send_user_message_sse(uid, _message_to_brief(msg))

    await db.commit()

    await log_action(
        db, current_user.id, "send_message",
        target=f"{payload.target_type}:{len(messages_created)} message(s)" + (" [differe]" if is_scheduled else ""),
        details=f"title={payload.title}, priority={payload.priority.value}" + (f", scheduled_at={payload.scheduled_at.isoformat()}" if is_scheduled else ""),
        notify_sse=False
    )
    await db.commit()

    if not is_scheduled:
        _notify_message("messages_updated", {"action": "sent", "count": len(messages_created)})

    return {
        "sent": 0 if is_scheduled else len(messages_created),
        "scheduled": is_scheduled,
        "scheduled_at": payload.scheduled_at.isoformat() if payload.scheduled_at else None,
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
    group_similar: bool = Query(False, description="Regrouper les messages identiques (titre+priorité+app) : 1 représentant par groupe + occurrences"),
    current_user = Depends(get_admin_or_admin_sa),
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

    if group_similar:
        # 1 ligne par groupe (titre, priorité, app) : représentant = plus récent,
        # occurrences = taille du groupe. Même pattern window que GET /me (Task 2),
        # sans jointure receipts (vue système admin).
        grp = func.coalesce(UserMessage.sender_app_slug, UserMessage.sender_type)
        PART = (UserMessage.title, UserMessage.priority, grp)
        rn = func.row_number().over(
            partition_by=PART,
            order_by=(desc(UserMessage.created_at), desc(UserMessage.id)),
        ).label("rn")
        occ = func.count().over(partition_by=PART).label("occurrences")
        subq = select(UserMessage, rn, occ)
        if filters:
            subq = subq.where(and_(*filters))
        subq = subq.subquery()
        um = aliased(UserMessage, subq)
        total = await db.scalar(
            select(func.count()).select_from(subq).where(subq.c.rn == 1)
        ) or 0
        pages = max(1, (total + per_page - 1) // per_page)
        offset = (page - 1) * per_page
        rows = (await db.execute(
            select(um, subq.c.occurrences)
            .where(subq.c.rn == 1)
            .order_by(desc(subq.c.created_at), desc(subq.c.id))
            .offset(offset).limit(per_page)
        )).all()
        messages = [row[0] for row in rows]
        occurrences_by_id = {row[0].id: row.occurrences for row in rows}
    else:
        total = await db.scalar(count_query) or 0
        pages = max(1, (total + per_page - 1) // per_page)

        offset = (page - 1) * per_page
        result = await db.execute(
            query.order_by(desc(UserMessage.created_at))
            .offset(offset).limit(per_page)
        )
        messages = result.scalars().all()
        occurrences_by_id = {}

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
            occurrences=occurrences_by_id.get(m.id),
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
    current_user = Depends(get_admin_or_admin_sa),
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


@router.get("/purge-preview")
async def purge_preview(
    older_than_days: int = Query(90, ge=1, le=3650, description="Seuil « vieux messages » en jours"),
    exclude_urgent_unread: bool = Query(False, description="Exclure les urgents jamais lus des comptes (même garde que DELETE /)"),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Aperçu des purges possibles : comptes par cas (lus / expirés / vieux / par app).

    Tous les comptes portent sur les messages ACTIFS, avec le même garde
    exclude_urgent_unread que DELETE / — les comptes affichés au modal doivent
    correspondre exactement à ce que la purge fera. per_app = top 5 apps émettrices.
    """
    now = utcnow()
    ACTIVE = UserMessage.status == "active"

    # Prédicats imposés (partagés avec DELETE /) :
    #   « lu par tous » : total_targets > 0 ET read_count >= total_targets
    READ_ALL = and_(UserMessage.total_targets > 0, UserMessage.read_count >= UserMessage.total_targets)
    #   « expiré » : date passée ET toujours actif
    EXPIRED = and_(UserMessage.expires_at.isnot(None), UserMessage.expires_at <= now, UserMessage.status == "active")
    #   garde urgent jamais lu (même prédicat que DELETE /exclude_urgent_unread)
    BASE = [ACTIVE]
    if exclude_urgent_unread:
        BASE.append(or_(UserMessage.priority != "urgent", UserMessage.read_count >= 1))

    read = await db.scalar(
        select(func.count(UserMessage.id)).where(and_(*BASE, READ_ALL))
    ) or 0
    expired = await db.scalar(
        select(func.count(UserMessage.id)).where(EXPIRED, *BASE)
    ) or 0
    older = await db.scalar(
        select(func.count(UserMessage.id)).where(and_(
            *BASE,
            UserMessage.created_at < now - timedelta(days=older_than_days),
        ))
    ) or 0

    app_rows = await db.execute(
        select(UserMessage.sender_app_slug, func.count(UserMessage.id))
        .where(and_(*BASE, UserMessage.sender_app_slug.isnot(None)))
        .group_by(UserMessage.sender_app_slug)
        .order_by(desc(func.count(UserMessage.id)))
        .limit(5)
    )
    per_app = [{"sender_app_slug": slug, "count": cnt} for slug, cnt in app_rows.all()]

    return {
        "read": read,
        "expired": expired,
        "older_than_days": {"days": older_than_days, "count": older},
        "per_app": per_app,
    }


# =============================================================================
# PRÉFÉRENCE BADGE NAVBAR (globale à l'instance)
# =============================================================================

BADGE_PRIORITIES_KEY = "messages_badge_priorities"
VALID_PRIORITIES = ("info", "warning", "urgent")


@router.get("/badge-priorities")
async def get_badge_priorities(
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Priorités comptées dans le badge messages de la navbar (préférence globale).
    Défaut : toutes. [] = badge masqué. Valeur absente/corrompue → repli sur
    toutes ; liste contenant des inconnues → projection sur les valides ([] si
    aucune)."""
    row = await db.get(AppSetting, BADGE_PRIORITIES_KEY)
    parsed = list(VALID_PRIORITIES)
    if row is not None:
        try:
            value = json.loads(row.value)
            if isinstance(value, list):
                parsed = value
        except (TypeError, ValueError):
            pass  # repli sur toutes
    return {"priorities": [p for p in VALID_PRIORITIES if p in parsed]}


@router.put("/badge-priorities")
async def update_badge_priorities(
    body: MessageBadgePrioritiesUpdate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Met à jour la préférence globale puis notifie : messages_updated (badges)
    + settings_updated (resync de la préférence chez les autres admins)."""
    invalid = [p for p in body.priorities if p not in VALID_PRIORITIES]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Priorités invalides : {', '.join(sorted(set(invalid)))}"
        )
    priorities = [p for p in VALID_PRIORITIES if p in set(body.priorities)]

    row = await db.get(AppSetting, BADGE_PRIORITIES_KEY)
    if row is None:
        row = AppSetting(key=BADGE_PRIORITIES_KEY, value=json.dumps(priorities),
                         updated_by_id=current_user.id)
        db.add(row)
    else:
        row.value = json.dumps(priorities)
        row.updated_by_id = current_user.id
    await db.commit()

    _notify_message()  # badge recalculé partout (cooldown 0,5 s existant)
    try:
        from app.routes.events import notify_event
        notify_event("settings_updated", {
            "key": BADGE_PRIORITIES_KEY,
            "value": json.dumps(priorities),
        })
    except ImportError:
        pass
    return {"priorities": priorities}


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
    page: int = Query(1, ge=1, description="Numéro de page (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Taille de page"),
    unread: bool = Query(False, description="Uniquement les non-lus"),
    priority: Optional[str] = Query(None, description="Filtrer par priorité (info|warning|urgent)"),
    sender_app: Optional[str] = Query(None, description="Filtrer par slug de l'app source"),
    search: Optional[str] = Query(None, description="Recherche dans titre + corps"),
    group_similar: bool = Query(False, description="Regrouper les messages identiques (titre+priorité+app)"),
    include_archived: bool = Query(False, description="Inclure les messages archivés par l'admin"),
    include_expired: bool = Query(True, description="Inclure les messages expirés"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Messages de l'utilisateur connecté (ciblés + broadcasts), TOUJOURS paginés.

    Réponse enveloppée (PaginatedMyMessages). Marque automatiquement les
    messages de la page comme "livrés" (delivered_at), pour le tracking
    d'engagement (rentre dans la même transaction). Les messages masqués par
    le destinataire (receipt.hidden_at) sont exclus du résultat ET de
    unread_total. group_similar regroupe les messages identiques (titre +
    priorité + app) : un représentant par groupe (le plus récent) + occurrences.
    """
    now = utcnow()

    # Jointure receipts de cet utilisateur (alias réutilisé par les Tasks 2-4)
    rcpt = aliased(MessageReadReceipt)
    RCPT_JOIN = and_(rcpt.message_id == UserMessage.id, rcpt.user_id == current_user.id)
    HIDDEN_OK = or_(rcpt.id.is_(None), rcpt.hidden_at.is_(None))
    UNREAD_COND = or_(rcpt.id.is_(None), rcpt.read_at.is_(None))

    base_filters = [
        or_(
            UserMessage.target_user_id == current_user.id,
            UserMessage.target_type == "broadcast",
        )
    ]
    if not include_archived:
        base_filters.append(UserMessage.status == "active")
    if not include_expired:
        base_filters.append(or_(UserMessage.expires_at.is_(None), UserMessage.expires_at > now))

    extra_filters = []
    if unread:
        extra_filters.append(UNREAD_COND)
    if priority:
        extra_filters.append(UserMessage.priority == priority)
    if sender_app:
        extra_filters.append(UserMessage.sender_app_slug == sender_app)
    if search:
        like = f"%{search}%"
        extra_filters.append(or_(UserMessage.title.ilike(like), UserMessage.body.ilike(like)))

    conditions = base_filters + [HIDDEN_OK] + extra_filters

    if group_similar:
        # 1 ligne par groupe (titre, priorité, app) : représentant = plus récent,
        # occurrences = taille du groupe. Window functions → portable SQLite/PG.
        grp = func.coalesce(UserMessage.sender_app_slug, UserMessage.sender_type)
        PART = (UserMessage.title, UserMessage.priority, grp)
        rn = func.row_number().over(
            partition_by=PART,
            order_by=(desc(UserMessage.created_at), desc(UserMessage.id)),
        ).label("rn")
        occ = func.count().over(partition_by=PART).label("occurrences")
        subq = (
            select(UserMessage, rcpt.id.label("rcpt_id"), rn, occ)
            .outerjoin(rcpt, RCPT_JOIN)
            .where(and_(*conditions))
            .subquery()
        )
        um = aliased(UserMessage, subq)
        rows = (await db.execute(
            select(um, subq.c.occurrences, subq.c.rcpt_id)
            .where(subq.c.rn == 1)
            .order_by(subq.c.created_at.desc(), subq.c.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).all()
        total = await db.scalar(
            select(func.count()).select_from(subq).where(subq.c.rn == 1)
        ) or 0

        # Receipts des représentants de la page (is_read / auto-delivery) :
        # la jointure vit dans la subquery → re-fetch ORM par id.
        rcpt_ids = [row.rcpt_id for row in rows if row.rcpt_id is not None]
        receipts_by_id: dict[int, MessageReadReceipt] = {}
        if rcpt_ids:
            res = await db.execute(
                select(MessageReadReceipt).where(MessageReadReceipt.id.in_(rcpt_ids))
            )
            receipts_by_id = {r.id: r for r in res.scalars().all()}
        page_items = [(row[0], receipts_by_id.get(row.rcpt_id), row.occurrences) for row in rows]
    else:
        rows = (await db.execute(
            select(UserMessage, rcpt)
            .outerjoin(rcpt, RCPT_JOIN)
            .where(and_(*conditions))
            .order_by(desc(UserMessage.created_at), desc(UserMessage.id))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).all()
        page_items = [(row[0], row[1], None) for row in rows]
        total = await db.scalar(
            select(func.count())
            .select_from(UserMessage)
            .outerjoin(rcpt, RCPT_JOIN)
            .where(and_(*conditions))
        ) or 0

    # unread_total (badge) : base + cachés exclus, SANS les filtres de recherche
    unread_total = await db.scalar(
        select(func.count())
        .select_from(UserMessage)
        .outerjoin(rcpt, RCPT_JOIN)
        .where(and_(*base_filters, HIDDEN_OK, UNREAD_COND))
    ) or 0

    # Auto-delivery : pour chaque message de la page non encore livré, créer ou
    # mettre à jour le receipt avec delivered_at + incrémenter delivered_count.
    messages: list[MessageBrief] = []
    new_deliveries = 0
    for m, r, occurrences in page_items:
        if r is None:
            r = MessageReadReceipt(
                message_id=m.id,
                user_id=current_user.id,
                delivered_at=now,
            )
            db.add(r)
            m.delivered_count = (m.delivered_count or 0) + 1
            new_deliveries += 1
        elif r.delivered_at is None:
            r.delivered_at = now
            m.delivered_count = (m.delivered_count or 0) + 1
            new_deliveries += 1
        brief_data = _message_to_brief(m, is_read=(r.read_at is not None), receipt=r)
        brief_data["occurrences"] = occurrences
        messages.append(MessageBrief(**brief_data))
    if new_deliveries:
        await db.commit()

    pages = (total + page_size - 1) // page_size
    return PaginatedMyMessages(
        messages=messages,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        unread_total=unread_total,
    )


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
    # Sous-requête : IDs des messages masqués par l'utilisateur (hidden_at) —
    # un masqué non lu ne doit pas compter dans la cloche (alignement avec
    # unread_total de GET /me).
    hidden_subq = (
        select(MessageReadReceipt.message_id)
        .where(and_(
            MessageReadReceipt.user_id == current_user.id,
            MessageReadReceipt.hidden_at.isnot(None),
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
        .where(UserMessage.id.notin_(hidden_subq))
    ) or 0

    return {"count": count}


# ----- Suppression côté destinataire (avant /{message_id}/... : pas de shadowing) -----

async def _hide_message_for_user(db: AsyncSession, message_id: int, user_id: int) -> Optional[str]:
    """Masque un message pour un destinataire (receipt.hidden_at, suppression réelle côté user).

    Le message reste visible des autres destinataires et de l'admin. Pattern
    post-incident 3.83.3 : select + scalars().first() + upsert explicite (unicité
    (message_id, user_id) via uq_message_read_receipts_message_user).

    Retourne "hidden" | "already_hidden" | None (message inexistant ou non visible du user).
    """
    msg = await db.get(UserMessage, message_id)
    if not msg:
        return None
    # Visible seulement si ciblé sur ce user ou broadcast
    if msg.target_type != "broadcast" and msg.target_user_id != user_id:
        return None

    now = utcnow()
    existing = await db.execute(
        select(MessageReadReceipt).where(and_(
            MessageReadReceipt.message_id == message_id,
            MessageReadReceipt.user_id == user_id,
        ))
    )
    receipt = existing.scalars().first()

    if receipt is None:
        db.add(MessageReadReceipt(
            message_id=message_id,
            user_id=user_id,
            delivered_at=now,
            hidden_at=now,
        ))
        msg.delivered_count = (msg.delivered_count or 0) + 1
        return "hidden"
    if receipt.hidden_at is not None:
        return "already_hidden"
    receipt.hidden_at = now
    return "hidden"


@router.delete("/me/{message_id}")
async def hide_my_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Suppression côté destinataire : masque le message (receipt.hidden_at).

    Idempotent : un message déjà masqué retourne "already_hidden".
    """
    result = await _hide_message_for_user(db, message_id, current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Message introuvable")
    await db.commit()
    return {"status": result, "message_id": message_id}


@router.post("/me/delete-batch")
async def hide_my_messages_batch(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Masque une liste de messages pour le destinataire (body dict brut, pattern DELETE /batch).

    Les ids non visibles ou inexistants sont ignorés silencieusement.
    """
    ids = payload.get("ids", [])
    if not ids or not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="Liste d'IDs requise")
    if len(ids) > 500:
        raise HTTPException(status_code=400, detail="Maximum 500 messages par requête")
    try:
        ids = [int(i) for i in ids]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="IDs invalides")

    hidden = 0
    for mid in ids:
        result = await _hide_message_for_user(db, mid, current_user.id)
        if result == "hidden":
            hidden += 1
    await db.commit()
    return {"hidden": hidden}


@router.post("/me/purge-read")
async def purge_my_read_messages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """« Vider les lus » : masque tous les messages déjà lus du destinataire."""
    from sqlalchemy import update as sql_update
    result = await db.execute(
        sql_update(MessageReadReceipt)
        .where(and_(
            MessageReadReceipt.user_id == current_user.id,
            MessageReadReceipt.read_at.isnot(None),
            MessageReadReceipt.hidden_at.is_(None),
        ))
        .values(hidden_at=utcnow())
    )
    hidden = result.rowcount
    await db.commit()
    return {"hidden": hidden}


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
    receipt = existing.scalars().first()

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
    receipt = existing.scalars().first()

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
    receipt = existing.scalars().first()

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

# =============================================================================
# TEMPLATES CRUD (avant /{message_id} pour eviter conflit de route)
# =============================================================================

@router.get("/templates", response_model=List[MessageTemplateResponse])
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(MessageTemplate).order_by(MessageTemplate.name)
    )
    return result.scalars().all()


@router.post("/templates", response_model=MessageTemplateResponse, status_code=201)
async def create_template(
    data: MessageTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    existing = await db.scalar(
        select(MessageTemplate).where(MessageTemplate.name == data.name)
    )
    if existing:
        raise HTTPException(400, "Un template avec ce nom existe deja")

    template = MessageTemplate(
        name=data.name,
        title_template=data.title_template,
        body_template=data.body_template,
        default_priority=data.default_priority.value if hasattr(data.default_priority, 'value') else data.default_priority,
        default_target_type=data.default_target_type.value if hasattr(data.default_target_type, 'value') else data.default_target_type,
        default_expiry_hours=data.default_expiry_hours,
        default_action_url=data.default_action_url,
        default_action_label=data.default_action_label,
        created_by=current_user.username,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.put("/templates/{template_id}", response_model=MessageTemplateResponse)
async def update_template(
    template_id: int,
    data: MessageTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    template = await db.get(MessageTemplate, template_id)
    if not template:
        raise HTTPException(404, "Template introuvable")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if hasattr(value, 'value'):
            value = value.value
        setattr(template, key, value)
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    template = await db.get(MessageTemplate, template_id)
    if not template:
        raise HTTPException(404, "Template introuvable")
    await db.delete(template)
    await db.commit()


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
    older_than_days: Optional[int] = Query(None, ge=1, le=3650, description="Limiter aux messages plus vieux que N jours (créés avant now-Nj) — inverse de `hours`"),
    priority: Optional[str] = Query(None),
    sender_app: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Filtrer par statut (ex: active — épargne les archivés même en hard)"),
    read_only: bool = Query(False, description="Limiter aux messages lus par tous leurs destinataires"),
    expired_only: bool = Query(False, description="Limiter aux messages expirés (date passée, encore actifs)"),
    exclude_urgent_unread: bool = Query(False, description="Exclure les urgents jamais lus"),
    hard: bool = Query(False, description="Si true, suppression définitive (au lieu de simple archivage)"),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Archive (soft-delete) ou supprime définitivement (?hard=true) les messages filtrés (admin)."""
    from app.routes.audit import log_action

    now = utcnow()
    filters = []
    if hours:
        since = now - timedelta(hours=hours)
        filters.append(UserMessage.created_at >= since)
    if older_than_days:
        # « Plus vieux que N jours » : créés AVANT now-Nj (le miroir exact du
        # purge-preview ?older_than_days ; `hours` reste une fenêtre récente)
        filters.append(UserMessage.created_at < now - timedelta(days=older_than_days))
    if priority:
        filters.append(UserMessage.priority == priority)
    if sender_app:
        filters.append(UserMessage.sender_app_slug == sender_app)
    if target_type:
        filters.append(UserMessage.target_type == target_type)
    if status:
        filters.append(UserMessage.status == status)
    # Nouveaux cas de purge (combinables entre eux et avec les filtres ci-dessus) :
    if read_only:
        # « lu par tous » : total_targets > 0 ET read_count >= total_targets
        filters.append(and_(UserMessage.total_targets > 0, UserMessage.read_count >= UserMessage.total_targets))
    if expired_only:
        # « expiré » : date passée ET encore actif
        filters.append(and_(
            UserMessage.expires_at.isnot(None),
            UserMessage.expires_at <= now,
            UserMessage.status == "active",
        ))
    if exclude_urgent_unread:
        # Survie des urgents jamais lus
        filters.append(or_(UserMessage.priority != "urgent", UserMessage.read_count >= 1))

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
        details=f"affected={affected}, hours={hours}, priority={priority}, app={sender_app}, hard={hard}, read_only={read_only}, expired_only={expired_only}, exclude_urgent_unread={exclude_urgent_unread}",
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

    # Résoudre le app_slug via le garde M2M (ownership + mode compat/enforced).
    from app.m2m_authorization import authorize_service_account_slug
    sender_app_slug = await authorize_service_account_slug(
        request=request,
        service_account=entity,
        requested_slug=request.headers.get("x-slug"),
        db=db,
    )

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


# =============================================================================
# JOB : Dispatch des messages differes + SEED templates
# =============================================================================

async def dispatch_scheduled_messages():
    """Envoie les messages dont le scheduled_at est passe (appele periodiquement)."""
    from app.database import async_session_maker
    from app.routes.events import broadcast_event

    async with async_session_maker() as db:
        now = utcnow()
        result = await db.execute(
            select(UserMessage).where(
                UserMessage.status == "scheduled",
                UserMessage.scheduled_at <= now,
            )
        )
        scheduled_messages = result.scalars().all()

        if not scheduled_messages:
            return 0

        for msg in scheduled_messages:
            msg.status = "active"
            msg.total_targets = 0

            if msg.target_type == "broadcast":
                users = await db.execute(select(User))
                user_list = users.scalars().all()
                for user in user_list:
                    receipt = MessageReadReceipt(message_id=msg.id, user_id=user.id)
                    db.add(receipt)
                msg.total_targets = len(user_list)
                await db.flush()
                await broadcast_event("user_message", _message_to_brief(msg))
            elif msg.target_user_id:
                receipt = MessageReadReceipt(message_id=msg.id, user_id=msg.target_user_id)
                db.add(receipt)
                msg.total_targets = 1
                await db.flush()
                await _send_user_message_sse(msg.target_user_id, _message_to_brief(msg))

        await db.commit()

        if scheduled_messages:
            _notify_message("messages_updated", {"action": "dispatched", "count": len(scheduled_messages)})
            logger.info(f"Dispatched {len(scheduled_messages)} scheduled messages")

        return len(scheduled_messages)


async def periodic_scheduled_message_dispatch():
    """Tache periodique qui dispatch les messages differes toutes les 60 secondes."""
    import asyncio
    logger.info("Scheduled message dispatch demarre")
    try:
        while True:
            await asyncio.sleep(60)
            try:
                dispatched = await dispatch_scheduled_messages()
                if dispatched:
                    logger.info(f"{dispatched} message(s) differes envoyes")
            except Exception as e:
                logger.error(f"Erreur dispatch messages differes: {e}", exc_info=True)
    except asyncio.CancelledError:
        logger.info("Scheduled message dispatch arrete")


# =============================================================================
# SEED : Templates par defaut
# =============================================================================

async def seed_default_templates(db: AsyncSession):
    """Cree les templates par defaut s'ils n'existent pas deja."""
    defaults = [
        {
            "name": "Maintenance planifiee",
            "title_template": "Maintenance planifiee — [DATE]",
            "body_template": "Le serveur sera indisponible le [DATE] de [HEURE_DEBUT] a [HEURE_FIN] pour maintenance. Merci de sauvegarder votre travail avant cette periode.",
            "default_priority": "warning",
            "default_target_type": "broadcast",
            "default_expiry_hours": 48,
        },
        {
            "name": "Incident / Alerte",
            "title_template": "Incident — [SERVICE]",
            "body_template": "Nous rencontrons actuellement un incident sur [SERVICE]. L'equipe technique est mobilisee. Prochaine mise a jour des que possible.",
            "default_priority": "urgent",
            "default_target_type": "broadcast",
            "default_expiry_hours": 24,
        },
        {
            "name": "Annonce nouvelle version",
            "title_template": "Nouvelle version disponible — [APP] v[VERSION]",
            "body_template": "Decouvrez les nouveautes de la version [VERSION] :\n- \n- \n\nPlus de details dans le changelog.",
            "default_priority": "info",
            "default_target_type": "broadcast",
            "default_expiry_hours": 168,
        },
    ]

    for tmpl in defaults:
        existing = await db.scalar(
            select(MessageTemplate).where(MessageTemplate.name == tmpl["name"])
        )
        if not existing:
            db.add(MessageTemplate(**tmpl))

    await db.commit()
    logger.info("Message templates seed complete")
