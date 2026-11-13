# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Collecteurs de donnees pour les snapshots Hub.
Chaque collecteur produit un fichier dans le repertoire temporaire et retourne des stats.
"""
import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Application, AppBackup, HubSnapshot

logger = logging.getLogger(__name__)

STORAGE_BASE_DIR = Path("/code/storage/backups")
SECRETS_DIR = Path("/code/secrets")
VERSION_FILE = Path("/code/VERSION")

APPS_SOURCE_DIR = Path("/code/apps_source")

# Artefacts régénérables exclus de la capture (build/CI/outils).
# ".git" est volontairement INCLUS (option B : historique hors-ligne au PRA).
APPS_CODE_TAR_EXCLUDES = [
    "node_modules", "__pycache__", ".opencode", ".worktrees",
    ".pytest_cache", ".mypy_cache", "dist", ".next", "target",
    "htmlcov", "*.pyc", ".coverage",
    # Build/artefacts lourds repérés par le diagnostic disque (08/11) :
    # target Rust 3GB, vendor/node artefacts, venvs Python, caches turbo.
    "build", ".venv", "venv", "coverage", ".turbo",
]

# Un fichier individuel plus gros que cette limite n'entre PAS dans apps_code :
# les données volumineuses (modèles gguf, addok.db...) sont couvertes par
# include_app_backups ou retéléchargeables — pas capturées dans le code.
APPS_CODE_MAX_FILE_BYTES = 50 * 1024 * 1024

# Répertoires nommés dans APPS_CODE_TAR_EXCLUDES (les patterns globbed comme
# "*.pyc" ne ciblent pas des répertoires) — utilisés pour élaguer le scan
# des fichiers trop gros (inutile de traverser des Go de build déjà exclus).
_APP_CODE_EXCLUDED_DIRS = {p for p in APPS_CODE_TAR_EXCLUDES if "*" not in p}


def _collect_oversized_files(apps_root: Path, max_bytes: int) -> list[str]:
    """Liste les chemins relatifs (POSIX) des fichiers individuels > max_bytes.

    Parcours élagué : les répertoires d'artefacts (_APP_CODE_EXCLUDED_DIRS) sont
    sautés — déjà exclus du tar par pattern, inutile de les traverser. Les
    fichiers disparus pendant le scan sont ignorés (le tar vivant gère déjà ce
    cas via le exit 1 toléré).
    """
    oversized: list[str] = []
    for root, dirs, files in os.walk(apps_root):
        dirs[:] = [d for d in dirs if d not in _APP_CODE_EXCLUDED_DIRS]
        for name in files:
            try:
                if (Path(root) / name).stat().st_size > max_bytes:
                    oversized.append((Path(root) / name).relative_to(apps_root).as_posix())
            except OSError:
                continue
    return oversized


async def run_pg_dump(temp_dir: Path, db_url: str) -> dict:
    start = time.monotonic()
    output_path = temp_dir / "postgres" / "full_dump.pgdump"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    parsed = urlparse(db_url.replace("+asyncpg", ""))
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password

    cmd = [
        "pg_dump",
        "--host", parsed.hostname or "db",
        "--port", str(parsed.port or 5432),
        "--username", parsed.username or "hub",
        "--dbname", parsed.path.lstrip("/") or "hub",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        # La propre ligne du snapshot (status 'running' au moment du dump)
        # ne doit pas voyager dans l'archive : au restore, le kill-switch la
        # requalifierait 'failed'. La table est recreee par migrations/init_db.
        "--exclude-table=hub_snapshots",
        "--file", str(output_path),
    ]

    logger.info(f"pg_dump: {' '.join(cmd)}")
    proc = await asyncio.create_subprocess_exec(
        *cmd, env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode()[:500] if stderr else f"pg_dump exit code {proc.returncode}"
        raise RuntimeError(f"pg_dump a echoue: {error_msg}")

    size = output_path.stat().st_size
    checksum = hashlib.sha256(output_path.read_bytes()).hexdigest()
    duration_ms = int((time.monotonic() - start) * 1000)

    logger.info(f"pg_dump termine: {size} bytes en {duration_ms}ms")
    return {"size_bytes": size, "duration_ms": duration_ms, "checksum": checksum}


async def run_files_tar(temp_dir: Path) -> dict:
    start = time.monotonic()
    files_dir = temp_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    total_size = 0
    file_count = 0

    storage_tar_path = files_dir / "storage_backups.tar"
    if STORAGE_BASE_DIR.exists():
        proc = await asyncio.create_subprocess_exec(
            "tar", "-cf", str(storage_tar_path), "-C", str(STORAGE_BASE_DIR.parent),
            str(STORAGE_BASE_DIR.name),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode == 0 and storage_tar_path.exists():
            total_size += storage_tar_path.stat().st_size
            file_count += 1

    secrets_tar_path = files_dir / "secrets.tar"
    if SECRETS_DIR.exists():
        proc = await asyncio.create_subprocess_exec(
            "tar", "-cf", str(secrets_tar_path), "-C", str(SECRETS_DIR.parent),
            str(SECRETS_DIR.name),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode == 0 and secrets_tar_path.exists():
            total_size += secrets_tar_path.stat().st_size
            file_count += 1

    duration_ms = int((time.monotonic() - start) * 1000)
    return {"size_bytes": total_size, "duration_ms": duration_ms, "files_count": file_count}


async def run_apps_code_tar(temp_dir: Path) -> dict:
    """Capture le code + config + .git de tout APPS/ vers apps/apps_code.tar.

    Exclusions : artefacts régénérables (APPS_CODE_TAR_EXCLUDES, à toute
    profondeur) et tout fichier individuel > APPS_CODE_MAX_FILE_BYTES — les
    données volumineuses utilisateur sont couvertes par include_app_backups,
    les gros binaires (modèles gguf, addok.db...) sont retéléchargeables.

    Dégradation gracieuse : si le montage /code/apps_source est absent
    (déploiement sans bind mount), log un warning et retourne des stats vides
    sans faire échouer le snapshot.
    """
    start = time.monotonic()
    apps_dir = temp_dir / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    out_path = apps_dir / "apps_code.tar"

    if not APPS_SOURCE_DIR.is_dir():
        logger.warning(
            f"apps_code: {APPS_SOURCE_DIR} indisponible (montage absent?) — collector ignore"
        )
        return {
            "size_bytes": 0,
            "apps_count": 0,
            "oversized_excluded": 0,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }

    cmd = ["tar", "-cf", str(out_path)]
    for pattern in APPS_CODE_TAR_EXCLUDES:
        cmd += ["--exclude", pattern]
    # Fichiers individuels trop gros : exclus explicitement par chemin (tar
    # ne filtre pas par taille). Scan en to_thread — jamais dans la boucle
    # (l'arbre APPS/ peut compter des centaines de milliers d'entrées).
    oversized = await asyncio.to_thread(
        _collect_oversized_files, APPS_SOURCE_DIR, APPS_CODE_MAX_FILE_BYTES
    )
    for rel_path in oversized:
        cmd += ["--exclude", f"./{rel_path}"]
    if oversized:
        max_mb = APPS_CODE_MAX_FILE_BYTES // (1024 * 1024)
        preview = ", ".join(oversized[:5]) + ("..." if len(oversized) > 5 else "")
        logger.info(f"apps_code: {len(oversized)} fichier(s) > {max_mb}MB exclus: {preview}")
    cmd += ["-C", str(APPS_SOURCE_DIR), "."]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode == 1 and out_path.exists() and out_path.stat().st_size > 0:
        logger.warning(
            "apps_code: fichiers modifies pendant la lecture (tar exit 1 tolere) "
            "- archive potentiellement incomplete, le snapshot continue"
        )
    elif proc.returncode != 0 or not out_path.exists():
        error_msg = stderr.decode()[:500] if stderr else f"tar exit code {proc.returncode}"
        raise RuntimeError(f"tar apps_code a echoue: {error_msg}")

    apps_count = sum(1 for d in APPS_SOURCE_DIR.iterdir() if d.is_dir())
    size = out_path.stat().st_size
    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(f"apps_code_tar termine: {size} bytes, {apps_count} apps en {duration_ms}ms")
    return {
        "size_bytes": size,
        "apps_count": apps_count,
        "oversized_excluded": len(oversized),
        "duration_ms": duration_ms,
    }


async def run_app_backups(db: AsyncSession, temp_dir: Path, snapshot: HubSnapshot) -> dict:
    from app.routes.app_backups import perform_app_backup, BackupExecutionError
    from app.database import async_session_maker

    start = time.monotonic()
    apps_dir = temp_dir / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)

    result = await db.execute(
        select(Application)
        .where(Application.is_active == True)
        .where(Application.backup_mode != "disabled")
        .where(Application.is_unmanaged == False)
        # Les ateliers ({slug}-ws) ne sont jamais sauvegardés.
        .where(~Application.slug.endswith("-ws"))
        .options(selectinload(Application.backups))
    )
    apps = result.scalars().all()

    succeeded = 0
    failed = 0
    app_entries = []

    async def _backup_one(app: Application) -> dict:
        nonlocal succeeded, failed
        try:
            async with async_session_maker() as bg_db:
                # Re-attach app to the bg session
                bg_app = await bg_db.get(Application, app.id)
                if not bg_app:
                    failed += 1
                    return {"slug": app.slug, "backup_id": None, "version": "", "size_bytes": 0, "status": "failed"}

                await asyncio.wait_for(
                    perform_app_backup(
                        bg_db, bg_app,
                        created_by="snapshot",
                        created_by_type="snapshot",
                        description_prefix=f"Snapshot {snapshot.snapshot_id}",
                    ),
                    timeout=120,
                )

                if bg_app.last_backup_id:
                    backup_result = await bg_db.execute(
                        select(AppBackup).where(AppBackup.backup_id == bg_app.last_backup_id)
                    )
                    backup = backup_result.scalar_one_or_none()
                    if backup and backup.storage_path:
                        src = STORAGE_BASE_DIR / backup.storage_path
                        if src.exists():
                            dst = apps_dir / app.slug
                            dst.mkdir(parents=True, exist_ok=True)
                            dst_file = dst / f"backup_{backup.backup_id}.zip"
                            dst_file.write_bytes(src.read_bytes())
                            succeeded += 1
                            return {
                                "slug": app.slug,
                                "backup_id": backup.backup_id,
                                "version": app.name or "",
                                "size_bytes": backup.total_size_bytes or 0,
                                "status": "success",
                            }
            failed += 1
            return {"slug": app.slug, "backup_id": None, "version": "", "size_bytes": 0, "status": "failed"}
        except (BackupExecutionError, asyncio.TimeoutError, Exception) as e:
            failed += 1
            logger.error(f"Backup echoue pour {app.slug}: {e}")
            return {"slug": app.slug, "backup_id": None, "version": "", "size_bytes": 0, "status": "failed"}

    tasks = [_backup_one(app) for app in apps]
    app_entries = await asyncio.gather(*tasks)

    duration_ms = int((time.monotonic() - start) * 1000)
    return {
        "size_bytes": 0,
        "duration_ms": duration_ms,
        "total": len(apps),
        "succeeded": succeeded,
        "failed": failed,
        "app_entries": app_entries,
    }


async def run_docker_config(temp_dir: Path) -> dict:
    start = time.monotonic()
    docker_dir = temp_dir / "docker"
    docker_dir.mkdir(parents=True, exist_ok=True)

    project_root = SECRETS_DIR.parent
    compose_file = project_root / "docker-compose.yml"
    if compose_file.exists():
        (docker_dir / "docker-compose.yml").write_bytes(compose_file.read_bytes())

    for dev_name in ("docker-compose.dev.yml", "docker-compose.override.yml"):
        dev_file = project_root / dev_name
        if dev_file.exists():
            (docker_dir / dev_name).write_bytes(dev_file.read_bytes())

    if VERSION_FILE.exists():
        (docker_dir / "VERSION").write_bytes(VERSION_FILE.read_bytes())

    env_lines = [
        "# Template d'environnement pour restauration bare-metal",
        "# Completer les valeurs avant de lancer docker compose",
        f"# Genere le {datetime.now(timezone.utc).isoformat()}",
        "",
        "# PostgreSQL",
        "POSTGRES_USER=hub",
        "POSTGRES_PASSWORD=<a-definir>",
        "POSTGRES_DB=hub",
        "",
        "# Hub API",
        "DATABASE_URL=postgresql+asyncpg://hub:<a-definir>@db:5432/hub",
        "SECRET_KEY=<a-definir>",
        "HUB_URL=http://localhost:8000",
        "",
        "# S3 / Snapshots",
        "SNAPSHOT_S3_ENDPOINT=",
        "SNAPSHOT_S3_BUCKET=hub-snapshots",
        "SNAPSHOT_S3_REGION=eu-west-1",
        "SNAPSHOT_S3_ACCESS_KEY=",
        "SNAPSHOT_S3_SECRET_KEY=",
        "",
    ]
    (docker_dir / "env.template").write_text("\n".join(env_lines))

    total_size = sum(f.stat().st_size for f in docker_dir.iterdir() if f.is_file())
    duration_ms = int((time.monotonic() - start) * 1000)
    return {"size_bytes": total_size, "duration_ms": duration_ms}
