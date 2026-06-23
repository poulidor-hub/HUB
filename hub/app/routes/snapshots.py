"""
Routes de gestion des snapshots complets du Hub.

Endpoints:
- GET  /api/admin/snapshots              : Liste des snapshots
- POST /api/admin/snapshots              : Declencher un snapshot
- GET  /api/admin/snapshots/in-progress  : Snapshot en cours
- GET  /api/admin/snapshots/config       : Lire la config snapshot
- PUT  /api/admin/snapshots/config       : Modifier la config snapshot
- GET  /api/admin/snapshots/{id}         : Detail d'un snapshot
- DELETE /api/admin/snapshots/{id}       : Supprimer un snapshot
- POST /api/admin/snapshots/{id}/restore : Restaurer un snapshot (hub existant)
- GET  /api/admin/snapshots/{id}/download: Telecharger un snapshot
- POST /api/admin/snapshots/{id}/restore/preview : Previsualiser le contenu
"""
import asyncio
import io
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

import zstandard as zstd
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.database import get_db
from app.models import HubSnapshot, User, utcnow
from app.auth import get_current_admin
from app.schemas import (
    HubSnapshotResponse,
    HubSnapshotListResponse,
    HubSnapshotProgressResponse,
    HubSnapshotConfigResponse,
    HubSnapshotConfigUpdate,
    HubSnapshotRestoreRequest,
    HubSnapshotRestoreResponse,
    HubSnapshotCreateResponse,
)
from app.snapshot_config import get_snapshot_config, update_snapshot_config
from app.snapshot_s3 import download_from_s3 as s3_download, delete_from_s3 as s3_delete

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/snapshots", tags=["Snapshots"])


async def _get_snapshot_or_404(db: AsyncSession, snapshot_id: str) -> HubSnapshot:
    result = await db.execute(
        select(HubSnapshot).where(HubSnapshot.snapshot_id == snapshot_id)
    )
    snap = result.scalar_one_or_none()
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot introuvable")
    return snap


async def _broadcast(event_type: str, payload: dict):
    try:
        from app.routes.events import broadcast_event
        await broadcast_event(event_type, payload)
    except ImportError:
        pass


