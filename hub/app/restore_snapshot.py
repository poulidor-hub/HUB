"""
Restore snapshot autonome — appele au demarrage si restart requis.
Ce script tourne SANS uvicorn, avant le lancement de l'API.
"""
import asyncio
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

import zstandard as zstd
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RESTORE] %(message)s")
logger = logging.getLogger("snapshot_restore")

PENDING_FILE = Path("/code/storage/pending_restore.txt")
SECRETS_DIR = Path("/code/secrets")
SNAPSHOTS_DIR = Path("/code/storage/snapshots")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


async def run_restore(snapshot_id: str):
    engine = create_async_engine(DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        from app.models import HubSnapshot

        result = await db.execute(
            select(HubSnapshot).where(HubSnapshot.snapshot_id == snapshot_id)
        )
        snap = result.scalar_one_or_none()
        if not snap:
            logger.error(f"Snapshot {snapshot_id} introuvable")
            return False

        if snap.status != "success":
            logger.error(f"Snapshot {snapshot_id} status={snap.status}, pas 'success'")
            return False

        logger.info(f"Debut restauration snapshot {snapshot_id} (taille={snap.total_size_bytes})")

        local_path = snap.local_path
        s3_key = snap.s3_key
        s3_bucket = snap.s3_bucket
        total_size = snap.total_size_bytes

    await engine.dispose()

    tmp = Path(tempfile.mkdtemp(prefix="hub_restore_"))
    try:
        enc_path = tmp / "snapshot.tar.zst.enc"

        if local_path and Path(local_path).exists():
            shutil.copy2(local_path, enc_path)
            logger.info(f"Utilisation stockage local: {local_path}")
        elif s3_key:
            logger.info("Telechargement S3...")
            engine2 = create_async_engine(DATABASE_URL, echo=False)
            sf2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
            async with sf2() as db2:
                from app.snapshot_config import get_snapshot_config
                config = await get_snapshot_config(db2)
                from app.snapshot_s3 import download_from_s3
                await download_from_s3(
                    s3_bucket or "", s3_key, enc_path,
                    config["s3_endpoint"], config["s3_access_key"], config["s3_secret_key"],
                    config.get("s3_region", "eu-west-1"),
                )
            await engine2.dispose()
        else:
            logger.error("Snapshot non disponible (ni local, ni S3)")
            return False

        data = enc_path.read_bytes()
        key_len = int.from_bytes(data[:4], "big")
        enc_key = data[4:4 + key_len]
        nonce = data[4 + key_len:4 + key_len + 12]
        ciphertext = data[4 + key_len + 12:]

        with open(SECRETS_DIR / "jwt_private_key.pem", "rb") as f:
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
            logger.info("Restauration PostgreSQL (pg_restore --clean)...")
            if not DATABASE_URL:
                logger.error("DATABASE_URL non defini")
                return False

            parsed = urlparse(DATABASE_URL.replace("+asyncpg", ""))
            env = os.environ.copy()
            if parsed.password:
                env["PGPASSWORD"] = parsed.password

            proc = await asyncio.create_subprocess_exec(
                "pg_restore", "--clean", "--if-exists",
                "--host", parsed.hostname or "db",
                "--port", str(parsed.port or 5432),
                "--username", parsed.username or "hubadmin",
                "--dbname", parsed.path.lstrip("/") or "hub_auth",
                "--no-owner", "--no-acl",
                str(dump_path),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode > 1:
                logger.error(f"pg_restore echec (code={proc.returncode}): {stderr.decode()[:500]}")
                return False
            if proc.returncode == 1:
                logger.warning(f"pg_restore warnings: {stderr.decode()[:500]}")
            logger.info("pg_restore OK")

        files_dir = extract_dir / "files"
        if (files_dir / "secrets.tar").exists():
            logger.info("Extraction secrets.tar...")
            shutil.unpack_archive(str(files_dir / "secrets.tar"), str(SECRETS_DIR.parent))
        if (files_dir / "storage_backups.tar").exists():
            logger.info("Extraction storage_backups.tar...")
            storage_target = Path("/code/storage")
            shutil.unpack_archive(str(files_dir / "storage_backups.tar"), str(storage_target))

        admin_user = os.environ.get("ADMIN_USERNAME", "admin")
        admin_pass = os.environ.get("ADMIN_PASSWORD", "")
        if admin_pass:
            logger.info(f"Reinitialisation du mot de passe admin ({admin_user})...")
            engine3 = create_async_engine(DATABASE_URL, echo=False)
            sf3 = async_sessionmaker(engine3, class_=AsyncSession, expire_on_commit=False)
            async with sf3() as db3:
                from app.auth import get_password_hash
                from app.models import User
                from sqlalchemy import update as sql_update
                hashed = get_password_hash(admin_pass)
                await db3.execute(
                    sql_update(User)
                    .where(User.username == admin_user)
                    .values(hashed_password=hashed, is_admin=True, is_active=True)
                )
                await db3.commit()
            await engine3.dispose()
            logger.info("Mot de passe admin reinitialise")

        logger.info(f"Restauration snapshot {snapshot_id} terminee avec succes")
        return True

    except Exception as e:
        logger.error(f"Restauration echouee: {e}", exc_info=True)
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if not PENDING_FILE.exists():
        logger.info("Aucune restauration en attente.")
        return

    snapshot_id = PENDING_FILE.read_text().strip()
    PENDING_FILE.unlink(missing_ok=True)

    if not snapshot_id:
        logger.warning("Fichier pending_restore.txt vide, ignore.")
        return

    logger.info(f"Restauration demandee: {snapshot_id}")
    success = asyncio.run(run_restore(snapshot_id))
    if success:
        logger.info("Restauration OK, demarrage de l'API.")
    else:
        logger.error("Restauration ECHEC — l'API demarre mais les donnees peuvent etre incoherentes.")


if __name__ == "__main__":
    main()
