# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Moteur de snapshot: orchestre les collecteurs, assemble l'archive,
chiffre et upload vers S3.
"""
import asyncio
import hashlib
import json
import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import zstandard as zstd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HubSnapshot, utcnow
from app.snapshot_collectors import (
    run_pg_dump, run_files_tar, run_app_backups, run_docker_config,
    run_apps_code_tar,
)
from app.snapshot_config import get_snapshot_config
from app.snapshot_s3 import upload_to_s3
from app.snapshot_crypto import encrypt_file
from app.config import get_settings

logger = logging.getLogger(__name__)

SECRETS_DIR = Path("/code/secrets")
SNAPSHOT_STORAGE_DIR = Path("/code/storage/snapshots")  # monkeypatchable en test
SETTINGS = get_settings()

# Chunks du pipeline d'assemblage : payload multi-Go jamais entier en RAM
# (incident OOM 29/10/2026 : cctx.compress(src.read()) + read_bytes() ont
# OOM-killé le container hub-api, memcg 2 Go, sur un snapshot de 4,4 Go).
_ARCHIVE_CHUNK = 4 * 1024 * 1024


class SnapshotCreationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _assemble_and_compress(temp_dir: Path, archive_path: Path) -> int:
    """Assemble le contenu de temp_dir en tar puis compresse en zstd, en streaming.

    - Le tar est écrit fichier par fichier (``tarfile.add`` lit depuis le disque).
    - La compression lit le tar par chunks (``copy_stream``) : RAM constante.
    - La taille source est « pledged » à zstd pour que le frame embarque le
      content-size — requis par la décompression one-shot de restore_snapshot.py
      (``dctx.decompress(data)`` sans max_output_size).

    Retourne la taille de l'archive .tar.zst produite.
    """
    files_to_add = []
    for root, dirs, filenames in os.walk(temp_dir):
        for fn in filenames:
            full = Path(root) / fn
            if full.suffix != ".enc":
                files_to_add.append(full)

    tmp_tar = tempfile.NamedTemporaryFile(suffix=".tar", delete=False)
    tmp_tar.close()
    tar_path = Path(tmp_tar.name)
    try:
        with tarfile.open(tar_path, "w") as tar:
            for f in files_to_add:
                tar.add(str(f), arcname=str(f.relative_to(temp_dir)))
        cctx = zstd.ZstdCompressor(level=3)
        with open(tar_path, "rb") as src, open(archive_path, "wb") as dst:
            cctx.copy_stream(src, dst, size=tar_path.stat().st_size)
    finally:
        tar_path.unlink(missing_ok=True)
    return archive_path.stat().st_size


def _sha256_file(path: Path) -> str:
    """SHA-256 d'un fichier par chunks de 4 Mo (jamais read_bytes())."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_ARCHIVE_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


