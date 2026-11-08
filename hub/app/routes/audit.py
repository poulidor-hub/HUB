# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Routes d'audit - Statistiques de connexion et logs d'actions

Système complet qui stocke:
- Les changements d'état (login/logout) via UserSession
- Les actions détaillées (accès apps, etc.) via AuditLog
Permet de connaître:
- Le nombre de connexions par utilisateur/application/période
- Le temps de connexion (durée des sessions)
- Les utilisateurs actuellement connectés
- Le détail des accès aux applications
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc, delete
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, timezone
from typing import Optional, List
import anyio
import csv
import io
import math
import time

from app.database import get_db
from app.models import User, UserSession, AuditLog, Application, AppUsageSegment, ServiceAccount, utcnow
from app.auth import get_current_user, get_current_entity, get_admin_or_admin_sa, get_settings
from app.usage_stats import (
    BASE_BUCKET_MINUTES,
    bucket_size_for_days,
    compute_activity_heatmap,
    compute_daily_uniques,
    compute_records,
    compute_simultaneous_series,
    downsample_max,
)
from app.usage_tracking import (
    SEGMENT_REASON_MAP,
    close_segments_for_session,
    touch_app_presence,
)

router = APIRouter(prefix="/api/audit", tags=["Audit"])
settings = get_settings()

# Cooldown pour les notifications SSE d'audit (évite le spam lors de changements fréquents)
_audit_last_notify = 0.0


# Import différé pour éviter les imports circulaires
def _notify_audit(extra: Optional[dict] = None):
    """
    Notifie les clients SSE d'une mise à jour d'audit.
    Utilise un cooldown configurable (AUDIT_SSE_COOLDOWN dans .env)
    pour éviter les notifications trop fréquentes.
    extra: champs additionnels fusionnés dans le payload (ex. error_logged).
    """
    global _audit_last_notify

    current_time = time.time()
    if current_time - _audit_last_notify < settings.audit_sse_cooldown:
        return  # Cooldown actif, on skip la notification

    _audit_last_notify = current_time

    try:
        from app.routes.events import notify_event
        payload = {"action": "log_added"}
        if extra:
            payload.update(extra)
        notify_event("audit_updated", payload)
    except ImportError:
        pass  # Module events pas encore chargé


def _notify_active_users():
    """Notifie un changement de présence (session/segment). Cooldown géré côté events."""
    try:
        from app.routes.events import notify_active_users_changed
        notify_active_users_changed()
    except ImportError:
        pass


# =============================================================================
# FONCTIONS UTILITAIRES POUR AUDIT LOG
# =============================================================================

async def log_action(
    db: AsyncSession,
    user_id: Optional[int],
    action: str,
    target: str = None,
    ip_address: str = None,
    user_agent: str = None,
    details: str = None,
    notify_sse: bool = True,
    # Nouveaux paramètres de traçabilité
    request: "Request" = None,  # Objet Request pour extraire le contexte
    service_account_id: Optional[int] = None,
    session_id: Optional[int] = None,
    response_status: Optional[int] = None
):
    """
    Enregistre une action dans les logs d'audit avec contexte enrichi.
    Actions supportées: login, logout, access_app, replace_app, delete_app, etc.
    
    Args:
        user_id: ID de l'utilisateur (None pour actions système/auto-registration)
        notify_sse: Si True, notifie les clients SSE de la nouvelle entrée
        request: Objet Request FastAPI pour extraire automatiquement:
            - request_id (UUID généré par middleware)
            - source_app_slug (header X-Slug)
            - request_method (GET, POST, etc.)
            - request_path (/api/...)
            - ip_address (si non fourni)
            - user_agent (si non fourni)
            - duration_ms (depuis start_time)
        service_account_id: ID du service account (pour actions M2M)
        session_id: ID de la session utilisateur
        response_status: Code HTTP de la réponse
    """
    # Extraire le contexte de la requête si fourni
    request_id = None
    source_app_slug = None
    request_method = None
    request_path = None
    duration_ms = None
    
    if request:
        import time
        request_id = getattr(request.state, "request_id", None)
        source_app_slug = getattr(request.state, "source_app_slug", None)
        request_method = request.method
        request_path = str(request.url.path)[:255]
        
        # Calculer la durée si start_time disponible
        start_time = getattr(request.state, "start_time", None)
        if start_time:
            duration_ms = int((time.time() - start_time) * 1000)
        
        # Utiliser les valeurs de la requête si non fournies
        if not ip_address and request.client:
            # IMPORTANT : derrière le reverse proxy nginx (hub-frontend),
            # request.client.host est l'IP du proxy (ex: 10.42.0.4), pas celle
            # du client réel. On honore X-Forwarded-For/X-Real-IP via le helper
            # dédié qui ne truste ces headers que si l'IP directe est interne
            # au réseau Docker (= proxy de confiance).
            from app.routes.auth import _get_client_ip
            ip_address = _get_client_ip(request)
        if not user_agent:
            user_agent = request.headers.get("User-Agent")
    
    log_entry = AuditLog(
        request_id=request_id,
        user_id=user_id,
        service_account_id=service_account_id,
        session_id=session_id,
        action=action,
        target=target,
        source_app_slug=source_app_slug,
        request_method=request_method,
        request_path=request_path,
        response_status=response_status,
        duration_ms=duration_ms,
        ip_address=ip_address,
        user_agent=user_agent[:255] if user_agent else None,
        details=details
    )
    db.add(log_entry)
    
    # Notifier les clients SSE (sauf si désactivé pour éviter les doublons)
    if notify_sse:
        _notify_audit()
    
    return log_entry


async def purge_old_logs(
    db: AsyncSession,
    audit_retention_days: int,
    error_retention_days: int
) -> dict:
    """
    Purge les anciens logs d'audit et d'erreurs.
    
    Args:
        audit_retention_days: Nombre de jours à conserver pour audit_logs (0 = pas de purge)
        error_retention_days: Nombre de jours à conserver pour error_logs (0 = pas de purge)
    
    Returns:
        dict avec le nombre d'entrées supprimées
    """
    from app.models import ErrorLog
    from sqlalchemy import delete
    
    audit_deleted = 0
    errors_deleted = 0
    
    if audit_retention_days > 0:
        cutoff = utcnow() - timedelta(days=audit_retention_days)
        result = await db.execute(
            delete(AuditLog).where(AuditLog.created_at < cutoff)
        )
        audit_deleted = result.rowcount
    
    if error_retention_days > 0:
        cutoff = utcnow() - timedelta(days=error_retention_days)
        result = await db.execute(
            delete(ErrorLog).where(ErrorLog.created_at < cutoff)
        )
        errors_deleted = result.rowcount
    
    await db.commit()
    
    return {
        "audit_deleted": audit_deleted,
        "errors_deleted": errors_deleted
    }


async def purge_old_sessions(db: AsyncSession, retention_days: int) -> int:
    """Purge les user_sessions plus vieilles que retention_days (login_at).

    Audit 08/11/2026 : seule table sans rétention (37,6 k lignes de churn,
    sans index sur login_at — index ajouté par la migration 063).
    0 = pas de purge (cohérent avec purge_old_logs).
    """
    from sqlalchemy import delete

    if retention_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=retention_days)
    result = await db.execute(
        delete(UserSession).where(UserSession.login_at < cutoff)
    )
    await db.commit()
    return result.rowcount


# =============================================================================
# GESTION DES SESSIONS
# =============================================================================

