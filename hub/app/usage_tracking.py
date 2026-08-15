"""
Ouverture/clôture des segments d'usage par application.

Branché sur le cycle de vie des sessions UserSession :
- bascule d'app (update_session_activity) -> clôture 'switch' + ouverture
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


async def _get_open_segment(db: AsyncSession, session_id: int) -> Optional[AppUsageSegment]:
    result = await db.execute(
        select(AppUsageSegment).where(
            and_(
                AppUsageSegment.session_id == session_id,
                AppUsageSegment.ended_at == None,  # noqa: E711
            )
        ).order_by(AppUsageSegment.started_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def close_open_segments(
    db: AsyncSession, session_id: int, ended_at: datetime, end_reason: str
) -> int:
    """Clôture tous les segments ouverts d'une session (défensif : normalement 0 ou 1)."""
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


async def record_app_switch(db: AsyncSession, session: UserSession, app_slug: str) -> None:
    """À l'accès à une app : clôture le segment ouvert si l'app change, ouvre un nouveau."""
    open_seg = await _get_open_segment(db, session.id)
    now = utcnow()
    if open_seg is not None:
        if open_seg.app_slug == app_slug:
            return  # même app, pas de nouveau segment
        open_seg.ended_at = max(now, open_seg.started_at)
        open_seg.end_reason = "switch"
    db.add(AppUsageSegment(
        session_id=session.id,
        user_id=session.user_id,
        app_slug=app_slug,
        started_at=now,
    ))


async def close_segments_for_session(
    db: AsyncSession,
    session: UserSession,
    end_reason: str,
    ended_at: Optional[datetime] = None,
) -> None:
    """Clôture le segment ouvert d'une session à sa fermeture."""
    await close_open_segments(db, session.id, ended_at or utcnow(), end_reason)
