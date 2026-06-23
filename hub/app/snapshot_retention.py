"""
Politique de retention des snapshots: nombre max + age minimum.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
from app.models import HubSnapshot
from app.snapshot_config import get_snapshot_config

logger = logging.getLogger(__name__)


async def apply_retention(db: AsyncSession, max_count=7, min_age_hours=24) -> int:
    config = await get_snapshot_config(db)
    s3_endpoint = config.get("s3_endpoint", "")
    s3_access_key = config.get("s3_access_key", "")
    s3_secret_key = config.get("s3_secret_key", "")
    s3_region = config.get("s3_region", "eu-west-1")

    result = await db.execute(
        select(HubSnapshot)
        .where(HubSnapshot.status == "success")
        .order_by(desc(HubSnapshot.created_at))
    )
    snapshots = result.scalars().all()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    kept = 0
    deleted = 0

    for s in snapshots:
        age_hours = (
            (now - (s.created_at or now)).total_seconds() / 3600
            if s.created_at else 0
        )

        if age_hours < min_age_hours:
            kept += 1
            continue
        if kept >= max_count:
            if s.local_path and Path(s.local_path).exists():
                Path(s.local_path).unlink(missing_ok=True)
            if s.s3_key and s3_endpoint and s3_access_key:
                from app.snapshot_s3 import delete_from_s3
                delete_from_s3(s.s3_bucket or "", s.s3_key, s3_endpoint, s3_access_key, s3_secret_key, s3_region)
            await db.delete(s)
            deleted += 1
        else:
            kept += 1

    if deleted:
        await db.commit()
        logger.info(f"Retention: {deleted} snapshots supprimes, {kept} conserves")

    return deleted