async def start_session(
    db: AsyncSession,
    user_id: int,
    ip_address: str = None,
    user_agent: str = None
) -> UserSession:
    """
    Démarre une nouvelle session pour un utilisateur.
    Ferme automatiquement les sessions orphelines (sans logout).
    """
    now = utcnow()
    
    # Fermer les éventuelles sessions orphelines de cet utilisateur
    timeout = now - timedelta(minutes=settings.session_timeout_minutes)
    result = await db.execute(
        select(UserSession).where(
            and_(
                UserSession.user_id == user_id,
                UserSession.logout_at == None
            )
        )
    )
    orphan_sessions = result.scalars().all()
    
    for session in orphan_sessions:
        # Utiliser last_activity_at pour déterminer le moment réel du timeout,
        # sans jamais poser une fermeture dans le futur (clamp à now)
        last_activity = getattr(session, 'last_activity_at', None) or session.login_at
        session.logout_at = min(
            last_activity + timedelta(minutes=settings.session_timeout_minutes),
            now,
        )
        session.logout_reason = "timeout"
        await close_segments_for_session(db, session, "timeout", ended_at=session.logout_at)
    
    # Créer la nouvelle session
    new_session = UserSession(
        user_id=user_id,
        login_at=now,
        last_activity_at=now,
        ip_address=ip_address,
        user_agent=user_agent[:255] if user_agent else None
    )
    db.add(new_session)

    # Notifier les clients SSE
    _notify_audit()
    _notify_active_users()

    return new_session