async def create_snapshot(
    db: AsyncSession,
    *,
    created_by: str = "admin",
    snapshot_id: Optional[str] = None,
) -> str:
    if snapshot_id:
        result = await db.execute(
            select(HubSnapshot).where(HubSnapshot.snapshot_id == snapshot_id)
        )
        snapshot = result.scalar_one()
        snapshot.status = "running"
        snapshot.progress_step = "queued"
        snapshot.created_at = utcnow()
        await db.commit()
        await db.refresh(snapshot)
    else:
        snapshot_id = str(uuid.uuid4())
        snapshot = HubSnapshot(
            snapshot_id=snapshot_id,
            status="running",
            progress_step="queued",
            created_by=created_by,
            created_at=utcnow(),
        )
        db.add(snapshot)
        await db.commit()
        await db.refresh(snapshot)

    temp_dir = Path(tempfile.mkdtemp(prefix=f"hub_snapshot_{snapshot_id}_"))
    logger.info(f"[SNAPSHOT] {snapshot_id}: demarrage (createur={created_by})")

    try:
        config = await get_snapshot_config(db)

        disk_usage = shutil.disk_usage(temp_dir)
        if disk_usage.free < 2 * 1024 * 1024 * 1024:
            raise SnapshotCreationError(
                f"Espace disque insuffisant: {disk_usage.free // 1024 // 1024} Mo disponibles"
            )

        pg_stats = {}
        files_stats = {}
        apps_stats = {}
        docker_stats = {}
        apps_code_stats = {}

        if snapshot.include_pg_dump:
            _update_progress(snapshot, "pg_dump", 10)
            await db.commit()
            pg_stats = await run_pg_dump(temp_dir, SETTINGS.database_url)
            logger.info(f"[SNAPSHOT] {snapshot_id}: pg_dump OK ({pg_stats.get('size_bytes', 0)} bytes)")

        if snapshot.include_files:
            _update_progress(snapshot, "files_tar", 20)
            await db.commit()
            files_stats = await run_files_tar(temp_dir)
            logger.info(f"[SNAPSHOT] {snapshot_id}: files_tar OK ({files_stats.get('files_count', 0)} fichiers, {files_stats.get('size_bytes', 0)} bytes)")

        if snapshot.include_apps_code:
            _update_progress(snapshot, "apps_code", 30)
            await db.commit()
            apps_code_stats = await run_apps_code_tar(temp_dir)
            logger.info(f"[SNAPSHOT] {snapshot_id}: apps_code OK ({apps_code_stats.get('size_bytes', 0)} bytes, {apps_code_stats.get('apps_count', 0)} apps)")

        if snapshot.include_app_backups:
            _update_progress(snapshot, "app_backups", 40)
            await db.commit()
            apps_stats = await run_app_backups(db, temp_dir, snapshot)
            if apps_stats:
                snapshot.apps_count = apps_stats.get("total", 0)
                snapshot.apps_succeeded = apps_stats.get("succeeded", 0)
                snapshot.apps_failed = apps_stats.get("failed", 0)
                logger.info(f"[SNAPSHOT] {snapshot_id}: app_backups OK ({apps_stats.get('succeeded', 0)}/{apps_stats.get('total', 0)})")

        if snapshot.include_docker_config:
            _update_progress(snapshot, "docker_config", 70)
            await db.commit()
            docker_stats = await run_docker_config(temp_dir)
            logger.info(f"[SNAPSHOT] {snapshot_id}: docker_config OK ({docker_stats.get('size_bytes', 0)} bytes)")

        _update_progress(snapshot, "assembling", 80)
        await db.commit()

        # Manifest
        manifest_path = temp_dir / "manifest.json"
        manifest = {
            "version": "1.0",
            "snapshot_id": snapshot_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hub_version": _read_version(),
            "collectors": {
                "pg_dump": {"size_bytes": pg_stats.get("size_bytes", 0), "duration_ms": pg_stats.get("duration_ms", 0)},
                "files": {"size_bytes": files_stats.get("size_bytes", 0), "files_count": files_stats.get("files_count", 0)},
                "apps_code": {
                    "size_bytes": apps_code_stats.get("size_bytes", 0),
                    "apps_count": apps_code_stats.get("apps_count", 0),
                },
                "docker_config": {"size_bytes": docker_stats.get("size_bytes", 0)},
                "app_backups": {
                    "total": apps_stats.get("total", 0),
                    "succeeded": apps_stats.get("succeeded", 0),
                    "failed": apps_stats.get("failed", 0),
                },
            },
            "apps": apps_stats.get("app_entries", []),
            "checksums": {},
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

        # Assemble tar + compression zstd en streaming, hors event loop
        # (to_thread) : Hub réactif pendant l'assemblage, RAM constante.
        archive_path = temp_dir / f"snapshot_{snapshot_id}.tar.zst"
        archive_size = await asyncio.to_thread(_assemble_and_compress, temp_dir, archive_path)
        logger.info(f"[SNAPSHOT] {snapshot_id}: archive .tar.zst = {archive_size} bytes")

        # Encrypt
        _update_progress(snapshot, "encrypt", 90)
        await db.commit()

        encrypted_path = temp_dir / f"snapshot_{snapshot_id}.tar.zst.enc"
        await asyncio.to_thread(_encrypt_file, archive_path, encrypted_path)
        archive_path.unlink()

        checksum = await asyncio.to_thread(_sha256_file, encrypted_path)

        # Sauvegarde locale
        storage_dir = SNAPSHOT_STORAGE_DIR
        storage_dir.mkdir(parents=True, exist_ok=True)
        local_dest = storage_dir / f"snapshot_{snapshot_id}.tar.zst.enc"
        shutil.copy2(encrypted_path, local_dest)
        snapshot.local_path = str(local_dest)
        logger.info(f"[SNAPSHOT] {snapshot_id}: sauvegarde locale -> {local_dest}")

        # Upload S3
        s3_endpoint = config.get("s3_endpoint", "")
        if s3_endpoint and config.get("s3_access_key"):
            _update_progress(snapshot, "upload", 95)
            await db.commit()

            prefix = config.get("s3_prefix", "prod/").rstrip("/")
            s3_key = f"{prefix}/snapshot_{snapshot_id}.tar.zst.enc" if prefix else f"snapshot_{snapshot_id}.tar.zst.enc"

            await upload_to_s3(
                encrypted_path, config["s3_bucket"], s3_key,
                s3_endpoint, config["s3_access_key"], config["s3_secret_key"],
                config.get("s3_region", "eu-west-1"),
            )
            snapshot.s3_bucket = config["s3_bucket"]
            snapshot.s3_key = s3_key

        snapshot.status = "success"
        snapshot.progress_percent = 100
        snapshot.progress_step = "done"
        snapshot.total_size_bytes = archive_size
        snapshot.checksum_sha256 = checksum
        snapshot.manifest = manifest
        snapshot.completed_at = utcnow()
        snapshot.retention_policy = {
            "max_count": config.get("snapshot_retention_max", 7),
            "min_age_hours": config.get("snapshot_retention_min_age_hours", 24),
        }
        await db.commit()

        from app.snapshot_retention import apply_retention
        await apply_retention(
            db,
            config.get("snapshot_retention_max", 7),
            config.get("snapshot_retention_min_age_hours", 24),
        )

        logger.info(f"Snapshot {snapshot_id} cree avec succes ({archive_size} bytes)")
        return snapshot_id

    except Exception as e:
        logger.error(f"Echec snapshot {snapshot_id}: {e}", exc_info=True)
        snapshot.status = "failed"
        snapshot.error_message = str(e)[:1000]
        snapshot.completed_at = utcnow()
        await db.commit()
        raise SnapshotCreationError(str(e)) from e

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _update_progress(snapshot: HubSnapshot, step: str, percent: int):
    snapshot.progress_step = step
    snapshot.progress_percent = min(percent, 100)


def _read_version() -> str:
    version_file = Path("/code/VERSION")
    if version_file.exists():
        return version_file.read_text().strip()
    return "unknown"


def _encrypt_file(input_path: Path, output_path: Path):
    """Chiffre un fichier de snapshot — délégué à snapshot_crypto (clé backup)."""
    encrypt_file(input_path, output_path, secrets_dir=SECRETS_DIR)


async def cleanup_stale_snapshots(db: AsyncSession):
    """Kill-switch au demarrage: marque les snapshots 'running' comme 'failed'."""
    from sqlalchemy import update
    await db.execute(
        update(HubSnapshot)
        .where(HubSnapshot.status == "running")
        .values(status="failed", error_message="Snapshot interrompu (redemarrage du Hub)")
    )
    await db.commit()
