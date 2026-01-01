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
from sqlalchemy import select, func, and_, or_, desc
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
from typing import Optional, List
import csv
import io

from app.database import get_db
from app.models import User, UserSession, AuditLog, Application
from app.auth import get_current_user, get_settings

router = APIRouter(prefix="/api/audit", tags=["Audit"])
settings = get_settings()


# =============================================================================
# FONCTIONS UTILITAIRES POUR AUDIT LOG
# =============================================================================

async def log_action(
    db: AsyncSession,
    user_id: int,
    action: str,
    target: str = None,
    ip_address: str = None,
    user_agent: str = None,
    details: str = None
):
    """
    Enregistre une action dans les logs d'audit.
    Actions supportées: login, logout, access_app, etc.
    """
    log_entry = AuditLog(
        user_id=user_id,
        action=action,
        target=target,
        ip_address=ip_address,
        user_agent=user_agent[:255] if user_agent else None,
        details=details
    )
    db.add(log_entry)
    return log_entry


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
    # Fermer les éventuelles sessions orphelines de cet utilisateur
    timeout = datetime.utcnow() - timedelta(minutes=settings.session_timeout_minutes)
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
        # Si dernière activité > timeout, fermer comme timeout
        session.logout_at = session.login_at + timedelta(minutes=settings.session_timeout_minutes)
        session.logout_reason = "timeout"
    
    # Créer la nouvelle session
    new_session = UserSession(
        user_id=user_id,
        login_at=datetime.utcnow(),
        ip_address=ip_address,
        user_agent=user_agent[:255] if user_agent else None
    )
    db.add(new_session)
    
    return new_session


async def end_session(
    db: AsyncSession,
    user_id: int,
    reason: str = "explicit"
) -> Optional[UserSession]:
    """
    Termine la session active d'un utilisateur.
    """
    result = await db.execute(
        select(UserSession).where(
            and_(
                UserSession.user_id == user_id,
                UserSession.logout_at == None
            )
        ).order_by(UserSession.login_at.desc())
    )
    session = result.scalar_one_or_none()
    
    if session:
        session.logout_at = datetime.utcnow()
        session.logout_reason = reason
    
    return session


async def cleanup_stale_sessions(db: AsyncSession) -> int:
    """
    Ferme les sessions inactives (pas de refresh depuis session_timeout_minutes).
    À appeler périodiquement (ex: toutes les heures).
    Retourne le nombre de sessions fermées.
    """
    timeout = datetime.utcnow() - timedelta(minutes=settings.session_timeout_minutes)
    
    result = await db.execute(
        select(UserSession).where(
            and_(
                UserSession.logout_at == None,
                UserSession.login_at < timeout
            )
        )
    )
    stale_sessions = result.scalars().all()
    
    for session in stale_sessions:
        session.logout_at = session.login_at + timedelta(minutes=settings.session_timeout_minutes)
        session.logout_reason = "timeout"
    
    return len(stale_sessions)


# =============================================================================
# ENDPOINTS API
# =============================================================================

@router.get("/sessions/active")
async def get_active_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Liste les utilisateurs actuellement connectés.
    Requiert: admin Hub
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    # Nettoyer les sessions expirées d'abord
    cleaned = await cleanup_stale_sessions(db)
    
    result = await db.execute(
        select(UserSession)
        .options(selectinload(UserSession.user))
        .where(UserSession.logout_at == None)
        .order_by(UserSession.login_at.desc())
    )
    active_sessions = result.scalars().all()
    
    return {
        "stale_sessions_cleaned": cleaned,
        "active_count": len(active_sessions),
        "sessions": [
            {
                "user_id": s.user_id,
                "username": s.user.username if s.user else None,
                "login_at": s.login_at.isoformat(),
                "duration_minutes": int((datetime.utcnow() - s.login_at).total_seconds() / 60),
                "ip_address": s.ip_address
            }
            for s in active_sessions
        ]
    }


@router.get("/sessions/history")
async def get_session_history(
    user_id: Optional[int] = Query(None, description="Filtrer par utilisateur"),
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
    
    # Si non-admin, forcer à ses propres sessions
    if not current_user.is_admin:
        user_id = current_user.id
    
    since = datetime.utcnow() - timedelta(days=days)
    
    query = select(UserSession).options(selectinload(UserSession.user)).where(
        UserSession.login_at >= since
    )
    
    if user_id:
        query = query.where(UserSession.user_id == user_id)
    
    query = query.order_by(UserSession.login_at.desc())
    
    result = await db.execute(query)
    sessions = result.scalars().all()
    
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
                "ip_address": s.ip_address
            }
            for s in sessions
        ]
    }


@router.get("/stats")
async def get_connection_stats(
    days: int = Query(7, ge=1, le=365, description="Période en jours"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Statistiques globales de connexion.
    Requiert: admin Hub
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    since = datetime.utcnow() - timedelta(days=days)
    
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
    
    since = datetime.utcnow() - timedelta(days=days)
    
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
            int((datetime.utcnow() - last_session.login_at).total_seconds())
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
    user_id: Optional[int] = Query(None, description="Filtrer par utilisateur"),
    page: int = Query(1, ge=1, description="Numéro de page"),
    page_size: int = Query(50, ge=10, le=200, description="Éléments par page"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Liste paginée des logs d'audit détaillés.
    Requiert: admin Hub (ou ses propres logs si user_id=self)
    """
    # Vérification des droits
    if not current_user.is_admin and user_id and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    # Si non-admin, forcer à ses propres logs
    if not current_user.is_admin:
        user_id = current_user.id
    
    since = datetime.utcnow() - timedelta(days=days)
    
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
    
    since = datetime.utcnow() - timedelta(days=days)
    
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
    
    # Vérifier que l'app existe
    result = await db.execute(select(Application).where(Application.slug == app_slug))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application non trouvée")
    
    since = datetime.utcnow() - timedelta(days=days)
    
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
            "slug": app.slug,
            "name": app.name,
            "icon": app.icon,
            "color": app.color
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Exporte les logs d'audit en CSV.
    Requiert: admin Hub
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Accès admin requis")
    
    since = datetime.utcnow() - timedelta(days=days)
    
    query = select(AuditLog).where(AuditLog.created_at >= since)
    
    if action:
        query = query.where(AuditLog.action == action)
    if target:
        query = query.where(AuditLog.target == target)
    
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
    
    filename = f"audit_logs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    
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
    
    since = datetime.utcnow() - timedelta(days=days)
    
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
    
    filename = f"sessions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )