"""Routes admin pour les clusters de sauvegarde."""

from datetime import datetime
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_admin
from app.database import get_db
from app.models import Application, AppBackup, BackupCluster, User, utcnow
from app.routes.app_backups import apply_retention_policy, human_readable_size
from app.routes.events import notify_event
from app.schemas import (
    AppBackupConfigUpdate,
    BackupClusterCreate,
    BackupClusterListResponse,
    BackupClusterResponse,
    BackupClusterAppResponse,
    BackupClusterUpdate,
    BackupModeEnum,
)

router = APIRouter(prefix="/api/admin/backup-clusters", tags=["Backup Clusters"])


def _success_backups(app: Application) -> list[AppBackup]:
    return [
        backup for backup in (app.backups or [])
        if (getattr(backup, "status", "success") or "success") == "success"
    ]


def _cluster_app_response(app: Application) -> BackupClusterAppResponse:
    success_backups = _success_backups(app)
    total_size = sum(backup.total_size_bytes or 0 for backup in success_backups)
    return BackupClusterAppResponse(
        app_id=app.id,
        app_slug=app.slug,
        app_name=app.name,
        is_system_app=bool(getattr(app, "is_system_app", False)),
        backup_mode=BackupModeEnum(app.backup_mode or "manual"),
        backup_cron=app.backup_cron,
        backup_retention=app.backup_retention or 5,
        last_backup_at=app.last_backup_at,
        last_backup_status=app.last_backup_status,
        next_backup_at=app.next_backup_at,
        backups_count=len(success_backups),
        total_size_bytes=total_size,
        total_size_human=human_readable_size(total_size),
    )


def _build_cluster_response(cluster: BackupCluster) -> BackupClusterResponse:
    apps = sorted(cluster.applications or [], key=lambda app: app.name.lower())
    app_items = [_cluster_app_response(app) for app in apps]
    total_backups = sum(item.backups_count for item in app_items)
    total_size = sum(item.total_size_bytes for item in app_items)
    scheduled_apps = sum(1 for item in app_items if item.backup_mode == BackupModeEnum.scheduled)
    return BackupClusterResponse(
        id=cluster.id,
        name=cluster.name,
        slug=cluster.slug,
        description=cluster.description,
        color=cluster.color or "#0d6efd",
        apps=app_items,
        apps_count=len(app_items),
        total_backups=total_backups,
        total_size_bytes=total_size,
        total_size_human=human_readable_size(total_size),
        scheduled_apps=scheduled_apps,
        created_at=cluster.created_at,
        updated_at=cluster.updated_at,
    )


async def _load_cluster_or_404(db: AsyncSession, cluster_id: int) -> BackupCluster:
    result = await db.execute(
        select(BackupCluster)
        .options(selectinload(BackupCluster.applications).selectinload(Application.backups))
        .where(BackupCluster.id == cluster_id)
    )
    cluster = result.scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster introuvable")
    return cluster


async def _load_apps_by_ids(db: AsyncSession, app_ids: Iterable[int]) -> list[Application]:
    unique_ids = sorted(set(app_ids))
    if not unique_ids:
        return []
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.backups))
        .where(Application.id.in_(unique_ids))
    )
    apps = result.scalars().all()
    found_ids = {app.id for app in apps}
    missing_ids = set(unique_ids) - found_ids
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Applications introuvables: {sorted(missing_ids)}",
        )
    return sorted(apps, key=lambda app: app.name.lower())


async def _ensure_unique_cluster(
    db: AsyncSession,
    *,
    name: str | None = None,
    slug: str | None = None,
    exclude_id: int | None = None,
) -> None:
    if name:
        result = await db.execute(select(BackupCluster).where(BackupCluster.name == name))
        existing = result.scalar_one_or_none()
        if existing and existing.id != exclude_id:
            raise HTTPException(status_code=400, detail=f"Un cluster nommé '{name}' existe déjà")
    if slug:
        result = await db.execute(select(BackupCluster).where(BackupCluster.slug == slug))
        existing = result.scalar_one_or_none()
        if existing and existing.id != exclude_id:
            raise HTTPException(status_code=400, detail=f"Un cluster avec le slug '{slug}' existe déjà")


@router.get("/", response_model=BackupClusterListResponse)
async def list_backup_clusters(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    result = await db.execute(
        select(BackupCluster)
        .options(selectinload(BackupCluster.applications).selectinload(Application.backups))
        .order_by(BackupCluster.name)
    )
    clusters = result.scalars().all()
    return BackupClusterListResponse(
        clusters=[_build_cluster_response(cluster) for cluster in clusters],
        total=len(clusters),
    )


@router.post("/", response_model=BackupClusterResponse, status_code=status.HTTP_201_CREATED)
async def create_backup_cluster(
    payload: BackupClusterCreate,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    await _ensure_unique_cluster(db, name=payload.name, slug=payload.slug)
    apps = await _load_apps_by_ids(db, payload.app_ids)
    cluster = BackupCluster(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        color=payload.color,
        created_by_id=current_admin.id,
    )
    cluster.applications = apps
    db.add(cluster)
    await db.commit()

    cluster = await _load_cluster_or_404(db, cluster.id)
    notify_event("backup_clusters_updated", {"action": "created", "cluster_id": cluster.id, "slug": cluster.slug})
    return _build_cluster_response(cluster)


@router.get("/{cluster_id}", response_model=BackupClusterResponse)
async def get_backup_cluster(
    cluster_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    cluster = await _load_cluster_or_404(db, cluster_id)
    return _build_cluster_response(cluster)


@router.put("/{cluster_id}", response_model=BackupClusterResponse)
async def update_backup_cluster(
    cluster_id: int,
    payload: BackupClusterUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    cluster = await _load_cluster_or_404(db, cluster_id)
    await _ensure_unique_cluster(db, name=payload.name, slug=payload.slug, exclude_id=cluster_id)

    if payload.name is not None:
        cluster.name = payload.name
    if payload.slug is not None:
        cluster.slug = payload.slug
    if payload.description is not None:
        cluster.description = payload.description
    if payload.color is not None:
        cluster.color = payload.color
    if payload.app_ids is not None:
        cluster.applications = await _load_apps_by_ids(db, payload.app_ids)
    cluster.updated_at = utcnow()
    await db.commit()

    cluster = await _load_cluster_or_404(db, cluster_id)
    notify_event("backup_clusters_updated", {"action": "updated", "cluster_id": cluster.id, "slug": cluster.slug})
    return _build_cluster_response(cluster)


@router.delete("/{cluster_id}")
async def delete_backup_cluster(
    cluster_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    cluster = await _load_cluster_or_404(db, cluster_id)
    slug = cluster.slug
    await db.delete(cluster)
    await db.commit()
    notify_event("backup_clusters_updated", {"action": "deleted", "cluster_id": cluster_id, "slug": slug})
    return {"message": f"Cluster '{slug}' supprimé"}


@router.put("/{cluster_id}/backup/config", response_model=BackupClusterResponse)
async def update_cluster_backup_config(
    cluster_id: int,
    config: AppBackupConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    from croniter import croniter

    cluster = await _load_cluster_or_404(db, cluster_id)
    if not cluster.applications:
        raise HTTPException(status_code=400, detail="Le cluster ne contient aucune application")

    if config.backup_cron:
        try:
            croniter(config.backup_cron)
        except Exception:
            raise HTTPException(status_code=400, detail="Expression cron invalide")

    retention_updates: list[tuple[str, int]] = []
    for app in cluster.applications:
        if config.backup_mode is not None:
            app.backup_mode = config.backup_mode.value
        if config.backup_cron is not None:
            app.backup_cron = config.backup_cron or None
            if app.backup_cron and app.backup_mode == "scheduled":
                app.next_backup_at = croniter(app.backup_cron, utcnow()).get_next(datetime)
            else:
                app.next_backup_at = None
        elif app.backup_mode == "scheduled" and not app.backup_cron:
            raise HTTPException(
                status_code=400,
                detail=f"L'application {app.slug} ne peut pas passer en planifié sans cron",
            )
        if config.backup_retention is not None:
            app.backup_retention = config.backup_retention
            retention_updates.append((app.slug, config.backup_retention))
        if config.backup_quiesce is not None:
            app.backup_quiesce = bool(config.backup_quiesce)

    await db.commit()
    for app_slug, retention in retention_updates:
        await apply_retention_policy(db, app_slug, retention)

    cluster = await _load_cluster_or_404(db, cluster_id)
    notify_event("backup_clusters_updated", {"action": "config_updated", "cluster_id": cluster.id, "slug": cluster.slug})
    return _build_cluster_response(cluster)
