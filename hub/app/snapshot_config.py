# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Lecture/ecriture de la configuration snapshot depuis app_settings.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import AppSetting, utcnow


async def get_snapshot_config(db: AsyncSession) -> dict:
    result = await db.execute(
        select(AppSetting).where(
            AppSetting.key.in_([
                "snapshot_cron", "snapshot_retention_max",
                "snapshot_retention_min_age_hours",
                "s3_endpoint", "s3_bucket", "s3_region",
                "s3_access_key", "s3_secret_key", "s3_prefix",
            ])
        )
    )
    rows = {s.key: s.value for s in result.scalars().all()}
    return {
        "snapshot_cron": rows.get("snapshot_cron", ""),
        "snapshot_retention_max": int(rows.get("snapshot_retention_max", "7")),
        "snapshot_retention_min_age_hours": int(rows.get("snapshot_retention_min_age_hours", "24")),
        "s3_endpoint": rows.get("s3_endpoint", ""),
        "s3_bucket": rows.get("s3_bucket", "hub-snapshots"),
        "s3_region": rows.get("s3_region", "eu-west-1"),
        "s3_access_key": rows.get("s3_access_key", ""),
        "s3_secret_key": rows.get("s3_secret_key", ""),
        "s3_prefix": rows.get("s3_prefix", "prod/"),
    }


async def update_snapshot_config(db: AsyncSession, updates: dict) -> dict:
    key_map = {
        "snapshot_cron": "snapshot_cron",
        "snapshot_retention_max": "snapshot_retention_max",
        "snapshot_retention_min_age_hours": "snapshot_retention_min_age_hours",
        "s3_endpoint": "s3_endpoint",
        "s3_bucket": "s3_bucket",
        "s3_region": "s3_region",
        "s3_access_key": "s3_access_key",
        "s3_secret_key": "s3_secret_key",
        "s3_prefix": "s3_prefix",
    }

    result = await db.execute(
        select(AppSetting).where(
            AppSetting.key.in_(list(key_map.values()))
        )
    )
    existing = {s.key: s for s in result.scalars().all()}

    for field, setting_key in key_map.items():
        if field in updates and updates[field] is not None:
            value = str(updates[field])
            if setting_key in existing:
                existing[setting_key].value = value
                existing[setting_key].updated_at = utcnow()
            else:
                db.add(AppSetting(key=setting_key, value=value))

    await db.commit()
    return await get_snapshot_config(db)
