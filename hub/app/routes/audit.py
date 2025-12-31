"""
Routes d'audit - Statistiques de connexion des utilisateurs

Système léger qui ne stocke que les changements d'état (login/logout).
Permet de connaître:
- Le nombre de connexions par utilisateur/application/période
- Le temps de connexion (durée des sessions)
- Les utilisateurs actuellement connectés
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
from typing import Optional, List

from app.database import get_db
from app.models import User, UserSession, AuditLog
from app.auth import get_current_user, get_settings

router = APIRouter(prefix="/api/audit", tags=["Audit"])
settings = get_settings()


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
