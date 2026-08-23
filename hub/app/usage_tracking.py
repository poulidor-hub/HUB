"""
Ouverture/clôture des segments d'usage par application.

Branché sur le cycle de vie des sessions UserSession :
- présence dans une app (update_session_activity) -> ouverture d'un segment par (session, app)
- déconnexion SSE de l'app (handle_app_disconnect) -> clôture 'disconnect'
- logout (end_session)                    -> clôture 'logout'
- timeout (cleanup_stale_sessions)        -> clôture 'timeout'
- révocation admin (revoke_session)       -> clôture 'forced'
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppUsageSegment, UserSession, utcnow

# Raisons UserSession -> raisons AppUsageSegment
SEGMENT_REASON_MAP = {
    "explicit": "logout",
    "timeout": "timeout",
    "token_revoked": "forced",
    "browser_closed": "logout",
    "sse_disconnect": "timeout",
}


async def _get_open_segment(
    db: AsyncSession, session_id: int, app_slug: Optional[str] = None
) -> Optional[AppUsageSegment]:
    conditions = [
        AppUsageSegment.session_id == session_id,
        AppUsageSegment.ended_at == None,  # noqa: E711
    ]
    if app_slug is not None:
        conditions.append(AppUsageSegment.app_slug == app_slug)
    result = await db.execute(
        select(AppUsageSegment).where(and_(*conditions))
        .order_by(AppUsageSegment.started_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def close_open_segments(
    db: AsyncSession, session_id: int, ended_at: datetime, end_reason: str
) -> int:
    """Clôture tous les segments ouverts d'une session.

    Usage parallèle : N segments ouverts par (session, app) est le cas normal ;
    ceux encore ouverts à la fermeture de session (leur connexion SSE n'a pas
    signalé de départ) sont clos ici d'un coup."""
    result = await db.execute(
        select(AppUsageSegment).where(
            and_(
                AppUsageSegment.session_id == session_id,
                AppUsageSegment.ended_at == None,  # noqa: E711
            )
        )
    )
    segments = result.scalars().all()
    for seg in segments:
        # Clamp défensif : jamais de ended_at antérieur au started_at
        seg.ended_at = max(ended_at, seg.started_at)
        seg.end_reason = end_reason
    return len(segments)


async def close_app_segment(
    db: AsyncSession, session_id: int, app_slug: str, ended_at: datetime, end_reason: str
) -> int:
    """Ferme le(s) segment(s) ouverts d'un couple (session, app) uniquement."""
    result = await db.execute(
        select(AppUsageSegment).where(and_(
            AppUsageSegment.session_id == session_id,
            AppUsageSegment.app_slug == app_slug,
            AppUsageSegment.ended_at == None,  # noqa: E711
        ))
    )
    segments = result.scalars().all()
    for seg in segments:
        seg.ended_at = max(ended_at, seg.started_at)
        seg.end_reason = end_reason
    return len(segments)


async def touch_app_presence(db: AsyncSession, session: UserSession, app_slug: str) -> None:
    """Présence dans une app : ouvre un segment par couple (session, app).
    Les segments des autres apps restent ouverts (usage parallèle multi-onglets) ;
    ils sont fermés par leur propre connexion SSE ou à la fermeture de session."""
    open_seg = await _get_open_segment(db, session.id, app_slug)
    if open_seg is not None:
        return
    db.add(AppUsageSegment(
        session_id=session.id,
        user_id=session.user_id,
        app_slug=app_slug,
        started_at=utcnow(),
    ))


async def close_segments_for_session(
    db: AsyncSession,
    session: UserSession,
    end_reason: str,
    ended_at: Optional[datetime] = None,
) -> None:
    """Clôture le segment ouvert d'une session à sa fermeture."""
    await close_open_segments(db, session.id, ended_at or utcnow(), end_reason)