@router.get("/", response_model=HubSnapshotListResponse)
async def list_snapshots(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    result = await db.execute(
        select(HubSnapshot).order_by(desc(HubSnapshot.created_at))
    )
    snapshots = result.scalars().all()
    return HubSnapshotListResponse(
        snapshots=[HubSnapshotResponse.from_orm_model(s) for s in snapshots],
        total=len(snapshots),
    )


@router.post("/", response_model=HubSnapshotCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_snapshot_endpoint(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    result = await db.execute(
        select(HubSnapshot).where(HubSnapshot.status == "running")
    )
    running = result.scalar_one_or_none()
    if running:
        raise HTTPException(status_code=409, detail="Un snapshot est deja en cours")

    sid = str(uuid.uuid4())

    snap = HubSnapshot(
        snapshot_id=sid,
        status="pending",
        progress_step="queued",
        created_by=f"admin:{current_admin.username}",
        created_at=utcnow(),
    )
    db.add(snap)
    await db.commit()

    async def _run():
        from app.database import async_session_maker
        async with async_session_maker() as bg_db:
            try:
                from app.snapshot_engine import create_snapshot as do_snap
                await do_snap(bg_db, created_by=f"admin:{current_admin.username}", snapshot_id=sid)
            except Exception as e:
                logger.error(f"Snapshot background echoue: {e}")

    background_tasks.add_task(_run)

    await _broadcast("snapshot_created", {"snapshot_id": sid, "status": "pending"})

    return HubSnapshotCreateResponse(
        snapshot_id=sid,
        status="pending",
        message="Snapshot declenche",
    )


@router.get("/in-progress", response_model=HubSnapshotProgressResponse)
async def get_in_progress_snapshot(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    result = await db.execute(
        select(HubSnapshot)
        .where(HubSnapshot.status.in_(["pending", "running", "assembling", "uploading"]))
        .order_by(desc(HubSnapshot.created_at))
        .limit(1)
    )
    snap = result.scalar_one_or_none()
    if not snap:
        return HubSnapshotProgressResponse(
            snapshot_id="",
            status="idle",
            progress_percent=0,
            progress_step=None,
        )
    return HubSnapshotProgressResponse(
        snapshot_id=snap.snapshot_id,
        status=snap.status,
        progress_percent=snap.progress_percent or 0,
        progress_step=snap.progress_step,
        error_message=snap.error_message,
    )


@router.get("/config", response_model=HubSnapshotConfigResponse)
async def get_snapshot_config_endpoint(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    config = await get_snapshot_config(db)
    return HubSnapshotConfigResponse(
        snapshot_cron=config["snapshot_cron"],
        snapshot_retention_max=config["snapshot_retention_max"],
        snapshot_retention_min_age_hours=config["snapshot_retention_min_age_hours"],
        s3_endpoint=config["s3_endpoint"],
        s3_bucket=config["s3_bucket"],
        s3_region=config["s3_region"],
        s3_prefix=config["s3_prefix"],
        s3_configured=bool(config["s3_endpoint"] and config["s3_access_key"]),
    )


@router.put("/config", response_model=HubSnapshotConfigResponse)
async def update_snapshot_config_endpoint(
    payload: HubSnapshotConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    updates = payload.model_dump(exclude_unset=True)
    config = await update_snapshot_config(db, updates)
    await _broadcast("snapshot_config_updated", {})
    return HubSnapshotConfigResponse(
        snapshot_cron=config["snapshot_cron"],
        snapshot_retention_max=config["snapshot_retention_max"],
        snapshot_retention_min_age_hours=config["snapshot_retention_min_age_hours"],
        s3_endpoint=config["s3_endpoint"],
        s3_bucket=config["s3_bucket"],
        s3_region=config["s3_region"],
        s3_prefix=config["s3_prefix"],
        s3_configured=bool(config["s3_endpoint"] and config["s3_access_key"]),
    )


@router.get("/{snapshot_id}", response_model=HubSnapshotResponse)
async def get_snapshot(
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    snap = await _get_snapshot_or_404(db, snapshot_id)
    return HubSnapshotResponse.from_orm_model(snap)


@router.delete("/{snapshot_id}")
async def delete_snapshot(
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    snap = await _get_snapshot_or_404(db, snapshot_id)
    if snap.status == "running":
        raise HTTPException(status_code=409, detail="Impossible de supprimer un snapshot en cours")

    if snap.local_path and Path(snap.local_path).exists():
        Path(snap.local_path).unlink(missing_ok=True)

    config = await get_snapshot_config(db)
    if snap.s3_key and config.get("s3_endpoint"):
        s3_delete(
            snap.s3_bucket or "", snap.s3_key,
            config["s3_endpoint"], config["s3_access_key"], config["s3_secret_key"],
            config.get("s3_region", "eu-west-1"),
        )

    await db.delete(snap)
    await db.commit()

    await _broadcast("snapshot_deleted", {"snapshot_id": snapshot_id})
    return {"detail": "Snapshot supprime"}


@router.post("/{snapshot_id}/restore/preview", response_model=dict)
async def preview_snapshot(
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    snap = await _get_snapshot_or_404(db, snapshot_id)
    return {
        "snapshot_id": snap.snapshot_id,
        "status": snap.status,
        "created_at": str(snap.created_at) if snap.created_at else None,
        "total_size_bytes": snap.total_size_bytes,
        "collectors": snap.manifest.get("collectors", {}) if snap.manifest else {},
        "apps": snap.manifest.get("apps", []) if snap.manifest else [],
        "apps_succeeded": snap.apps_succeeded,
        "apps_failed": snap.apps_failed,
    }


@router.post("/{snapshot_id}/restore", response_model=HubSnapshotRestoreResponse)
async def restore_snapshot(
    snapshot_id: str,
    payload: HubSnapshotRestoreRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    if not payload.confirm_full_restore:
        raise HTTPException(
            status_code=400,
            detail="confirm_full_restore=true est requis pour restaurer un snapshot complet",
        )

    snap = await _get_snapshot_or_404(db, snapshot_id)
    if snap.status != "success":
        raise HTTPException(status_code=400, detail="Seuls les snapshots reussis peuvent etre restaures")

    config = await get_snapshot_config(db)

    async def _do_restore():
        tmp = Path(tempfile.mkdtemp(prefix="hub_restore_"))
        try:
            enc_path = tmp / "snapshot.tar.zst.enc"
            if snap.local_path and Path(snap.local_path).exists():
                shutil.copy2(snap.local_path, enc_path)
            elif snap.s3_key and config.get("s3_endpoint"):
                await s3_download(
                    snap.s3_bucket or "", snap.s3_key, enc_path,
                    config["s3_endpoint"], config["s3_access_key"], config["s3_secret_key"],
                    config.get("s3_region", "eu-west-1"),
                )
            else:
                logger.error("Snapshot non disponible (ni stockage local ni S3)")
                return

            data = enc_path.read_bytes()
            key_len = int.from_bytes(data[:4], "big")
            enc_key = data[4:4 + key_len]
            nonce = data[4 + key_len:4 + key_len + 12]
            ciphertext = data[4 + key_len + 12:]

            secrets_dir = Path("/code/secrets")
            with open(secrets_dir / "jwt_private_key.pem", "rb") as f:
                priv_key = serialization.load_pem_private_key(f.read(), password=None)

            aes_key = priv_key.decrypt(
                enc_key,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            aesgcm = AESGCM(aes_key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)

            tar_zst_path = tmp / "snapshot.tar.zst"
            tar_zst_path.write_bytes(plaintext)

            dctx = zstd.ZstdDecompressor()
            tar_path_data = tmp / "snapshot.tar"
            tar_path_data.write_bytes(dctx.decompress(tar_zst_path.read_bytes()))

            extract_dir = tmp / "extract"
            extract_dir.mkdir()
            shutil.unpack_archive(str(tar_path_data), str(extract_dir), "tar")

            dump_path = extract_dir / "postgres" / "full_dump.pgdump"
            if dump_path.exists():
                db_url = os.environ.get("DATABASE_URL", "")
                if db_url:
                    parsed = urlparse(db_url.replace("+asyncpg", ""))
                    env = os.environ.copy()
                    if parsed.password:
                        env["PGPASSWORD"] = parsed.password
                    proc = await asyncio.create_subprocess_exec(
                        "pg_restore", "--clean", "--if-exists",
                        "--host", parsed.hostname or "db",
                        "--port", str(parsed.port or 5432),
                        "--username", parsed.username or "hub",
                        "--dbname", parsed.path.lstrip("/") or "hub",
                        "--no-owner", "--no-acl",
                        str(dump_path),
                        env=env,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await proc.communicate()
                    if proc.returncode != 0:
                        logger.error(f"pg_restore echec: {stderr.decode()[:500]}")

            files_dir = extract_dir / "files"
            if (files_dir / "secrets.tar").exists():
                secrets_target = Path("/code/secrets")
                shutil.unpack_archive(str(files_dir / "secrets.tar"), str(secrets_target))
            if (files_dir / "storage_backups.tar").exists():
                storage_target = Path("/code/storage")
                shutil.unpack_archive(str(files_dir / "storage_backups.tar"), str(storage_target))

            logger.info(f"Snapshot {snapshot_id} restaure avec succes")

        except Exception as e:
            logger.error(f"Restauration snapshot echouee: {e}", exc_info=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    background_tasks.add_task(_do_restore)

    return HubSnapshotRestoreResponse(
        snapshot_id=snapshot_id,
        status="restoring",
        message="Restauration du snapshot en cours...",
    )


@router.get("/{snapshot_id}/download")
async def download_snapshot(
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    snap = await _get_snapshot_or_404(db, snapshot_id)
    if snap.status != "success":
        raise HTTPException(status_code=400, detail="Snapshot non disponible au telechargement")

    if snap.local_path and Path(snap.local_path).exists():
        local_path = Path(snap.local_path)

        def _iter_local():
            with open(local_path, "rb") as f:
                while chunk := f.read(64 * 1024):
                    yield chunk

        return StreamingResponse(
            _iter_local(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="snapshot_{snapshot_id}.tar.zst.enc"',
            },
        )

    config = await get_snapshot_config(db)
    if not snap.s3_key or not config.get("s3_endpoint"):
        raise HTTPException(status_code=400, detail="Snapshot non disponible (pas de stockage S3)")

    tmp = Path(tempfile.mkdtemp(prefix="hub_dl_"))
    dest = tmp / f"snapshot_{snapshot_id}.tar.zst.enc"
    await s3_download(
        snap.s3_bucket or "", snap.s3_key, dest,
        config["s3_endpoint"], config["s3_access_key"], config["s3_secret_key"],
        config.get("s3_region", "eu-west-1"),
    )

    def _iter_and_cleanup():
        try:
            with open(dest, "rb") as f:
                while chunk := f.read(64 * 1024):
                    yield chunk
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return StreamingResponse(
        _iter_and_cleanup(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="snapshot_{snapshot_id}.tar.zst.enc"',
        },
    )


async def _snapshot_cron_scheduler():
    """Tache periodique pour declencher les snapshots planifies."""
    from croniter import croniter
    from datetime import datetime, timezone
    from app.database import async_session_maker
    from app.snapshot_config import get_snapshot_config as get_cfg
    from app.snapshot_engine import create_snapshot as do_snapshot

    logger.info("Snapshot scheduler demarre")
    await asyncio.sleep(60)

    while True:
        try:
            async with async_session_maker() as db:
                config = await get_cfg(db)
                cron_expr = config.get("snapshot_cron", "")
                if cron_expr:
                    now = datetime.now(timezone.utc).replace(tzinfo=None)
                    cron = croniter(cron_expr, now)
                    next_run = cron.get_next(datetime)
                    if next_run.replace(tzinfo=None) <= now:
                        logger.info("Declenchement snapshot planifie")
                        try:
                            await do_snapshot(db, created_by="scheduler")
                        except Exception as e:
                            logger.error(f"Snapshot planifie echoue: {e}")
        except asyncio.CancelledError:
            logger.info("Snapshot scheduler arrete")
            break
        except Exception as e:
            logger.error(f"Erreur snapshot scheduler: {e}", exc_info=True)
        await asyncio.sleep(60)