async def get_active_session_for_user(
    db: AsyncSession,
    user_id: int
) -> Optional[UserSession]:
    """
    Récupère la session active d'un utilisateur (s'il en a une).
    
    Returns:
        La session active ou None
    """
    result = await db.execute(
        select(UserSession).where(
            and_(
                UserSession.user_id == user_id,
                UserSession.logout_at == None
            )
        ).order_by(UserSession.login_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def end_session(
    db: AsyncSession,
    user_id: int,
    reason: str = "explicit"
) -> Optional[UserSession]:
    """
    Termine la session active d'un utilisateur.
    
    Args:
        user_id: ID de l'utilisateur
        reason: Raison de fin de session:
            - explicit: logout volontaire
            - timeout: inactivité
            - token_revoked: révocation admin
            - browser_closed: fermeture navigateur (via beforeunload)
            - sse_disconnect: déconnexion SSE détectée
    """
    result = await db.execute(
        select(UserSession).where(
            and_(
                UserSession.user_id == user_id,
                UserSession.logout_at == None
            )
        ).order_by(UserSession.login_at.desc())
        .limit(1)
    )
    session = result.scalar_one_or_none()

    if session:
        session.logout_at = utcnow()
        session.logout_reason = reason
        session.current_app_slug = None  # Plus d'app active
        await close_segments_for_session(
            db, session, SEGMENT_REASON_MAP.get(reason, "logout")
        )
        # Notifier les clients SSE
        _notify_audit()
        _notify_active_users()

    return session


async def update_session_activity(
    db: AsyncSession,
    user_id: int,
    app_slug: str = None
) -> Optional[UserSession]:
    """
    Met à jour l'activité d'une session (heartbeat).
    
    Args:
        user_id: ID de l'utilisateur
        app_slug: Si fourni, met à jour l'application courante
    
    Returns:
        La session mise à jour ou None si pas de session active
    """
    result = await db.execute(
        select(UserSession).where(
            and_(
                UserSession.user_id == user_id,
                UserSession.logout_at == None
            )
        ).order_by(UserSession.login_at.desc())
        .limit(1)
    )
    session = result.scalar_one_or_none()

    if session:
        session.last_activity_at = utcnow()
        if app_slug is not None:
            session.current_app_slug = app_slug
            await touch_app_presence(db, session, app_slug)

    return session


async def ensure_session_presence(
    db: AsyncSession,
    user_id: int,
    app_slug: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> Optional[UserSession]:
    """Heartbeat + segment d'usage ; recrée une session si aucune n'est ouverte
    (ex: passage SSO OIDC d'un utilisateur dont la session HUB a expiré)."""
    session = await update_session_activity(db, user_id, app_slug=app_slug)
    if session is None:
        session = await start_session(db, user_id, ip_address=ip_address, user_agent=user_agent)
        if app_slug:
            await update_session_activity(db, user_id, app_slug=app_slug)
    return session


async def cleanup_stale_sessions(db: AsyncSession) -> int:
    """
    Ferme les sessions inactives basé sur last_activity_at.
    Appelé périodiquement par le système SSE.
    Retourne le nombre de sessions fermées.
    """
    timeout = utcnow() - timedelta(minutes=settings.session_timeout_minutes)

    now = utcnow()

    # Défensif : au plus une session ouverte par utilisateur. Les doublons
    # (crash, ancien bug de commit) sont fermés en gardant la plus récente.
    result = await db.execute(
        select(UserSession)
        .where(UserSession.logout_at == None)  # noqa: E711
        .order_by(UserSession.login_at.desc(), UserSession.id.desc())
    )
    duplicate_closed = 0
    seen_users = set()
    for session in result.scalars().all():
        if session.user_id in seen_users:
            last_activity = session.last_activity_at or session.login_at
            session.logout_at = min(
                last_activity + timedelta(minutes=settings.session_timeout_minutes),
                now,
            )
            session.logout_reason = "timeout"
            session.current_app_slug = None
            await close_segments_for_session(db, session, "timeout", ended_at=session.logout_at)
            duplicate_closed += 1
        else:
            seen_users.add(session.user_id)

    # Sélectionner les sessions dont la dernière activité dépasse le timeout
    result = await db.execute(
        select(UserSession).where(
            and_(
                UserSession.logout_at == None,
                UserSession.last_activity_at < timeout
            )
        )
    )
    stale_sessions = result.scalars().all()
    
    for session in stale_sessions:
        # Utiliser last_activity_at pour le logout_at réel
        session.logout_at = session.last_activity_at + timedelta(minutes=settings.session_timeout_minutes)
        session.logout_reason = "timeout"
        session.current_app_slug = None
        await close_segments_for_session(
            db, session, "timeout", ended_at=session.logout_at
        )

    if stale_sessions or duplicate_closed:
        await db.commit()
        _notify_audit()

    return len(stale_sessions) + duplicate_closed


# =============================================================================
# ENDPOINTS API
# =============================================================================

@router.get("/sessions/active")
async def get_active_sessions(
    current_user = Depends(get_admin_or_admin_sa),
    db: AsyncSession = Depends(get_db)
):
    """
    Liste les utilisateurs actuellement connectés.
    Requiert: admin Hub
    
    Retourne pour chaque session:
    - user_id, username
    - login_at, duration_minutes
    - last_activity_at, idle_minutes (temps depuis dernière activité)
    - current_app_slug, current_app_name (application active)
    - ip_address
    """
    
    # Nettoyer les sessions expirées d'abord
    cleaned = await cleanup_stale_sessions(db)
    
    result = await db.execute(
        select(UserSession)
        .options(selectinload(UserSession.user))
        .where(UserSession.logout_at == None)
        .order_by(UserSession.login_at.desc())
    )
    active_sessions = result.scalars().all()
    
    # Récupérer les noms des applications pour les sessions qui ont une app active
    app_slugs = [s.current_app_slug for s in active_sessions if s.current_app_slug]
    app_names = {}
    if app_slugs:
        apps_result = await db.execute(
            select(Application).where(Application.slug.in_(app_slugs))
        )
        for app in apps_result.scalars():
            app_names[app.slug] = app.name
    
    now = utcnow()
    return {
        "stale_sessions_cleaned": cleaned,
        "active_count": len(active_sessions),
        "sessions": [
            {
                "id": s.id,
                "user_id": s.user_id,
                "username": s.user.username if s.user else None,
                "login_at": s.login_at.isoformat(),
                "duration_minutes": int((now - s.login_at).total_seconds() / 60),
                "last_activity_at": s.last_activity_at.isoformat() if s.last_activity_at else s.login_at.isoformat(),
                "idle_minutes": int((now - (s.last_activity_at or s.login_at)).total_seconds() / 60),
                "current_app_slug": s.current_app_slug,
                "current_app_name": app_names.get(s.current_app_slug) if s.current_app_slug else None,
                "ip_address": s.ip_address
            }
            for s in active_sessions
        ]
    }


@router.post("/sessions/{session_id}/revoke")
async def revoke_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Force la déconnexion d'UNE session précise (admin uniquement).
    Ferme la session avec la raison 'forced'.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")

    result = await db.execute(
        select(UserSession).where(UserSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable")
    if session.logout_at is not None:
        raise HTTPException(status_code=400, detail="Session déjà fermée")

    session.logout_at = utcnow()
    session.logout_reason = "forced"
    session.current_app_slug = None
    await close_segments_for_session(db, session, "forced")
    await db.commit()

    _notify_audit()

    return {
        "message": "Session révoquée",
        "session_id": session.id,
        "user_id": session.user_id,
    }


@router.get("/sessions/history")
async def get_session_history(
    user_id: Optional[int] = Query(None, description="Filtrer par utilisateur (ID)"),
    username: Optional[str] = Query(None, description="Filtrer par nom d'utilisateur (recherche partielle)"),
    days: int = Query(7, ge=1, le=90, description="Nombre de jours d'historique"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Historique des sessions.
    Requiert: admin Hub (ou ses propres sessions)
    """
    # Vérification des droits
    if not current_user.is_admin and user_id and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    # Si non-admin, forcer à ses propres sessions et ignorer le filtre username
    if not current_user.is_admin:
        user_id = current_user.id
        username = None
    
    since = utcnow() - timedelta(days=days)
    
    query = select(UserSession).options(selectinload(UserSession.user)).where(
        UserSession.login_at >= since
    )
    
    if user_id:
        query = query.where(UserSession.user_id == user_id)

    if username:
        username_pattern = f"%{username.strip()}%"
        ids_result = await db.execute(
            select(User.id).where(User.username.ilike(username_pattern))
        )
        matching_ids = [row[0] for row in ids_result.all()]
        if not matching_ids:
            return {"period_days": days, "total_sessions": 0, "sessions": []}
        query = query.where(UserSession.user_id.in_(matching_ids))
    
    query = query.order_by(UserSession.login_at.desc())
    
    result = await db.execute(query)
    sessions = result.scalars().all()
    
    # Récupérer les noms des applications pour les sessions qui ont une app
    app_slugs = [s.current_app_slug for s in sessions if s.current_app_slug]
    app_names = {}
    if app_slugs:
        apps_result = await db.execute(
            select(Application).where(Application.slug.in_(app_slugs))
        )
        for app in apps_result.scalars():
            app_names[app.slug] = app.name
    
    now = utcnow()
    return {
        "period_days": days,
        "total_sessions": len(sessions),
        "sessions": [
            {
                "id": s.id,
                "user_id": s.user_id,
                "username": s.user.username if s.user else None,
                "login_at": s.login_at.isoformat(),
                "logout_at": s.logout_at.isoformat() if s.logout_at else None,
                "logout_reason": s.logout_reason,
                "duration_seconds": s.duration_seconds,
                "duration_display": format_duration(s.duration_seconds) if s.duration_seconds else "en cours",
                "duration_minutes": int((now - s.login_at).total_seconds() / 60) if not s.logout_at else None,
                "idle_minutes": int((now - (s.last_activity_at or s.login_at)).total_seconds() / 60) if not s.logout_at else None,
                "current_app_slug": s.current_app_slug,
                "current_app_name": app_names.get(s.current_app_slug) if s.current_app_slug else None,
                "ip_address": s.ip_address
            }
            for s in sessions
        ]
    }


@router.get("/stats")
async def get_connection_stats(
    days: int = Query(7, ge=1, le=365, description="Période en jours"),
    current_user = Depends(get_admin_or_admin_sa),
    db: AsyncSession = Depends(get_db)
):
    """
    Statistiques globales de connexion.
    Requiert: admin Hub
    """
    
    since = utcnow() - timedelta(days=days)
    
    # Nombre total de sessions
    result = await db.execute(
        select(func.count(UserSession.id)).where(UserSession.login_at >= since)
    )
    total_sessions = result.scalar()
    
    # Nombre d'utilisateurs uniques
    result = await db.execute(
        select(func.count(func.distinct(UserSession.user_id))).where(UserSession.login_at >= since)
    )
    unique_users = result.scalar()
    
    # Durée moyenne des sessions (terminées)
    result = await db.execute(
        select(func.avg(
            func.extract('epoch', UserSession.logout_at) - func.extract('epoch', UserSession.login_at)
        )).where(
            and_(
                UserSession.login_at >= since,
                UserSession.logout_at != None
            )
        )
    )
    avg_duration = result.scalar()
    
    # Sessions actuellement actives
    result = await db.execute(
        select(func.count(UserSession.id)).where(UserSession.logout_at == None)
    )
    active_now = result.scalar()
    
    # Connexions par jour
    result = await db.execute(
        select(
            func.date(UserSession.login_at).label('date'),
            func.count(UserSession.id).label('count')
        ).where(UserSession.login_at >= since)
        .group_by(func.date(UserSession.login_at))
        .order_by(func.date(UserSession.login_at))
    )
    daily_connections = [{"date": str(r.date), "count": r.count} for r in result.all()]
    
    # Top utilisateurs (par nombre de connexions)
    result = await db.execute(
        select(
            UserSession.user_id,
            func.count(UserSession.id).label('sessions'),
            func.sum(
                func.extract('epoch', UserSession.logout_at) - func.extract('epoch', UserSession.login_at)
            ).label('total_time')
        )
        .where(and_(UserSession.login_at >= since, UserSession.logout_at != None))
        .group_by(UserSession.user_id)
        .order_by(func.count(UserSession.id).desc())
        .limit(10)
    )
    top_users_raw = result.all()
    
    # Récupérer les usernames
    user_ids = [r.user_id for r in top_users_raw]
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u.username for u in result.scalars().all()}
    else:
        users_map = {}
    
    top_users = [
        {
            "user_id": r.user_id,
            "username": users_map.get(r.user_id, "unknown"),
            "sessions": r.sessions,
            "total_time_seconds": int(r.total_time) if r.total_time else 0,
            "total_time_display": format_duration(int(r.total_time)) if r.total_time else "0s"
        }
        for r in top_users_raw
    ]
    
    return {
        "period_days": days,
        "total_sessions": total_sessions,
        "unique_users": unique_users,
        "active_now": active_now,
        "avg_session_duration_seconds": int(avg_duration) if avg_duration else 0,
        "avg_session_duration_display": format_duration(int(avg_duration)) if avg_duration else "N/A",
        "daily_connections": daily_connections,
        "top_users": top_users
    }


@router.get("/overview/kpis")
async def get_overview_kpis(
    days: int = Query(7, ge=1, le=365, description="Période en jours"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """KPI de la vue d'ensemble + deltas vs période précédente équivalente."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")

    from app.models import ErrorLog

    now = utcnow()
    cur_start = now - timedelta(days=days)
    prev_start = now - timedelta(days=days * 2)

    async def count_errors(low, start, end):
        stmt = select(func.count(ErrorLog.id)).where(
            ErrorLog.created_at >= start, ErrorLog.created_at < end
        )
        if low == 5:
            stmt = stmt.where(ErrorLog.error_code >= 500)
        else:
            stmt = stmt.where(ErrorLog.error_code.in_([401, 403]))
        return (await db.execute(stmt)).scalar() or 0

    async def count_apps(start, end):
        stmt = select(func.count(func.distinct(AuditLog.target))).where(
            AuditLog.action == "access_app",
            AuditLog.created_at >= start,
            AuditLog.created_at < end,
        )
        return (await db.execute(stmt)).scalar() or 0

    active_now = (await db.execute(
        select(func.count(UserSession.id)).where(UserSession.logout_at == None)
    )).scalar() or 0

    return {
        "period_days": days,
        "active_now": active_now,
        "errors_critical": {
            "current": await count_errors(5, cur_start, now),
            "previous": await count_errors(5, prev_start, cur_start),
        },
        "auth_failures": {
            "current": await count_errors(4, cur_start, now),
            "previous": await count_errors(4, prev_start, cur_start),
        },
        "apps_active": {
            "current": await count_apps(cur_start, now),
            "previous": await count_apps(prev_start, cur_start),
        },
    }


@router.get("/overview/alerts")
async def get_overview_alerts(
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(5, ge=1, le=20),
    auth_threshold: int = Query(5, ge=1, le=100),
    loop_window_minutes: int = Query(15, ge=1, le=1440),
    loop_threshold: int = Query(10, ge=2, le=10000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Alertes calculées à la volée depuis les erreurs HTTP et les échecs d'auth.

    `error_loop` : N+ requêtes identiques (méthode+chemin+code+IP) dans la
    fenêtre glissante `loop_window_minutes` — signale une boucle de retries
    d'un client (ex. une app qui retente son enregistrement toutes les 15 s).
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")

    from app.models import ErrorLog

    since = utcnow() - timedelta(days=days)
    alerts = []

    http_rows = (await db.execute(
        select(
            ErrorLog.request_path,
            func.count(ErrorLog.id).label("occ"),
            func.max(ErrorLog.created_at).label("last_seen"),
            func.max(ErrorLog.source_app_slug).label("app"),
        )
        .where(ErrorLog.error_code >= 500, ErrorLog.created_at >= since)
        .group_by(ErrorLog.request_path)
        .order_by(desc("occ"))
        .limit(limit)
    )).all()
    for path, occ, last_seen, app in http_rows:
        alerts.append({
            "type": "http_error",
            "severity": "danger",
            "occurrences": occ,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "source_app_slug": app,
            "request_path": path,
            "deeplink": {"tab": "errors", "filters": {"path": path}},
        })

    auth_rows = (await db.execute(
        select(
            ErrorLog.ip_address,
            func.count(ErrorLog.id).label("occ"),
            func.max(ErrorLog.created_at).label("last_seen"),
        )
        .where(
            ErrorLog.error_code.in_([401, 403]),
            ErrorLog.created_at >= since,
            ErrorLog.ip_address != None,
        )
        .group_by(ErrorLog.ip_address)
        .having(func.count(ErrorLog.id) >= auth_threshold)
        .order_by(desc("occ"))
        .limit(limit)
    )).all()
    for ip, occ, last_seen in auth_rows:
        alerts.append({
            "type": "auth_failures",
            "severity": "warning",
            "occurrences": occ,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "ip_address": ip,
            "deeplink": {"tab": "logs", "filters": {"ip": ip}},
        })

    # Boucles de requêtes répétées : même méthode+chemin+code+IP, fenêtre courte
    loop_since = utcnow() - timedelta(minutes=loop_window_minutes)
    loop_rows = (await db.execute(
        select(
            ErrorLog.request_method,
            ErrorLog.request_path,
            ErrorLog.error_code,
            ErrorLog.ip_address,
            func.count(ErrorLog.id).label("occ"),
            func.max(ErrorLog.created_at).label("last_seen"),
        )
        .where(ErrorLog.created_at >= loop_since)
        .group_by(ErrorLog.request_method, ErrorLog.request_path,
                  ErrorLog.error_code, ErrorLog.ip_address)
        .having(func.count(ErrorLog.id) >= loop_threshold)
        .order_by(desc("occ"))
        .limit(limit)
    )).all()
    for method, path, code, ip, occ, last_seen in loop_rows:
        alerts.append({
            "type": "error_loop",
            "severity": "danger",
            "occurrences": occ,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "request_method": method,
            "request_path": path,
            "error_code": code,
            "ip_address": ip,
            "deeplink": {"tab": "errors",
                         "filters": {"path": path, "code": str(code)}},
        })

    return {"alerts": alerts}


@router.get("/stats/user/{user_id}")
async def get_user_stats(
    user_id: int,
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Statistiques de connexion pour un utilisateur spécifique.
    Requiert: admin Hub ou l'utilisateur lui-même
    """
    if not current_user.is_admin and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    since = utcnow() - timedelta(days=days)
    
    # Récupérer l'utilisateur
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    
    # Statistiques
    result = await db.execute(
        select(
            func.count(UserSession.id).label('total_sessions'),
            func.sum(
                func.extract('epoch', UserSession.logout_at) - func.extract('epoch', UserSession.login_at)
            ).label('total_time'),
            func.avg(
                func.extract('epoch', UserSession.logout_at) - func.extract('epoch', UserSession.login_at)
            ).label('avg_time')
        ).where(
            and_(
                UserSession.user_id == user_id,
                UserSession.login_at >= since,
                UserSession.logout_at != None
            )
        )
    )
    stats = result.one()
    
    # Dernière connexion
    result = await db.execute(
        select(UserSession)
        .where(UserSession.user_id == user_id)
        .order_by(UserSession.login_at.desc())
        .limit(1)
    )
    last_session = result.scalar_one_or_none()
    
    # Session active ?
    is_online = last_session and last_session.logout_at is None
    
    return {
        "user_id": user_id,
        "username": user.username,
        "period_days": days,
        "is_online": is_online,
        "total_sessions": stats.total_sessions or 0,
        "total_time_seconds": int(stats.total_time) if stats.total_time else 0,
        "total_time_display": format_duration(int(stats.total_time)) if stats.total_time else "0s",
        "avg_session_seconds": int(stats.avg_time) if stats.avg_time else 0,
        "avg_session_display": format_duration(int(stats.avg_time)) if stats.avg_time else "N/A",
        "last_login": last_session.login_at.isoformat() if last_session else None,
        "current_session_duration": format_duration(
            int((utcnow() - last_session.login_at).total_seconds())
        ) if is_online else None
    }


# =============================================================================
# UTILITAIRES
# =============================================================================

def format_duration(seconds: int) -> str:
    """Formate une durée en format lisible"""
    if seconds is None or seconds < 0:
        return "N/A"
    
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"


# =============================================================================
# ENDPOINTS API - LOGS D'AUDIT DÉTAILLÉS
# =============================================================================

@router.get("/logs")
async def get_audit_logs(
    days: int = Query(30, ge=1, le=365, description="Période en jours"),
    action: Optional[str] = Query(None, description="Filtrer par action (access_app, login, logout...)"),
    target: Optional[str] = Query(None, description="Filtrer par cible (app_slug)"),
    user_id: Optional[int] = Query(None, description="Filtrer par utilisateur (ID)"),
    username: Optional[str] = Query(None, description="Filtrer par nom d'utilisateur (recherche partielle, insensible à la casse)"),
    ip: Optional[str] = Query(None, description="Filtrer par adresse IP exacte"),
    page: int = Query(1, ge=1, description="Numéro de page"),
    page_size: int = Query(50, ge=10, le=200, description="Éléments par page"),
    current_user = Depends(get_current_entity),
    db: AsyncSession = Depends(get_db)
):
    """
    Liste paginée des logs d'audit détaillés.
    Requiert: admin Hub (ou ses propres logs si user_id=self)
    """
    if isinstance(current_user, ServiceAccount):
        if not current_user.can_admin_read:
            raise HTTPException(status_code=403,
                                detail="Lecture admin requise pour ce service account")
    else:
        # Sémantique user inchangée : non-admin → ses propres logs
        if not current_user.is_admin and user_id and user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Accès non autorisé")
        if not current_user.is_admin:
            user_id = current_user.id
            username = None
    
    since = utcnow() - timedelta(days=days)
    
    # Construction de la requête
    query = select(AuditLog).where(AuditLog.created_at >= since)
    count_query = select(func.count(AuditLog.id)).where(AuditLog.created_at >= since)
    
    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    
    if target:
        query = query.where(AuditLog.target == target)
        count_query = count_query.where(AuditLog.target == target)
    
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
        count_query = count_query.where(AuditLog.user_id == user_id)

    if ip:
        query = query.where(AuditLog.ip_address == ip)
        count_query = count_query.where(AuditLog.ip_address == ip)

    # Filtre par username (recherche partielle ILIKE) — résolu en liste d'IDs.
    # Les échecs de login sont tracés avec l'identifiant tenté dans `target`,
    # y compris pour un utilisateur inexistant (user_id NULL) : on matche aussi
    # sur target pour l'action login_failed.
    if username:
        username_pattern = f"%{username.strip()}%"
        ids_result = await db.execute(
            select(User.id).where(User.username.ilike(username_pattern))
        )
        matching_ids = [row[0] for row in ids_result.all()]
        attempted_filter = and_(
            AuditLog.action == "login_failed",
            AuditLog.target.ilike(username_pattern),
        )
        if matching_ids:
            combined = or_(AuditLog.user_id.in_(matching_ids), attempted_filter)
        else:
            # Aucun utilisateur existant ne matche : seuls les identifiants
            # tentés (login_failed) peuvent correspondre.
            combined = attempted_filter
        query = query.where(combined)
        count_query = count_query.where(combined)
    
    # Compte total
    result = await db.execute(count_query)
    total_count = result.scalar()
    
    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(desc(AuditLog.created_at)).offset(offset).limit(page_size)
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    # Récupérer les usernames
    user_ids = list(set(l.user_id for l in logs if l.user_id))
    users_map = {}
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u.username for u in result.scalars().all()}
    
    return {
        "period_days": days,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": (total_count + page_size - 1) // page_size,
        "logs": [
            {
                "id": l.id,
                "user_id": l.user_id,
                "username": users_map.get(l.user_id, "unknown"),
                "action": l.action,
                "target": l.target,
                "details": l.details,
                "ip_address": l.ip_address,
                "user_agent": l.user_agent,
                "created_at": l.created_at.isoformat()
            }
            for l in logs
        ]
    }


@router.get("/logs/apps")
async def get_app_audit_summary(
    days: int = Query(30, ge=1, le=365, description="Période en jours"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Résumé des accès par application.
    Requiert: admin Hub
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    since = utcnow() - timedelta(days=days)
    
    # Accès par application
    result = await db.execute(
        select(
            AuditLog.target,
            func.count(AuditLog.id).label('access_count'),
            func.count(func.distinct(AuditLog.user_id)).label('unique_users')
        )
        .where(and_(
            AuditLog.created_at >= since,
            AuditLog.action == 'access_app',
            AuditLog.target != None
        ))
        .group_by(AuditLog.target)
        .order_by(func.count(AuditLog.id).desc())
    )
    app_stats_raw = result.all()
    
    # Récupérer les infos des apps
    app_slugs = [r.target for r in app_stats_raw]
    apps_map = {}
    if app_slugs:
        result = await db.execute(select(Application).where(Application.slug.in_(app_slugs)))
        apps_map = {a.slug: {"name": a.name, "icon": a.icon, "color": a.color} for a in result.scalars().all()}
    
    return {
        "period_days": days,
        "apps": [
            {
                "slug": r.target,
                "name": apps_map.get(r.target, {}).get("name", r.target),
                "icon": apps_map.get(r.target, {}).get("icon", "bi-app"),
                "color": apps_map.get(r.target, {}).get("color", "#6c757d"),
                "access_count": r.access_count,
                "unique_users": r.unique_users
            }
            for r in app_stats_raw
        ]
    }


@router.get("/logs/apps/{app_slug}")
async def get_app_audit_detail(
    app_slug: str,
    days: int = Query(30, ge=1, le=365, description="Période en jours"),
    page: int = Query(1, ge=1, description="Numéro de page"),
    page_size: int = Query(50, ge=10, le=200, description="Éléments par page"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Détail des accès pour une application spécifique.
    Requiert: admin Hub
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    # Récupérer les infos de l'app (peut avoir été supprimée — logs persistent)
    result = await db.execute(select(Application).where(Application.slug == app_slug))
    app = result.scalar_one_or_none()
    
    since = utcnow() - timedelta(days=days)
    
    # Logs pour cette app
    query = select(AuditLog).where(and_(
        AuditLog.created_at >= since,
        AuditLog.action == 'access_app',
        AuditLog.target == app_slug
    ))
    count_query = select(func.count(AuditLog.id)).where(and_(
        AuditLog.created_at >= since,
        AuditLog.action == 'access_app',
        AuditLog.target == app_slug
    ))
    
    # Compte total
    result = await db.execute(count_query)
    total_count = result.scalar()
    
    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(desc(AuditLog.created_at)).offset(offset).limit(page_size)
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    # Récupérer les usernames
    user_ids = list(set(l.user_id for l in logs if l.user_id))
    users_map = {}
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u.username for u in result.scalars().all()}
    
    # Statistiques de cette app
    result = await db.execute(
        select(
            func.count(AuditLog.id).label('total_access'),
            func.count(func.distinct(AuditLog.user_id)).label('unique_users')
        ).where(and_(
            AuditLog.created_at >= since,
            AuditLog.action == 'access_app',
            AuditLog.target == app_slug
        ))
    )
    stats = result.one()
    
    # Top utilisateurs pour cette app
    result = await db.execute(
        select(
            AuditLog.user_id,
            func.count(AuditLog.id).label('access_count')
        )
        .where(and_(
            AuditLog.created_at >= since,
            AuditLog.action == 'access_app',
            AuditLog.target == app_slug
        ))
        .group_by(AuditLog.user_id)
        .order_by(func.count(AuditLog.id).desc())
        .limit(10)
    )
    top_users_raw = result.all()
    
    # Compléter avec usernames
    top_user_ids = [r.user_id for r in top_users_raw]
    if top_user_ids:
        result = await db.execute(select(User).where(User.id.in_(top_user_ids)))
        top_users_map = {u.id: u.username for u in result.scalars().all()}
    else:
        top_users_map = {}
    
    return {
        "app": {
            "slug": app_slug,
            "name": app.name if app else app_slug,
            "icon": app.icon if app else "bi-app",
            "color": app.color if app else "#6c757d"
        },
        "period_days": days,
        "stats": {
            "total_access": stats.total_access or 0,
            "unique_users": stats.unique_users or 0
        },
        "top_users": [
            {
                "user_id": r.user_id,
                "username": top_users_map.get(r.user_id, "unknown"),
                "access_count": r.access_count
            }
            for r in top_users_raw
        ],
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": (total_count + page_size - 1) // page_size,
        "logs": [
            {
                "id": l.id,
                "user_id": l.user_id,
                "username": users_map.get(l.user_id, "unknown"),
                "ip_address": l.ip_address,
                "user_agent": l.user_agent,
                "created_at": l.created_at.isoformat()
            }
            for l in logs
        ]
    }


@router.get("/logs/actions")
async def get_available_actions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Liste des types d'actions disponibles dans les logs.
    Requiert: admin Hub
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    result = await db.execute(
        select(AuditLog.action, func.count(AuditLog.id).label('count'))
        .group_by(AuditLog.action)
        .order_by(func.count(AuditLog.id).desc())
    )
    actions = result.all()
    
    return {
        "actions": [
            {"action": a.action, "count": a.count}
            for a in actions
        ]
    }


# =============================================================================
# EXPORT CSV
# =============================================================================

@router.get("/export/logs")
async def export_audit_logs_csv(
    days: int = Query(30, ge=1, le=365, description="Période en jours"),
    action: Optional[str] = Query(None, description="Filtrer par action"),
    target: Optional[str] = Query(None, description="Filtrer par cible (app_slug)"),
    user_id: Optional[int] = Query(None, description="Filtrer par utilisateur (ID)"),
    username: Optional[str] = Query(None, description="Filtrer par nom d'utilisateur (recherche partielle)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Exporte les logs d'audit en CSV.
    Requiert: admin Hub
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    since = utcnow() - timedelta(days=days)
    
    query = select(AuditLog).where(AuditLog.created_at >= since)
    
    if action:
        query = query.where(AuditLog.action == action)
    if target:
        query = query.where(AuditLog.target == target)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if username:
        username_pattern = f"%{username.strip()}%"
        ids_result = await db.execute(
            select(User.id).where(User.username.ilike(username_pattern))
        )
        matching_ids = [row[0] for row in ids_result.all()]
        if matching_ids:
            query = query.where(AuditLog.user_id.in_(matching_ids))
        else:
            # Aucun user matché → CSV vide (juste l'entête)
            query = query.where(AuditLog.id == -1)
    
    query = query.order_by(desc(AuditLog.created_at))
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    # Récupérer les usernames
    user_ids = list(set(l.user_id for l in logs if l.user_id))
    users_map = {}
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u.username for u in result.scalars().all()}
    
    # Générer le CSV
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    
    # En-têtes
    writer.writerow(['Date/Heure', 'Utilisateur', 'Action', 'Cible', 'Détails', 'Adresse IP', 'User Agent'])
    
    # Données
    for log in logs:
        writer.writerow([
            log.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            users_map.get(log.user_id, 'unknown'),
            log.action,
            log.target or '',
            log.details or '',
            log.ip_address or '',
            log.user_agent or ''
        ])
    
    output.seek(0)
    
    filename = f"audit_logs_{utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/export/sessions")
async def export_sessions_csv(
    days: int = Query(30, ge=1, le=90, description="Période en jours"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Exporte l'historique des sessions en CSV.
    Requiert: admin Hub
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    since = utcnow() - timedelta(days=days)
    
    query = select(UserSession).options(selectinload(UserSession.user)).where(
        UserSession.login_at >= since
    ).order_by(desc(UserSession.login_at))
    
    result = await db.execute(query)
    sessions = result.scalars().all()
    
    # Générer le CSV
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    
    # En-têtes
    writer.writerow(['Utilisateur', 'Connexion', 'Déconnexion', 'Durée (sec)', 'Raison', 'Adresse IP', 'User Agent'])
    
    # Données
    for s in sessions:
        writer.writerow([
            s.user.username if s.user else 'unknown',
            s.login_at.strftime('%Y-%m-%d %H:%M:%S'),
            s.logout_at.strftime('%Y-%m-%d %H:%M:%S') if s.logout_at else 'En cours',
            s.duration_seconds if s.duration_seconds else '',
            s.logout_reason or '',
            s.ip_address or '',
            s.user_agent or ''
        ])
    
    output.seek(0)
    
    filename = f"sessions_{utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# =============================================================================
# ENDPOINTS ERROR LOGS
# =============================================================================

@router.get("/errors")
async def get_error_logs(
    page: int = Query(1, ge=1, description="Numéro de page"),
    per_page: int = Query(50, ge=10, le=200, description="Éléments par page"),
    error_code: Optional[int] = Query(None, description="Filtrer par code HTTP (401, 403, 500, etc.)"),
    error_type: Optional[str] = Query(None, description="Filtrer par type (authentication, authorization, internal, etc.)"),
    source_app: Optional[str] = Query(None, description="Filtrer par application source (X-Slug)"),
    path: Optional[str] = Query(None, description="Filtrer par chemin (contient)"),
    hours: int = Query(24, ge=1, le=168, description="Heures d'historique (max 7 jours)"),
    current_user = Depends(get_admin_or_admin_sa),
    db: AsyncSession = Depends(get_db)
):
    """
    Liste les logs d'erreurs HTTP (4xx, 5xx).
    Requiert: admin Hub
    """
    from app.models import ErrorLog
    
    since = utcnow() - timedelta(hours=hours)
    
    # Construire la requête
    query = select(ErrorLog).where(ErrorLog.created_at >= since)
    
    if error_code:
        query = query.where(ErrorLog.error_code == error_code)
    if error_type:
        query = query.where(ErrorLog.error_type == error_type)
    if source_app:
        query = query.where(ErrorLog.source_app_slug == source_app)
    if path:
        query = query.where(ErrorLog.request_path.ilike(f"%{path}%"))
    
    # Compter le total
    count_query = select(func.count()).select_from(query.subquery())
    result = await db.execute(count_query)
    total = result.scalar()
    
    # Pagination
    offset = (page - 1) * per_page
    query = query.order_by(desc(ErrorLog.created_at)).offset(offset).limit(per_page)
    
    result = await db.execute(query)
    errors = result.scalars().all()
    
    # Récupérer les usernames
    user_ids = list(set(e.user_id for e in errors if e.user_id))
    users_map = {}
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u.username for u in result.scalars().all()}
    
    # Résoudre les service accounts (acteur M2M) — même logique que l'export CSV
    sa_ids = list(set(e.service_account_id for e in errors if e.service_account_id))
    sa_map = {}
    if sa_ids:
        from app.models import ServiceAccount
        result = await db.execute(select(ServiceAccount).where(ServiceAccount.id.in_(sa_ids)))
        sa_map = {s.id: s.client_id for s in result.scalars().all()}
    
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "errors": [
            {
                "id": e.id,
                "request_id": e.request_id,
                "error_code": e.error_code,
                "error_type": e.error_type,
                "error_message": e.error_message,
                "user_id": e.user_id,
                "username": users_map.get(e.user_id) if e.user_id else None,
                "service_account_id": e.service_account_id,
                "service_account_client_id": sa_map.get(e.service_account_id) if e.service_account_id else None,
                "source_app_slug": e.source_app_slug,
                "request_method": e.request_method,
                "request_path": e.request_path,
                "ip_address": e.ip_address,
                "created_at": e.created_at.isoformat(),
                "has_stack_trace": bool(e.stack_trace)
            }
            for e in errors
        ]
    }


@router.get("/errors/stats")
async def get_error_stats(
    hours: int = Query(24, ge=1, le=168, description="Heures d'historique"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Statistiques des erreurs HTTP.
    Requiert: admin Hub
    """
    from app.models import ErrorLog
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    since = utcnow() - timedelta(hours=hours)
    
    # Total des erreurs
    result = await db.execute(
        select(func.count(ErrorLog.id)).where(ErrorLog.created_at >= since)
    )
    total = result.scalar()
    
    # Par code d'erreur
    result = await db.execute(
        select(ErrorLog.error_code, func.count(ErrorLog.id).label('count'))
        .where(ErrorLog.created_at >= since)
        .group_by(ErrorLog.error_code)
        .order_by(func.count(ErrorLog.id).desc())
    )
    by_code = [{"code": r.error_code, "count": r.count} for r in result.all()]
    
    # Par type d'erreur
    result = await db.execute(
        select(ErrorLog.error_type, func.count(ErrorLog.id).label('count'))
        .where(ErrorLog.created_at >= since)
        .group_by(ErrorLog.error_type)
        .order_by(func.count(ErrorLog.id).desc())
    )
    by_type = [{"type": r.error_type, "count": r.count} for r in result.all()]
    
    # Par application source
    result = await db.execute(
        select(ErrorLog.source_app_slug, func.count(ErrorLog.id).label('count'))
        .where(and_(ErrorLog.created_at >= since, ErrorLog.source_app_slug != None))
        .group_by(ErrorLog.source_app_slug)
        .order_by(func.count(ErrorLog.id).desc())
        .limit(10)
    )
    by_source = [{"app": r.source_app_slug, "count": r.count} for r in result.all()]
    
    # Top endpoints avec erreurs
    result = await db.execute(
        select(ErrorLog.request_path, func.count(ErrorLog.id).label('count'))
        .where(ErrorLog.created_at >= since)
        .group_by(ErrorLog.request_path)
        .order_by(func.count(ErrorLog.id).desc())
        .limit(10)
    )
    top_endpoints = [{"path": r.request_path, "count": r.count} for r in result.all()]
    
    # Erreurs par heure (pour graphique)
    hour_col = func.date_trunc('hour', ErrorLog.created_at).label('hour')
    result = await db.execute(
        select(
            hour_col,
            func.count(ErrorLog.id).label('count')
        )
        .where(ErrorLog.created_at >= since)
        .group_by(hour_col)
        .order_by(hour_col)
    )
    hourly = [{"hour": r.hour.isoformat() if r.hour else None, "count": r.count} for r in result.all()]
    
    return {
        "period_hours": hours,
        "total": total,
        "by_code": by_code,
        "by_type": by_type,
        "by_source_app": by_source,
        "top_endpoints": top_endpoints,
        "hourly": hourly
    }


@router.get("/errors/new-count")
async def get_new_errors_count(
    since: Optional[str] = Query(None, description="ISO timestamp depuis lequel compter les nouvelles erreurs"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Compte le nombre de nouvelles erreurs depuis un timestamp donné.
    Utilisé pour le badge de notification dans la navigation.
    Requiert: admin Hub.
    """
    from app.models import ErrorLog
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    query = select(func.count(ErrorLog.id))
    
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace('Z', '+00:00')).replace(tzinfo=None)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Format de date invalide (ISO 8601 attendu)")
        query = query.where(ErrorLog.created_at > since_dt)
    
    result = await db.execute(query)
    count = result.scalar() or 0
    
    return {"count": count}


@router.get("/errors/{error_id}")
async def get_error_detail(
    error_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Détail d'une erreur (incluant le stack trace si disponible).
    Requiert: admin Hub
    """
    from app.models import ErrorLog
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    result = await db.execute(
        select(ErrorLog).where(ErrorLog.id == error_id)
    )
    error = result.scalar_one_or_none()
    
    if not error:
        raise HTTPException(status_code=404, detail="Erreur non trouvée")
    
    # Récupérer le username si user_id présent
    username = None
    if error.user_id:
        result = await db.execute(select(User).where(User.id == error.user_id))
        user = result.scalar_one_or_none()
        username = user.username if user else None
    
    # Résoudre le client_id du service account si acteur M2M
    service_account_client_id = None
    if error.service_account_id:
        from app.models import ServiceAccount
        result = await db.execute(
            select(ServiceAccount).where(ServiceAccount.id == error.service_account_id)
        )
        sa = result.scalar_one_or_none()
        service_account_client_id = sa.client_id if sa else None
    
    return {
        "id": error.id,
        "request_id": error.request_id,
        "error_code": error.error_code,
        "error_type": error.error_type,
        "error_message": error.error_message,
        "user_id": error.user_id,
        "username": username,
        "service_account_id": error.service_account_id,
        "service_account_client_id": service_account_client_id,
        "source_app_slug": error.source_app_slug,
        "request_method": error.request_method,
        "request_path": error.request_path,
        "request_body": error.request_body,
        "ip_address": error.ip_address,
        "user_agent": error.user_agent,
        "stack_trace": error.stack_trace,
        "created_at": error.created_at.isoformat()
    }


@router.get("/export/errors")
async def export_errors_csv(
    hours: int = Query(24, ge=1, le=168, description="Heures d'historique"),
    error_code: Optional[int] = Query(None, description="Filtrer par code HTTP"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Exporte les logs d'erreurs en CSV.
    Requiert: admin Hub
    """
    from app.models import ErrorLog
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    since = utcnow() - timedelta(hours=hours)
    
    query = select(ErrorLog).where(ErrorLog.created_at >= since)
    
    if error_code:
        query = query.where(ErrorLog.error_code == error_code)
    
    query = query.order_by(desc(ErrorLog.created_at))
    
    result = await db.execute(query)
    errors = result.scalars().all()
    
    # Résoudre les utilisateurs et service accounts rattachés aux erreurs
    user_ids = list(set(e.user_id for e in errors if e.user_id))
    users_map = {}
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u.username for u in result.scalars().all()}
    sa_ids = list(set(e.service_account_id for e in errors if e.service_account_id))
    sa_map = {}
    if sa_ids:
        from app.models import ServiceAccount
        result = await db.execute(select(ServiceAccount).where(ServiceAccount.id.in_(sa_ids)))
        sa_map = {s.id: s.client_id for s in result.scalars().all()}
    
    def _actor_label(e):
        if e.user_id:
            return users_map.get(e.user_id, f"user#{e.user_id}")
        if e.service_account_id:
            client_id = sa_map.get(e.service_account_id)
            return f"sa:{client_id}" if client_id else f"sa#{e.service_account_id}"
        return ''
    
    # Générer le CSV
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    
    # En-têtes
    writer.writerow([
        'Date/Heure', 'Request ID', 'Code', 'Type', 'Message',
        'Méthode', 'Chemin', 'App Source', 'Utilisateur', 'Adresse IP', 'User Agent'
    ])
    
    # Données
    for e in errors:
        writer.writerow([
            e.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            e.request_id or '',
            e.error_code,
            e.error_type or '',
            e.error_message or '',
            e.request_method or '',
            e.request_path or '',
            e.source_app_slug or '',
            _actor_label(e),
            e.ip_address or '',
            e.user_agent or ''
        ])
    
    output.seek(0)
    
    filename = f"error_logs_{utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# =============================================================================
# PURGE ET ACQUITTEMENT DES ERREURS
# =============================================================================

@router.delete("/errors")
async def purge_error_logs(
    hours: Optional[int] = Query(None, ge=1, le=168, description="Purger les erreurs de cette période (en heures). Si omis, purge toutes les erreurs."),
    error_code: Optional[int] = Query(None, description="Purger uniquement un code HTTP spécifique"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Purge les logs d'erreurs HTTP.
    Requiert: admin Hub.
    Filtres optionnels : période et/ou code HTTP.
    """
    from app.models import ErrorLog
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    query = delete(ErrorLog)
    
    if hours:
        since = utcnow() - timedelta(hours=hours)
        query = query.where(ErrorLog.created_at >= since)
    
    if error_code:
        query = query.where(ErrorLog.error_code == error_code)
    
    result = await db.execute(query)
    deleted_count = result.rowcount
    await db.commit()
    
    # Log l'action d'admin
    await log_action(
        db, current_user.id, "purge_errors",
        details=f"Purgé {deleted_count} erreurs (filtres: hours={hours}, code={error_code})",
        notify_sse=False
    )
    await db.commit()
    
    # Notifier les clients SSE
    _notify_audit()
    
    return {
        "deleted": deleted_count,
        "message": f"{deleted_count} erreur(s) supprimée(s)"
    }


@router.post("/errors/acknowledge")
async def acknowledge_errors(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Marque les erreurs comme consultées.
    Enregistre le timestamp de consultation pour réinitialiser le compteur
    de notifications sur la page erreurs.
    Requiert: admin Hub.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    now = utcnow()
    
    # Notifier les clients SSE que les erreurs ont été acquittées
    try:
        from app.routes.events import notify_event
        notify_event("errors_acknowledged", {
            "acknowledged_at": now.isoformat(),
            "acknowledged_by": current_user.username
        })
    except ImportError:
        pass
    
    return {
        "acknowledged_at": now.isoformat(),
        "message": "Erreurs marquées comme consultées"
    }


# =============================================================================
# VISUALISATIONS D'USAGE (segments par application)
# =============================================================================

@router.get("/usage/user/{user_id}")
async def get_user_usage_day(
    user_id: int,
    date: str = Query(None, description="Jour YYYY-MM-DD (défaut: aujourd'hui UTC)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Segments d'usage d'un utilisateur sur une journée (UTC).
    Les segments ouverts sont virtuellement clôturés à
    min(maintenant, last_activity_at + timeout session) — cohérent avec
    cleanup_stale_sessions.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    try:
        day = datetime.strptime(date, "%Y-%m-%d") if date else utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Date invalide (YYYY-MM-DD attendu)")

    day_start = day
    day_end = day + timedelta(days=1)

    # Segments chevauchant le jour, avec la session pour clamp des segments ouverts.
    # Exclusion des ateliers (slug finissant par « -ws ») : l'uptime utilisateur
    # ne mesure que l'usage production — la sandbox atelier n'est pas une app.
    result = await db.execute(
        select(AppUsageSegment, UserSession)
        .join(UserSession, AppUsageSegment.session_id == UserSession.id)
        .where(
            and_(
                AppUsageSegment.user_id == user_id,
                AppUsageSegment.started_at < day_end,
                or_(AppUsageSegment.ended_at == None,
                    AppUsageSegment.ended_at > day_start),  # noqa: E711
                ~AppUsageSegment.app_slug.endswith("-ws"),
            )
        )
    )
    rows = result.all()

    # Noms/couleurs des apps
    app_slugs = {seg.app_slug for seg, _ in rows}
    apps_by_slug = {}
    if app_slugs:
        apps_result = await db.execute(
            select(Application).where(Application.slug.in_(app_slugs))
        )
        for a in apps_result.scalars():
            apps_by_slug[a.slug] = a

    segments = []
    totals = {}
    for seg, session in rows:
        started = max(seg.started_at, day_start)
        if seg.ended_at is not None:
            raw_end = seg.ended_at
        else:
            # Segment ouvert : fin virtuelle cohérente avec cleanup_stale_sessions
            # (ended_at = last_activity + timeout), plafonnée à maintenant.
            raw_end = min(
                utcnow(),
                session.last_activity_at + timedelta(minutes=settings.session_timeout_minutes),
            )
        ended = min(raw_end, day_end)
        if ended <= started:
            continue
        app = apps_by_slug.get(seg.app_slug)
        if seg.ended_at is None:
            # Segment ouvert en cours d'utilisation : au moins 1 s de durée
            # (une utilisation active ne doit jamais apparaître à durée 0).
            duration = math.ceil((ended - started).total_seconds())
        else:
            duration = int((ended - started).total_seconds())
        segments.append({
            "app_slug": seg.app_slug,
            "app_name": app.name if app else seg.app_slug,
            "color": app.color if app else "#6c757d",
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "duration_seconds": duration,
            "end_reason": seg.end_reason,
        })
        tot = totals.setdefault(seg.app_slug, {
            "app_slug": seg.app_slug,
            "app_name": app.name if app else seg.app_slug,
            "color": app.color if app else "#6c757d",
            "total_seconds": 0,
        })
        tot["total_seconds"] += duration

    segments.sort(key=lambda s: s["started_at"])
    totals = sorted(totals.values(), key=lambda t: -t["total_seconds"])
    return {
        "user_id": user_id,
        "username": user.username,
        "date": day_start.strftime("%Y-%m-%d"),
        "segments": segments,
        "totals": totals,
        "day_total_seconds": sum(t["total_seconds"] for t in totals),
    }


@router.get("/usage/apps")
async def get_apps_usage_stats(
    days: int = Query(7, ge=1, le=90),
    app: Optional[str] = Query(None, description="Slug d'app (défaut: toutes)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Stats d'usage par application : simultanés (base 5 min, downsample affichage),
    uniques/jour, records (max+date+users, avg, min, p95, σ) sur les simultanés.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")

    end = utcnow()
    # Fenêtre alignée minuit UTC : `days` buckets quotidiens, le dernier = aujourd'hui
    # (requis par compute_daily_uniques / compute_activity_heatmap — fenêtres par offset depuis start)
    start = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)

    conditions = [
        AppUsageSegment.started_at < end,
        or_(AppUsageSegment.ended_at == None,
            AppUsageSegment.ended_at > start),  # noqa: E711
    ]
    if app:
        conditions.append(AppUsageSegment.app_slug == app)
    result = await db.execute(select(AppUsageSegment).where(and_(*conditions)))
    segments = result.scalars().all()

    if not segments:
        return {"period_days": days, "apps": []}

    # user_id -> username
    user_ids = {s.user_id for s in segments}
    users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
    user_names = {u.id: u.username for u in users_result.scalars()}

    app_slugs = {s.app_slug for s in segments}
    apps_result = await db.execute(
        select(Application).where(Application.slug.in_(app_slugs))
    )
    apps_by_slug = {a.slug: a for a in apps_result.scalars()}

    display_step = bucket_size_for_days(days)
    factor = max(1, display_step // BASE_BUCKET_MINUTES)

    # Des segments peuvent se terminer après "maintenant" (commit concurrent,
    # horloge, artefact de données) : étendre la fenêtre de la série à la fin
    # réelle des données pour que les derniers buckets les voient.
    series_end = max([s.ended_at for s in segments if s.ended_at] + [end])

    def _aggregate() -> list[dict]:
        """Agrégation CPU-bound (fonctions pures) — exécutée hors event loop."""
        apps_out = []
        for slug in sorted(app_slugs):
            entries = [
                {"app_slug": slug, "user_id": s.user_id,
                 "started_at": s.started_at, "ended_at": s.ended_at}
                for s in segments if s.app_slug == slug
            ]
            base_series = compute_simultaneous_series(
                entries, start, series_end, BASE_BUCKET_MINUTES, user_names
            )
            app_obj = apps_by_slug.get(slug)
            apps_out.append({
                "slug": slug,
                "name": app_obj.name if app_obj else slug,
                "icon": app_obj.icon if app_obj else "bi-grid",
                "color": app_obj.color if app_obj else "#6c757d",
                "simultaneous": [
                    {"t": p["t"], "count": p["count"]}
                    for p in downsample_max(base_series, factor)
                ],
                "daily_uniques": compute_daily_uniques(entries, start, days),
                "records": compute_records(base_series),
            })
        return apps_out

    apps_out = await anyio.to_thread.run_sync(_aggregate)

    return {"period_days": days, "apps": apps_out}


@router.get("/usage/heatmap")
async def get_usage_heatmap(
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Heatmap d'activité du parc : sessions actives par jour × heure (UTC)."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")

    end = utcnow()
    # Fenêtre alignée minuit UTC : `days` buckets quotidiens, le dernier = aujourd'hui
    # (requis par compute_daily_uniques / compute_activity_heatmap — fenêtres par offset depuis start)
    start = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)

    result = await db.execute(
        select(UserSession.login_at, UserSession.logout_at).where(
            and_(
                UserSession.login_at < end,
                or_(UserSession.logout_at == None,
                    UserSession.logout_at > start),  # noqa: E711
            )
        )
    )
    sessions = [(row[0], row[1]) for row in result.all()]

    def _aggregate() -> list[dict]:
        """Agrégation CPU-bound (fonction pure) — exécutée hors event loop."""
        return compute_activity_heatmap(sessions, start, days)

    return {
        "period_days": days,
        "days": await anyio.to_thread.run_sync(_aggregate),
    }
