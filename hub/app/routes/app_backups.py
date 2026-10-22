"""
Routes pour la gestion centralisée des sauvegardes d'applications.

Ce module permet au HUB de :
- Recevoir les backups poussés par les applications (sync-to-hub)
- Stocker les fichiers ZIP avec métadonnées
- Chiffrer et exporter les backups individuellement
- Planifier des backups automatiques via cron
- Gérer la rétention des backups par application

Endpoints:
- POST /api/apps/{slug}/backup          : Recevoir un backup depuis une app
- GET  /api/apps/{slug}/backup          : Lister les backups d'une app
- GET  /api/apps/{slug}/backup/latest   : Récupérer le dernier backup
- GET  /api/apps/{slug}/backup/{id}     : Télécharger un backup spécifique
- DELETE /api/apps/{slug}/backup/{id}   : Supprimer un backup
- POST /api/apps/{slug}/backup/{id}/export : Exporter un backup chiffré par mot de passe
- POST /api/apps/{slug}/backup/import : Importer un backup externe (.encrypted/.zip)
- PUT  /api/apps/{slug}/backup/config   : Configurer le scheduling
- POST /api/apps/{slug}/backup/trigger  : Déclencher un backup manuellement
- GET  /api/admin/backups               : Vue globale admin
- GET  /api/admin/backups/stats         : Statistiques de stockage
"""
import os
import io
import uuid
import json
import hashlib
import secrets
import zipfile
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.database import get_db
from app.models import Application, AppBackup, ServiceAccount, User, ApprovalStatus, utcnow
from app.auth import get_admin_or_admin_sa, get_current_admin, get_current_entity, get_password_hash
from app.config import get_settings
from app.routes.audit import log_action
from app.schemas import (
    AppBackupConfig, AppBackupConfigUpdate, AppBackupReceive,
    AppBackupResponse, AppBackupListResponse, AppBackupStatusResponse,
    AllBackupsResponse, BackupTriggerResponse, BackupExportResponse, BackupImportResponse,
    BackupModeEnum, RestoreToAppRequest, RestoreToAppResponse,
    InProgressBackupItem, InProgressBackupsResponse,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# Configuration du stockage
STORAGE_BASE_DIR = Path("/code/storage/backups")
SECRETS_DIR = Path("/code/secrets")
JWT_PRIVATE_KEY_PATH = SECRETS_DIR / "jwt_private_key.pem"
BACKUP_PRIVATE_KEY_PATH = SECRETS_DIR / "backup_private_key.pem"
BACKUP_PUBLIC_KEY_PATH = SECRETS_DIR / "backup_public_key.pem"

# Chiffrement par mot de passe (format .encrypted v2)
PBKDF2_ITERATIONS = 600_000
PASSWORD_MIN_LENGTH = 8
PASSWORD_DECRYPT_ERROR = "Mot de passe incorrect ou fichier corrompu"

router = APIRouter(tags=["App Backups"])


def get_backup_file_path_or_404(backup: AppBackup, *, encrypted: bool = False) -> Path:
    path_value = backup.encrypted_path if encrypted else backup.storage_path
    if not path_value:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fichier de backup non trouvé sur le serveur",
        )
    return STORAGE_BASE_DIR / path_value


def delete_backup_file_if_present(path_value: Optional[str]) -> int:
    if not path_value:
        return 0
    file_path = STORAGE_BASE_DIR / path_value
    if not file_path.exists():
        return 0
    file_size = file_path.stat().st_size
    file_path.unlink()
    return file_size


_VALID_BACKUP_MODES = {"disabled", "manual", "scheduled"}


def _safe_backup_mode(raw: Optional[str]) -> BackupModeEnum:
    """Normalise la valeur de backup_mode : fallback "manual" si la valeur est invalide ou absente."""
    return BackupModeEnum(raw if raw in _VALID_BACKUP_MODES else "manual")


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise un datetime en naïf UTC.

    Les colonnes `started_at` / `completed_at` ajoutées par la migration 024
    sont `TIMESTAMP WITH TIME ZONE` : SQLAlchemy les retourne donc *aware*,
    alors que `utcnow()` (models) renvoie du *naive*. Pour éviter
    `TypeError: can't subtract offset-naive and offset-aware datetimes`,
    on ramène toujours les valeurs en naïf UTC avant calcul.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


async def reassociate_orphan_backups(db: AsyncSession, app: Application) -> int:
    """
    Réassocie les backups orphelins à une application.
    
    Quand une application est créée avec un slug qui a des backups existants
    (par exemple après une suppression/recréation), cette fonction réassocie
    ces backups à la nouvelle application.
    
    Args:
        db: Session de base de données
        app: Application nouvellement créée/modifiée
        
    Returns:
        Nombre de backups réassociés
    """
    # Trouver les backups avec ce slug mais sans app_id (orphelins)
    result = await db.execute(
        select(AppBackup).where(
            AppBackup.app_slug == app.slug,
            AppBackup.app_id.is_(None)
        )
    )
    orphan_backups = result.scalars().all()
    
    count = 0
    for backup in orphan_backups:
        backup.app_id = app.id
        count += 1
    
    if count > 0:
        await db.commit()
        logger.info(f"🔗 {count} backup(s) orphelin(s) réassocié(s) à l'application '{app.slug}'")
    
    return count


def ensure_storage_dir(app_slug: str) -> Path:
    """Crée et retourne le dossier de stockage pour une application"""
    app_dir = STORAGE_BASE_DIR / app_slug
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def compute_checksum(data: bytes) -> str:
    """Calcule le checksum SHA-256 des données"""
    return hashlib.sha256(data).hexdigest()


def safe_int(value, default: int = 0) -> int:
    """Convertit une valeur metadata en int sans faire échouer le backup."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def metadata_value(payload: dict, key: str, default=None):
    """Lit une valeur top-level ou dans payload.metadata."""
    if not isinstance(payload, dict):
        return default
    value = payload.get(key)
    if value is not None:
        return value
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get(key)
        if value is not None:
            return value
    return default


def bool_to_form_value(value: bool) -> str:
    """Formate un booléen pour relayage multipart."""
    return str(bool(value)).lower()


def parse_bool(value, default: bool = False) -> bool:
    """Parse les booléens envoyés comme strings par des clients multipart/query."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def human_readable_size(size: int) -> str:
    """Convertit une taille en bytes en format lisible"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def extract_metadata_from_zip(zip_content: bytes) -> dict:
    """
    Extrait les métadonnées et compteurs depuis un fichier ZIP de backup.
    
    Le ZIP contient typiquement un fichier data.json avec la structure:
    {
        "metadata": {
            "backup_id": "...",
            "app_version": "...",
            ...
        },
        "data": {
            "items": [...],
            "user_preferences": [...],
            "uploaded_files": [...],
            ...
        }
    }
    
    Returns:
        dict avec app_version, total_items, total_preferences, total_files, content_summary
    """
    result = {
        "app_version": None,
        "format_version": None,
        "total_items": 0,
        "total_preferences": 0,
        "total_files": 0,
        "total_tables": 0,
        "total_rows": 0,
        "includes_vaultwarden_snapshot": False,
        "includes_secret_bundle": False,
        "restore_requires_offline": False,
        "content_summary": ""
    }
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zf:
            file_list = zf.namelist()
            normalized_names = {name.lstrip("./"): name for name in file_list}
            result["includes_vaultwarden_snapshot"] = "vaultwarden/manifest.json" in normalized_names
            result["includes_secret_bundle"] = "secrets/backup-secrets.json" in normalized_names
            result["restore_requires_offline"] = result["includes_vaultwarden_snapshot"]

            # Chercher data.json ou manifest.json
            data_file = None
            for name in ['data.json', 'manifest.json', 'backup.json']:
                if name in normalized_names:
                    data_file = normalized_names[name]
                    break
            
            if data_file:
                # ⚠️ Ne JAMAIS json.load() le fichier entier : les backups
                # génériques (format 3.0) peuvent contenir des data.json de
                # plusieurs centaines de MB (ex. exemple-app : 1M lignes) — le parse
                # complet en dicts Python dépasse la RAM du conteneur et tue la
                # tâche de fond (exception avalée par create_task → backup
                # bloqué "running" à 85% pour toujours).
                # On extrait l'objet "metadata" (petit, en tête du JSON) en
                # streaming, sans jamais charger la section "data".
                metadata: dict | None = None
                try:
                    decoder = json.JSONDecoder()
                    buf = b""
                    with zf.open(data_file) as f:
                        while True:
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            buf += chunk
                            i = buf.find(b'"metadata"')
                            if i == -1:
                                continue
                            j = buf.find(b"{", i)
                            if j == -1:
                                continue
                            try:
                                obj, _end = decoder.raw_decode(buf[j:].decode("utf-8", "ignore"))
                                metadata = obj
                                break
                            except json.JSONDecodeError:
                                # objet metadata pas encore complet → chunk suivant
                                continue
                except Exception as stream_err:
                    logger.warning(f"Extraction metadata streaming échouée: {stream_err}")

                if metadata is not None:
                    result["app_version"] = metadata.get("app_version") or metadata.get("version")
                    result["format_version"] = metadata.get("format_version")
                    result["total_tables"] = safe_int(metadata.get("total_tables"))
                    result["total_rows"] = safe_int(metadata.get("total_rows"))
                    result["includes_vaultwarden_snapshot"] = (
                        result["includes_vaultwarden_snapshot"]
                        or parse_bool(metadata.get("includes_vaultwarden_snapshot"))
                    )
                    result["includes_secret_bundle"] = (
                        result["includes_secret_bundle"]
                        or parse_bool(metadata.get("includes_secret_bundle"))
                    )
                    result["restore_requires_offline"] = (
                        result["restore_requires_offline"]
                        or parse_bool(metadata.get("restore_requires_offline"))
                    )
                    if metadata.get("tables") or metadata.get("table_stats"):
                        summary_parts = []
                        for key, count in (metadata.get("table_stats") or {}).items():
                            if count:
                                summary_parts.append(f"{count} {key.replace('_', ' ').title()}")
                        if summary_parts:
                            result["content_summary"] = ", ".join(summary_parts[:6])
                else:
                    # Fallback formats historiques SANS section "metadata"
                    # (backups anciens, supposés petits) : parse complet.
                    with zf.open(data_file) as f:
                        data = json.load(f)

                    metadata = data.get("metadata", {})
                    result["app_version"] = metadata.get("app_version") or metadata.get("version")
                    result["format_version"] = metadata.get("format_version")
                    result["total_tables"] = safe_int(metadata.get("total_tables"))
                    result["total_rows"] = safe_int(metadata.get("total_rows"))

                    backup_data = data.get("data", {})
                    items = backup_data.get("items", [])
                    result["total_items"] = len(items)
                    prefs = backup_data.get("user_preferences", []) or backup_data.get("preferences", [])
                    result["total_preferences"] = len(prefs)
                    files = backup_data.get("uploaded_files", []) or backup_data.get("files", [])
                    result["total_files"] = len(files)

                    summary_parts = []
                    for key, value in backup_data.items():
                        if isinstance(value, list) and len(value) > 0:
                            display_name = key.replace("_", " ").title()
                            summary_parts.append(f"{len(value)} {display_name}")

                    if summary_parts:
                        result["content_summary"] = ", ".join(summary_parts)
                    else:
                        result["content_summary"] = "Backup vide"
            else:
                # Pas de fichier de données trouvé, lister les fichiers du ZIP
                result["total_files"] = len(file_list)
                result["content_summary"] = f"{len(file_list)} fichier(s) dans l'archive"
                
    except zipfile.BadZipFile:
        logger.warning("Le fichier n'est pas un ZIP valide")
        result["content_summary"] = "Archive invalide"
    except json.JSONDecodeError:
        logger.warning("Erreur de parsing JSON dans le backup")
        result["content_summary"] = "Données JSON invalides"
    except Exception as e:
        logger.warning(f"Erreur extraction métadonnées: {e}")
        result["content_summary"] = "Erreur d'extraction"
    
    return result


def encrypt_backup(data: bytes) -> tuple[bytes, bytes, bytes]:
    """
    Chiffre les données avec un schéma hybride RSA + AES-256-GCM.

    1. Génère une clé AES-256 aléatoire
    2. Chiffre les données avec AES-256-GCM
    3. Chiffre la clé AES avec la clé RSA dédiée aux backups

    Returns:
        tuple: (encrypted_data, encrypted_key, nonce)
    """
    if not BACKUP_PRIVATE_KEY_PATH.exists():
        if JWT_PRIVATE_KEY_PATH.exists():
            logger.warning("Clé backup absente, fallback sur clé JWT (legacy)")
            key_path = JWT_PRIVATE_KEY_PATH
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Clé de chiffrement backup non trouvée"
            )
    else:
        key_path = BACKUP_PRIVATE_KEY_PATH

    private_key_pem = key_path.read_bytes()
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)

    aes_key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)

    aesgcm = AESGCM(aes_key)
    encrypted_data = aesgcm.encrypt(nonce, data, None)

    public_key = private_key.public_key()
    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )

    return encrypted_data, encrypted_key, nonce


def create_encrypted_package(backup_data: bytes, backup_id: str, app_slug: str) -> bytes:
    """
    Crée un package chiffré avec métadonnées pour l'export.
    
    Structure du fichier exporté:
    - 4 bytes: longueur de l'en-tête JSON
    - N bytes: en-tête JSON (métadonnées + encrypted_key + nonce en base64)
    - Reste: données chiffrées AES-GCM
    """
    import base64
    
    encrypted_data, encrypted_key, nonce = encrypt_backup(backup_data)
    
    # Créer l'en-tête avec métadonnées
    header = {
        "version": "2.0",
        "algorithm": "RSA-OAEP-256+AES-256-GCM",
        "key_type": "backup" if BACKUP_PRIVATE_KEY_PATH.exists() else "jwt_legacy",
        "backup_id": backup_id,
        "app_slug": app_slug,
        "encrypted_at": utcnow().isoformat(),
        "encrypted_key_b64": base64.b64encode(encrypted_key).decode(),
        "nonce_b64": base64.b64encode(nonce).decode(),
        "original_checksum": compute_checksum(backup_data)
    }
    
    header_json = json.dumps(header).encode()
    header_length = len(header_json).to_bytes(4, byteorder='big')
    
    # Assembler le package
    return header_length + header_json + encrypted_data


def decrypt_encrypted_package(encrypted_package: bytes, password: Optional[str] = None) -> tuple[bytes, dict]:
    """
    Déchiffre un package .encrypted (v1 RSA ou v2 password).
    
    Args:
        encrypted_package: Contenu du fichier .encrypted
        password: Mot de passe (requis pour les packages v2 mode password)
        
    Returns:
        tuple: (données_déchiffrées, header_dict)
        
    Raises:
        HTTPException: Si le déchiffrement échoue
    """
    import base64
    
    try:
        # 1. Lire la longueur de l'en-tête (4 premiers bytes)
        header_length = int.from_bytes(encrypted_package[:4], byteorder='big')
        
        # 2. Extraire et parser l'en-tête JSON
        header_json = encrypted_package[4:4 + header_length]
        header = json.loads(header_json.decode())
        
        # 3. Extraire les données chiffrées
        encrypted_data = encrypted_package[4 + header_length:]
        nonce = base64.b64decode(header["nonce_b64"])
        
        if header.get("encryption_mode") == "password":
            # Format v2 : clé AES dérivée du mot de passe
            if not password:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Mot de passe requis pour déchiffrer ce fichier",
                )
            try:
                salt = base64.b64decode(header["salt_b64"])
                iterations = int(header.get("kdf_iterations", PBKDF2_ITERATIONS))
                if not (1 <= iterations <= PBKDF2_ITERATIONS):
                    raise ValueError("kdf_iterations hors bornes")
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=PASSWORD_DECRYPT_ERROR,
                )
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=iterations,
            )
            aes_key = kdf.derive(password.encode())
            aesgcm = AESGCM(aes_key)
            try:
                decrypted_data = aesgcm.decrypt(nonce, encrypted_data, None)
            except Exception:
                # Erreur générique : pas de distinction mauvais mdp / corruption
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=PASSWORD_DECRYPT_ERROR,
                )
        else:
            # Format v1 (RSA) : code existant inchangé
            # 4. Charger la clé privée : backup key prioritaire, JWT en fallback legacy
            key_paths = []
            if BACKUP_PRIVATE_KEY_PATH.exists():
                key_paths.append(("backup", BACKUP_PRIVATE_KEY_PATH))
            if JWT_PRIVATE_KEY_PATH.exists():
                key_paths.append(("jwt", JWT_PRIVATE_KEY_PATH))
    
            if not key_paths:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Aucune clé de déchiffrement trouvée"
                )
    
            # 5. Décoder la clé AES chiffrée depuis base64
            encrypted_key = base64.b64decode(header["encrypted_key_b64"])
    
            # 6. Déchiffrer la clé AES avec RSA (essayer chaque clé)
            aes_key = None
            last_error = None
            for key_name, key_path in key_paths:
                try:
                    private_key_pem = key_path.read_bytes()
                    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
                    aes_key = private_key.decrypt(
                        encrypted_key,
                        padding.OAEP(
                            mgf=padding.MGF1(algorithm=hashes.SHA256()),
                            algorithm=hashes.SHA256(),
                            label=None
                        )
                    )
                    if key_name != key_paths[0][0]:
                        logger.info(f"Déchiffrement via clé {key_name} (fallback)")
                    break
                except Exception as e:
                    last_error = e
                    continue
    
            if aes_key is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Échec du déchiffrement avec toutes les clés disponibles: {last_error}"
                )
            
            # 7. Déchiffrer les données avec AES-GCM
            aesgcm = AESGCM(aes_key)
            decrypted_data = aesgcm.decrypt(nonce, encrypted_data, None)
        
        # 8. Vérifier le checksum (v2: "checksum", v1: "original_checksum")
        expected_checksum = header.get("checksum") or header.get("original_checksum")
        if expected_checksum:
            computed_checksum = compute_checksum(decrypted_data)
            if computed_checksum != expected_checksum:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Checksum invalide - le fichier est peut-être corrompu"
                )
        
        logger.info(f"🔓 Package déchiffré: backup_id={header.get('backup_id')}, app_slug={header.get('app_slug')}")
        
        return decrypted_data, header
        
    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format de fichier .encrypted invalide - en-tête JSON corrompu"
        )
    except Exception as e:
        logger.error(f"❌ Erreur de déchiffrement: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erreur lors du déchiffrement: {str(e)}"
        )


def create_password_encrypted_package(backup_data: bytes, backup_id: str, app_slug: str, password: str) -> bytes:
    """
    Crée un package .encrypted v2 chiffré par mot de passe (portable).
    
    Le mot de passe doit être validé au préalable via validate_export_password().
    
    - AES-256-GCM, clé dérivée du mot de passe via PBKDF2-HMAC-SHA256
    - Aucune dépendance aux clés RSA du hub : déchiffrable partout avec le mot de passe
    """
    import base64
    
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    aes_key = kdf.derive(password.encode())
    
    aesgcm = AESGCM(aes_key)
    encrypted_data = aesgcm.encrypt(nonce, backup_data, None)
    
    header = {
        "version": "2.0",
        "encryption_mode": "password",
        "kdf": "PBKDF2-HMAC-SHA256",
        "kdf_iterations": PBKDF2_ITERATIONS,
        "salt_b64": base64.b64encode(salt).decode(),
        "nonce_b64": base64.b64encode(nonce).decode(),
        "backup_id": backup_id,
        "app_slug": app_slug,
        "checksum": compute_checksum(backup_data),
        "original_filename": f"backup_{backup_id}.zip",
        "encrypted_at": utcnow().isoformat(),
    }
    
    header_json = json.dumps(header).encode()
    header_length = len(header_json).to_bytes(4, byteorder='big')
    return header_length + header_json + encrypted_data


def validate_export_password(password: Optional[str], password_confirm: Optional[str]) -> str:
    """Valide le mot de passe d'export. Lève HTTPException si invalide."""
    if not password or len(password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Le mot de passe doit contenir au moins {PASSWORD_MIN_LENGTH} caractères",
        )
    if password != password_confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Les deux mots de passe ne correspondent pas",
        )
    return password


async def get_app_or_404(db: AsyncSession, slug: str) -> Application:
    """Récupère une application par son slug ou lève une 404"""
    if slug == "hub":
        return await ensure_hub_system_application(db)

    result = await db.execute(
        select(Application).where(Application.slug == slug)
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{slug}' non trouvée"
        )
    return app


async def ensure_hub_system_application(db: AsyncSession) -> Application:
    """Garantit l'existence de l'application système utilisée pour sauvegarder le Hub."""
    result = await db.execute(select(Application).where(Application.slug == "hub"))
    app = result.scalar_one_or_none()
    if app is None:
        app = Application(
            name="Hub",
            slug="hub",
            description="Application système représentant la configuration du Hub pour les sauvegardes planifiées.",
            icon="bi-grid-3x3-gap-fill",
            color="#495057",
            category="Système",
            api_url="http://hub-api:8000",
            frontend_url="/",
            git_repo_url="https://github.com/POULIDOR-Audit-Developpement/POULIDOR.git",
            is_active=True,
            requires_auth=True,
            is_shared_service=False,
            is_system_app=True,
            approval_status=ApprovalStatus.approved,
            backup_mode="manual",
            backup_retention=5,
            backup_quiesce=False,
        )
        db.add(app)
        await db.commit()
        await db.refresh(app)

    changed = False
    if not getattr(app, "is_system_app", False):
        app.is_system_app = True
        changed = True
    if not app.category:
        app.category = "Système"
        changed = True
    if not getattr(app, "git_repo_url", None):
        app.git_repo_url = "https://github.com/POULIDOR-Audit-Developpement/POULIDOR.git"
        changed = True
    if not app.backup_mode or app.backup_mode not in _VALID_BACKUP_MODES:
        app.backup_mode = "manual"
        changed = True
    if not app.backup_retention:
        app.backup_retention = 5
        changed = True
    if changed:
        await db.commit()
        await db.refresh(app)

    # Garantir un ServiceAccount associé à l'app hub : le flux de restauration
    # self-backup génère son token M2M via un SA de l'app (le hub signe ses
    # propres tokens, le secret n'a pas besoin d'être partagé). Sans SA, le
    # restore échoue avec « Aucun service account trouvé pour cette application ».
    sa_result = await db.execute(
        select(ServiceAccount)
        .join(ServiceAccount.applications)
        .where(Application.slug == "hub")
        .limit(1)
    )
    if sa_result.scalar_one_or_none() is None:
        sa = ServiceAccount(
            name="SA - Hub",
            description="Service Account HUB (auto-restauration + M2M)",
            client_id=ServiceAccount.generate_client_id(),
            client_secret_hash=get_password_hash(secrets.token_urlsafe(32)),
            is_active=True,
        )
        sa.applications = [app]
        db.add(sa)
        await db.commit()

    return app


async def get_backup_or_404(db: AsyncSession, backup_id: str) -> AppBackup:
    """Récupère un backup par son ID ou lève une 404"""
    result = await db.execute(
        select(AppBackup).where(AppBackup.backup_id == backup_id)
    )
    backup = result.scalar_one_or_none()
    if not backup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Backup '{backup_id}' non trouvé"
        )
    return backup


async def apply_retention_policy(db: AsyncSession, app_slug: str, retention: int):
    """Applique la politique de rétention en supprimant les anciens backups réussis.

    Les backups en cours (pending/running) et en échec sont ignorés : ils ne
    comptent pas dans la rétention et ne sont pas supprimés ici.
    """
    if retention <= 0:
        return  # 0 = illimité

    # Compter les backups réussis existants
    count_result = await db.execute(
        select(func.count(AppBackup.id))
        .where(AppBackup.app_slug == app_slug)
        .where(AppBackup.status == "success")
    )
    count = count_result.scalar() or 0

    if count <= retention:
        return

    # Supprimer les plus anciens (parmi les réussis)
    to_delete = count - retention
    oldest_result = await db.execute(
        select(AppBackup)
        .where(AppBackup.app_slug == app_slug)
        .where(AppBackup.status == "success")
        .order_by(AppBackup.created_at.asc())
        .limit(to_delete)
    )
    oldest_backups = oldest_result.scalars().all()

    for backup in oldest_backups:
        # Supprimer les fichiers physiques (storage_path peut être NULL pour anciennes lignes)
        if backup.storage_path:
            storage_path = STORAGE_BASE_DIR / backup.storage_path
            if storage_path.exists():
                storage_path.unlink()
        if backup.encrypted_path:
            encrypted_path = STORAGE_BASE_DIR / backup.encrypted_path
            if encrypted_path.exists():
                encrypted_path.unlink()

        await db.delete(backup)

    await db.commit()
    logger.info(f"🗑️ Rétention appliquée pour {app_slug}: {to_delete} backup(s) supprimé(s)")


# =============================================================================
# ENDPOINTS POUR LES APPLICATIONS (M2M)
# =============================================================================

@router.post("/api/apps/{slug}/backup", response_model=AppBackupResponse)
async def receive_backup(
    slug: str,
    request: Request,
    file: UploadFile = File(..., description="Fichier ZIP du backup"),
    backup_id: Optional[str] = Form(None),
    app_version: Optional[str] = Form(None),
    format_version: Optional[str] = Form(None),
    total_items: int = Form(0),
    total_preferences: int = Form(0),
    total_files: int = Form(0),
    total_tables: int = Form(0),
    total_rows: int = Form(0),
    checksum: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    includes_vaultwarden_snapshot: bool = Form(False),
    includes_secret_bundle: bool = Form(False),
    restore_requires_offline: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_entity)
):
    """
    Reçoit un backup poussé par une application (sync-to-hub).
    
    L'application envoie son fichier ZIP de backup avec les métadonnées.
    Le HUB stocke le fichier et enregistre les métadonnées en base.
    
    Authentification: Token M2M (service account) ou utilisateur admin.
    """
    app = await get_app_or_404(db, slug)
    await check_backup_entity_access(slug, current_user, db)

    if hasattr(current_user, 'client_id'):
        created_by = current_user.name
        created_by_type = "app"
    else:
        created_by = current_user.username
        created_by_type = "user"

    # Compatibilité: avant l'usage explicite de Form(...), certains clients
    # pouvaient envoyer ces métadonnées en query params.
    query_params = request.query_params
    backup_id = backup_id or query_params.get("backup_id")
    app_version = app_version or query_params.get("app_version")
    format_version = format_version or query_params.get("format_version")
    total_items = total_items if total_items > 0 else safe_int(query_params.get("total_items"), total_items)
    total_preferences = total_preferences if total_preferences > 0 else safe_int(query_params.get("total_preferences"), total_preferences)
    total_files = total_files if total_files > 0 else safe_int(query_params.get("total_files"), total_files)
    total_tables = total_tables if total_tables > 0 else safe_int(query_params.get("total_tables"), total_tables)
    total_rows = total_rows if total_rows > 0 else safe_int(query_params.get("total_rows"), total_rows)
    checksum = checksum or query_params.get("checksum")
    description = description or query_params.get("description")
    includes_vaultwarden_snapshot = includes_vaultwarden_snapshot or parse_bool(query_params.get("includes_vaultwarden_snapshot"))
    includes_secret_bundle = includes_secret_bundle or parse_bool(query_params.get("includes_secret_bundle"))
    restore_requires_offline = restore_requires_offline or parse_bool(query_params.get("restore_requires_offline"))
    
    # Générer un backup_id si non fourni
    if not backup_id:
        backup_id = str(uuid.uuid4())
    
    # Lire le contenu du fichier
    content = await file.read()
    file_size = len(content)
    
    # Vérifier le checksum si fourni
    computed_checksum = compute_checksum(content)
    if checksum and checksum != computed_checksum:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Checksum invalide - le fichier est peut-être corrompu"
        )
    
    # Extraire les métadonnées du ZIP si les valeurs ne sont pas fournies
    zip_metadata = extract_metadata_from_zip(content)
    
    # Utiliser les valeurs du ZIP si non fournies par l'appelant
    final_app_version = app_version or zip_metadata.get("app_version")
    final_total_items = total_items if total_items > 0 else zip_metadata.get("total_items", 0)
    final_total_preferences = total_preferences if total_preferences > 0 else zip_metadata.get("total_preferences", 0)
    final_total_files = total_files if total_files > 0 else zip_metadata.get("total_files", 0)
    final_format_version = format_version or zip_metadata.get("format_version")
    final_total_tables = total_tables if total_tables > 0 else zip_metadata.get("total_tables", 0)
    final_total_rows = total_rows if total_rows > 0 else zip_metadata.get("total_rows", 0)
    final_includes_vaultwarden_snapshot = (
        includes_vaultwarden_snapshot or bool(zip_metadata.get("includes_vaultwarden_snapshot"))
    )
    final_includes_secret_bundle = includes_secret_bundle or bool(zip_metadata.get("includes_secret_bundle"))
    final_restore_requires_offline = (
        restore_requires_offline
        or bool(zip_metadata.get("restore_requires_offline"))
        or final_includes_vaultwarden_snapshot
    )
    
    # Générer une description automatique si non fournie
    content_summary = zip_metadata.get("content_summary", "")
    final_description = description or content_summary
    
    # Créer le dossier de stockage
    app_dir = ensure_storage_dir(slug)
    
    # Nom du fichier: backup_{backup_id}.zip
    filename = f"backup_{backup_id}.zip"
    storage_path = f"{slug}/{filename}"
    full_path = app_dir / filename
    
    # Sauvegarder le fichier
    with open(full_path, "wb") as f:
        f.write(content)
    
    # Créer l'enregistrement en base
    backup = AppBackup(
        backup_id=backup_id,
        app_id=app.id,
        app_slug=slug,
        app_version=final_app_version,
        total_items=final_total_items,
        total_preferences=final_total_preferences,
        total_files=final_total_files,
        format_version=final_format_version,
        total_tables=final_total_tables,
        total_rows=final_total_rows,
        includes_vaultwarden_snapshot=final_includes_vaultwarden_snapshot,
        includes_secret_bundle=final_includes_secret_bundle,
        restore_requires_offline=final_restore_requires_offline,
        total_size_bytes=file_size,
        checksum=computed_checksum,
        storage_path=storage_path,
        created_by=created_by,
        created_by_type=created_by_type,
        description=final_description,
        status="success",
        started_at=utcnow(),
        completed_at=utcnow(),
        duration_seconds=0,
        progress_step="done",
        progress_percent=100,
    )

    db.add(backup)

    # Mettre à jour l'application
    app.last_backup_at = utcnow()
    app.last_backup_id = backup_id
    app.last_backup_status = "success"

    await db.commit()
    await db.refresh(backup)

    # Appliquer la politique de rétention
    await apply_retention_policy(db, slug, app.backup_retention)

    logger.info(f"📦 Backup reçu pour {slug}: {backup_id} ({human_readable_size(file_size)})")

    # Notifier les clients SSE qu'un backup a été reçu (rafraîchir la vue admin)
    try:
        from app.routes.events import broadcast_event
        await broadcast_event("backup_completed", {
            "app_slug": slug,
            "backup_id": backup_id,
            "duration_seconds": 0,
            "source": "push",
        })
        await broadcast_event("apps_updated", {"reason": "backup_received", "slug": slug})
    except Exception as exc:
        logger.warning(f"⚠️ Impossible de diffuser SSE backup_completed pour {slug}: {exc}")

    return AppBackupResponse.from_orm_model(backup)


@router.get(
    "/api/apps/{slug}/backup/list",
    response_model=AppBackupListResponse,
    include_in_schema=False,
)
@router.get("/api/apps/{slug}/backup", response_model=AppBackupListResponse)
async def list_app_backups(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_entity)
):
    """
    Liste tous les backups d'une application.

    L'alias /backup/list est conservé pour compatibilite avec les anciens clients.
    """
    app = await get_app_or_404(db, slug)
    await check_backup_entity_access(slug, current_user, db)

    result = await db.execute(
        select(AppBackup)
        .where(AppBackup.app_slug == slug)
        .order_by(AppBackup.created_at.desc())
    )
    backups = result.scalars().all()
    
    backup_config = AppBackupConfig(
        backup_mode=_safe_backup_mode(app.backup_mode),
        backup_cron=app.backup_cron,
        backup_retention=app.backup_retention or 5,
        backup_quiesce=bool(getattr(app, "backup_quiesce", False)),
    )
    
    return AppBackupListResponse(
        app_slug=slug,
        app_name=app.name,
        backups=[AppBackupResponse.from_orm_model(b) for b in backups],
        total=len(backups),
        backup_config=backup_config,
        is_unmanaged=bool(getattr(app, "is_unmanaged", False))
    )


@router.get("/api/apps/{slug}/backup/latest")
async def get_latest_backup(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_entity)
):
    """
    Récupère les informations du dernier backup d'une application.
    
    ⚠️ Le téléchargement direct des backups non chiffrés a été désactivé pour des raisons de sécurité.
    Utilisez l'endpoint /export pour télécharger une version chiffrée.
    """
    app = await get_app_or_404(db, slug)
    await check_backup_entity_access(slug, current_user, db)

    result = await db.execute(
        select(AppBackup)
        .where(AppBackup.app_slug == slug)
        .order_by(AppBackup.created_at.desc())
        .limit(1)
    )
    backup = result.scalar_one_or_none()
    
    if not backup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aucun backup trouvé pour l'application '{slug}'"
        )
    
    # Retourner les infos du backup sans le fichier
    return AppBackupResponse.from_orm_model(backup)


@router.get("/api/apps/{slug}/backup/{backup_id}/download")
async def download_backup(
    slug: str,
    backup_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    ⚠️ DÉSACTIVÉ - Téléchargement des backups non chiffrés interdit.
    
    Pour des raisons de sécurité, le téléchargement direct des backups 
    non chiffrés a été désactivé.
    
    Utilisez plutôt:
    - POST /api/apps/{slug}/backup/{backup_id}/export : pour créer une version chiffrée
    - GET /api/apps/{slug}/backup/{backup_id}/export/download : pour télécharger la version chiffrée
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Téléchargement des backups non chiffrés désactivé. Utilisez /export pour obtenir une version chiffrée."
    )


@router.post("/api/apps/{slug}/backup/{backup_id}/export", response_model=BackupExportResponse)
async def export_encrypted_backup(
    slug: str,
    backup_id: str,
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Exporte un backup chiffré par mot de passe (format v2 portable,
    déchiffrable sur tout hub avec ce mot de passe).
    L'ancien mode RSA reste déchiffrable à l'import.
    """
    backup = await get_backup_or_404(db, backup_id)
    
    if backup.app_slug != slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le backup ne correspond pas à l'application"
        )
    
    # Valider le mot de passe avant toute lecture/écriture
    validate_export_password(password, password_confirm)
    
    # Lire le fichier original
    file_path = get_backup_file_path_or_404(backup)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fichier de backup non trouvé"
        )
    
    original_data = file_path.read_bytes()
    
    # Chiffrer en mode password (portable)
    try:
        encrypted_package = create_password_encrypted_package(original_data, backup_id, slug, password)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur de chiffrement: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors du chiffrement du backup"
        )
    
    # Sauvegarder le fichier chiffré
    encrypted_filename = f"backup_{backup_id}.encrypted"
    encrypted_path = f"{slug}/{encrypted_filename}"
    full_encrypted_path = STORAGE_BASE_DIR / encrypted_path
    
    with open(full_encrypted_path, "wb") as f:
        f.write(encrypted_package)
    
    # Mettre à jour le backup
    backup.encrypted_path = encrypted_path
    backup.is_encrypted = True
    await db.commit()
    
    encrypted_size = len(encrypted_package)
    
    logger.info(f"🔐 Backup {backup_id} chiffré: {human_readable_size(encrypted_size)}")
    
    return BackupExportResponse(
        success=True,
        message="Backup chiffré avec succès",
        backup_id=backup_id,
        encrypted_filename=encrypted_filename,
        encrypted_size_bytes=encrypted_size,
        encrypted_size_human=human_readable_size(encrypted_size)
    )


@router.get("/api/apps/{slug}/backup/{backup_id}/export/download")
async def download_encrypted_backup(
    slug: str,
    backup_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Télécharge le fichier de backup chiffré.
    
    Le fichier doit d'abord être chiffré via l'endpoint /export.
    """
    backup = await get_backup_or_404(db, backup_id)
    
    if backup.app_slug != slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le backup ne correspond pas à l'application"
        )
    
    if not backup.encrypted_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce backup n'a pas encore été chiffré. Utilisez d'abord l'endpoint /export"
        )
    
    encrypted_path = STORAGE_BASE_DIR / backup.encrypted_path
    if not encrypted_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fichier chiffré non trouvé"
        )
    
    return FileResponse(
        path=str(encrypted_path),
        filename=f"backup_{slug}_{backup_id}.encrypted",
        media_type="application/octet-stream"
    )


@router.delete("/api/apps/{slug}/backup/{backup_id}")
async def delete_backup(
    slug: str,
    backup_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Supprime un backup spécifique.
    
    Supprime le fichier physique et l'enregistrement en base.
    """
    backup = await get_backup_or_404(db, backup_id)
    
    if backup.app_slug != slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le backup ne correspond pas à l'application"
        )
    
    # Supprimer les fichiers physiques
    delete_backup_file_if_present(backup.storage_path)
    delete_backup_file_if_present(backup.encrypted_path)
    
    await db.delete(backup)
    await db.commit()
    
    logger.info(f"🗑️ Backup {backup_id} supprimé pour {slug}")
    
    return {"message": f"Backup {backup_id} supprimé avec succès"}


# =============================================================================
# CONFIGURATION DU SCHEDULING
# =============================================================================

@router.get("/api/apps/{slug}/backup/config", response_model=AppBackupConfig)
async def get_backup_config(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_admin_or_admin_sa)
):
    """
    Récupère la configuration de sauvegarde d'une application.
    """
    app = await get_app_or_404(db, slug)
    
    return AppBackupConfig(
        backup_mode=_safe_backup_mode(app.backup_mode),
        backup_cron=app.backup_cron,
        backup_retention=app.backup_retention or 5,
        backup_quiesce=bool(getattr(app, "backup_quiesce", False)),
    )


@router.put("/api/apps/{slug}/backup/config", response_model=AppBackupConfig)
async def update_backup_config(
    slug: str,
    config: AppBackupConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Met à jour la configuration de sauvegarde d'une application.
    
    - **backup_mode**: disabled, manual, scheduled
    - **backup_cron**: Expression cron (ex: "0 2 * * *" = tous les jours à 2h)
    - **backup_retention**: Nombre de backups à conserver (0 = illimité)
    """
    from croniter import croniter
    
    app = await get_app_or_404(db, slug)
    
    if config.backup_mode is not None:
        app.backup_mode = config.backup_mode.value
    
    if config.backup_cron is not None:
        # Valider l'expression cron
        if config.backup_cron:
            try:
                croniter(config.backup_cron)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Expression cron invalide"
                )
            app.backup_cron = config.backup_cron
            
            # Calculer le prochain backup si mode scheduled
            if app.backup_mode == "scheduled":
                cron = croniter(config.backup_cron, utcnow())
                app.next_backup_at = cron.get_next(datetime)
        else:
            app.backup_cron = None
            app.next_backup_at = None
    
    if config.backup_retention is not None:
        app.backup_retention = config.backup_retention
        # Appliquer immédiatement la nouvelle rétention
        await apply_retention_policy(db, slug, config.backup_retention)

    if config.backup_quiesce is not None:
        app.backup_quiesce = bool(config.backup_quiesce)

    await db.commit()
    await db.refresh(app)
    
    logger.info(f"⚙️ Configuration backup mise à jour pour {slug}: mode={app.backup_mode}, cron={app.backup_cron}")
    
    return AppBackupConfig(
        backup_mode=_safe_backup_mode(app.backup_mode),
        backup_cron=app.backup_cron,
        backup_retention=app.backup_retention or 5,
        backup_quiesce=bool(getattr(app, "backup_quiesce", False)),
    )


class BackupExecutionError(Exception):
    """Erreur survenue durant l'exécution d'un backup (réseau, app, stockage)."""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --- Constantes pour le suivi de progression -----------------------------
PROGRESS_STEPS = {
    "queued":            ("queued", 0),
    "create":            ("create", 10),
    "downloading":       ("downloading", 30),
    "download_complete": ("download_complete", 70),
    "storing":           ("storing", 85),
    "done":              ("done", 100),
}

# Au-delà de cette ancienneté, un backup resté `running` est considéré orphelin
# (cas crash du Hub pendant l'exécution). Il sera marqué `timeout` au démarrage.
STALE_RUNNING_BACKUP_MAX_AGE_SECONDS = 30 * 60  # 30 minutes


async def _emit_backup_event(event_type: str, payload: dict) -> None:
    """Diffuse un événement SSE backup_* sans faire échouer l'opération en cas d'erreur."""
    try:
        from app.routes.events import broadcast_event
        await broadcast_event(event_type, payload)
    except Exception as exc:
        logger.warning(f"⚠️ SSE {event_type} non diffusé: {exc}")


async def _update_backup_progress(
    db: AsyncSession,
    backup: AppBackup,
    step: str,
    *,
    extra_payload: Optional[dict] = None,
) -> None:
    """Met à jour la progression d'un backup en base et émet un événement SSE."""
    label, percent = PROGRESS_STEPS.get(step, (step, backup.progress_percent or 0))
    backup.progress_step = label
    backup.progress_percent = percent
    if step != "done":
        backup.status = "running"
    await db.commit()

    payload = {
        "backup_id": backup.backup_id,
        "app_slug": backup.app_slug,
        "progress_step": label,
        "progress_percent": percent,
        "status": backup.status,
    }
    if extra_payload:
        payload.update(extra_payload)
    await _emit_backup_event("backup_progress", payload)


async def cleanup_stale_running_backups() -> int:
    """Kill-switch : marque comme `timeout` les backups restés `running` trop longtemps.

    Appelé au démarrage du Hub : si le processus a crashé pendant un backup,
    la ligne reste bloquée en `running`. On les requalifie pour libérer le verrou
    qui empêche un nouveau déclenchement.

    Retourne le nombre de backups requalifiés.
    """
    from app.database import async_session_maker
    from datetime import timedelta

    cutoff = utcnow() - timedelta(seconds=STALE_RUNNING_BACKUP_MAX_AGE_SECONDS)
    requalified = 0

    async with async_session_maker() as db:
        result = await db.execute(
            select(AppBackup).where(
                AppBackup.status.in_(["pending", "running"]),
            )
        )
        stale = result.scalars().all()

        for backup in stale:
            ref = _naive_utc(backup.started_at or backup.created_at)
            # Toujours requalifier ce qui est plus vieux que cutoff ; au démarrage
            # on requalifie aussi les `pending` même récents (pas de tâche en cours).
            if ref and ref > cutoff and backup.status == "running":
                continue
            backup.status = "timeout"
            backup.completed_at = utcnow()
            backup.error_message = (
                "Backup interrompu (Hub redémarré ou délai dépassé). "
                "Statut requalifié au démarrage."
            )
            started = _naive_utc(backup.started_at)
            completed = _naive_utc(backup.completed_at)
            if started and completed:
                backup.duration_seconds = int(
                    (completed - started).total_seconds()
                )
            requalified += 1

            # Réaligner le statut de l'application si elle pointe sur ce backup
            if backup.app_id:
                app_res = await db.execute(
                    select(Application).where(Application.id == backup.app_id)
                )
                target_app = app_res.scalar_one_or_none()
                if target_app and target_app.last_backup_status == "running":
                    target_app.last_backup_status = "failed"

        if requalified:
            await db.commit()
            logger.warning(
                f"⏱️ Kill-switch backups : {requalified} backup(s) requalifié(s) en 'timeout' au démarrage"
            )

    return requalified


async def perform_app_backup(
    db: AsyncSession,
    app: Application,
    *,
    created_by: str,
    created_by_type: str = "hub",
    description_prefix: Optional[str] = None,
    backup: Optional[AppBackup] = None,
) -> AppBackup:
    """
    Exécute un backup réel pour une application : appelle l'API distante,
    télécharge le ZIP, l'enregistre localement, met à jour les statuts et
    applique la politique de rétention.

    La ligne `AppBackup` est créée immédiatement avec `status='running'` (si non
    fournie) afin d'exposer la progression. La progression est diffusée via SSE
    (`backup_progress`, `backup_completed`, `backup_failed`).

    Levée :
        BackupExecutionError en cas d'échec contrôlé (réseau, app, validation).
    """
    import httpx
    from app.auth import create_access_token
    from datetime import timedelta

    slug = app.slug

    # 0. Créer ou réutiliser la ligne AppBackup avec statut "running"
    started_at = utcnow()
    if backup is None:
        backup = AppBackup(
            backup_id=str(uuid.uuid4()),
            app_id=app.id,
            app_slug=slug,
            status="running",
            started_at=started_at,
            progress_step="queued",
            progress_percent=0,
            created_by=created_by,
            created_by_type=created_by_type,
            description=description_prefix or f"Backup déclenché par {created_by}",
        )
        db.add(backup)
        await db.commit()
        await db.refresh(backup)
    else:
        backup.status = "running"
        if not backup.started_at:
            backup.started_at = started_at
        await db.commit()

    app.last_backup_status = "running"
    await db.commit()

    async def _fail(message: str, status_code: int = 500, *, kind: str = "failed") -> None:
        """Marque le backup en échec, diffuse l'événement SSE et lève l'erreur."""
        backup.status = kind  # 'failed' ou 'timeout'
        backup.completed_at = utcnow()
        backup.error_message = message[:2000]
        started = _naive_utc(backup.started_at)
        completed = _naive_utc(backup.completed_at)
        if started and completed:
            backup.duration_seconds = int(
                (completed - started).total_seconds()
            )
        app.last_backup_status = "failed"
        try:
            await db.commit()
        except Exception:
            await db.rollback()
        await _emit_backup_event("backup_failed", {
            "backup_id": backup.backup_id,
            "app_slug": slug,
            "status": kind,
            "error_message": backup.error_message,
            "duration_seconds": backup.duration_seconds,
        })
        raise BackupExecutionError(message, status_code=status_code)

    try:
        if getattr(app, "is_unmanaged", False):
            await _fail("Cette application est non gérée (lien only) — sauvegarde indisponible", status_code=400)

        if app.backup_mode == "disabled":
            await _fail("Les sauvegardes sont désactivées pour cette application", status_code=400)

        if bool(getattr(app, "is_system_app", False)) and slug == "hub":
            await _update_backup_progress(db, backup, "create")
            from app.hub_self_backup import create_hub_backup_archive

            try:
                archive_info = await create_hub_backup_archive(
                    db,
                    backup_id=backup.backup_id,
                    created_by=created_by,
                    include_jwt_keys=False,
                    include_sa_secrets=True,
                    include_app_backups=False,
                )
            except Exception as archive_error:
                await _fail(f"Impossible de créer l'archive de configuration Hub: {archive_error}")

            file_size = archive_info["total_size_bytes"]
            await _update_backup_progress(db, backup, "download_complete", extra_payload={
                "total_size_bytes": file_size,
            })
            await _update_backup_progress(db, backup, "storing")

            backup.app_version = "hub"
            backup.format_version = archive_info.get("format_version")
            backup.total_items = archive_info.get("total_items", 0)
            backup.total_preferences = 0
            backup.total_files = archive_info.get("total_files", 1)
            backup.total_tables = archive_info.get("total_tables", 0)
            backup.total_rows = archive_info.get("total_rows", 0)
            backup.includes_vaultwarden_snapshot = False
            backup.includes_secret_bundle = False
            backup.restore_requires_offline = False
            backup.total_size_bytes = file_size
            backup.checksum = archive_info.get("checksum")
            backup.storage_path = archive_info.get("storage_path")
            backup.description = (
                f"{description_prefix} par {created_by}" if description_prefix
                else archive_info.get("description") or f"Backup déclenché par {created_by}"
            )
            backup.status = "success"
            backup.completed_at = utcnow()
            backup.progress_step = "done"
            backup.progress_percent = 100
            backup.error_message = None
            started = _naive_utc(backup.started_at)
            completed = _naive_utc(backup.completed_at)
            if started and completed:
                backup.duration_seconds = int((completed - started).total_seconds())

            app.last_backup_at = utcnow()
            app.last_backup_id = backup.backup_id
            app.last_backup_status = "success"

            await db.commit()
            await db.refresh(backup)

            await apply_retention_policy(db, slug, app.backup_retention)

            logger.info(
                f"✅ Backup {backup.backup_id} stocké pour {slug} "
                f"({human_readable_size(file_size)}, {backup.duration_seconds}s)"
            )

            await _emit_backup_event("backup_completed", {
                "backup_id": backup.backup_id,
                "app_slug": slug,
                "duration_seconds": backup.duration_seconds,
                "total_size_bytes": file_size,
                "status": "success",
            })
            await _emit_backup_event("apps_updated", {"reason": "backup_completed", "slug": slug})

            return backup

        if not app.api_url:
            await _fail("L'application n'a pas d'URL d'API configurée", status_code=400)

        # Récupérer un service account utilisable
        sa_result = await db.execute(
            select(ServiceAccount)
            .join(ServiceAccount.applications)
            .where(Application.slug == slug)
            .limit(1)
        )
        sa = sa_result.scalar_one_or_none()

        if not sa:
            await _fail(
                "Aucun service account trouvé pour cette application. Créez-en un avec accès à l'app.",
                status_code=400,
            )

        # Token M2M admin pour l'app cible
        m2m_token, _, _ = create_access_token(
            username=sa.client_id,
            apps={slug: "admin"},
            expires_delta=timedelta(minutes=10),
            is_service_account=True,
        )

        logger.info(f"🔄 Déclenchement backup pour {slug} via {app.api_url}")

        create_url = f"{app.api_url.rstrip('/')}/api/admin/backup"
        create_params = {}
        if getattr(app, "backup_quiesce", False):
            create_params["quiesce_vaultwarden"] = "true"
            logger.info(
                f"🛑 Quiesce Vaultwarden demande pour backup {slug} "
                "(stop/start auto du conteneur pendant le snapshot)"
            )

        async with httpx.AsyncClient(timeout=120.0) as client:
            await _update_backup_progress(db, backup, "create")

            try:
                create_response = await client.post(
                    create_url,
                    headers={"Authorization": f"Bearer {m2m_token}"},
                    params=create_params or None,
                )
            except httpx.RequestError as net_err:
                from app.outbox import outbox_service
                try:
                    await outbox_service.enqueue(
                        target_url=create_url,
                        method="POST",
                        headers={"Authorization": f"Bearer {m2m_token}"},
                        payload=create_params,
                        target_slug=slug,
                        auth_context={"type": "m2m", "slug": slug},
                        max_retries=5,
                    )
                    logger.info(f"📦 Backup outbox créé pour {slug}")
                except Exception as outbox_err:
                    logger.error(f"❌ Outbox enqueue failed: {outbox_err}")
                await _fail(
                    f"Impossible de contacter {create_url}: {net_err}",
                    status_code=502,
                )

            if create_response.status_code != 200:
                error_detail = create_response.text[:500] if create_response.text else "Pas de détails"
                logger.error(
                    f"❌ Erreur création backup {slug}: {create_response.status_code} - {error_detail}"
                )
                if create_response.status_code >= 500:
                    from app.outbox import outbox_service
                    try:
                        await outbox_service.enqueue(
                            target_url=create_url,
                            method="POST",
                            headers={"Authorization": f"Bearer {m2m_token}"},
                            payload=create_params,
                            target_slug=slug,
                            auth_context={"type": "m2m", "slug": slug},
                            max_retries=5,
                        )
                        logger.info(f"📦 Backup outbox créé pour {slug} (HTTP {create_response.status_code})")
                    except Exception as outbox_err:
                        logger.error(f"❌ Outbox enqueue failed: {outbox_err}")
                await _fail(
                    f"L'application a retourné HTTP {create_response.status_code} sur {create_url} : {error_detail}",
                    status_code=502,
                )

            backup_info = create_response.json()
            remote_backup_id = metadata_value(backup_info, "backup_id")
            logger.info(f"✅ Backup créé sur l'app {slug}: {remote_backup_id}")

            await _update_backup_progress(db, backup, "downloading", extra_payload={
                "remote_backup_id": remote_backup_id,
            })

            # 2. Télécharger le ZIP — avec un retry à timeout étendu si le premier
            # essai timeout (cas où l'app a bien créé le backup mais répond lentement).
            download_url = f"{app.api_url.rstrip('/')}/api/admin/backup/download?backup_id={remote_backup_id}"
            headers = {"Authorization": f"Bearer {m2m_token}"}
            download_response = None
            try:
                download_response = await client.get(download_url, headers=headers)
            except httpx.TimeoutException:
                logger.warning(
                    f"⏳ Timeout download backup {slug} (1er essai). Retry avec timeout étendu (300s)..."
                )

            if download_response is None:
                # Retry avec timeout étendu
                async with httpx.AsyncClient(timeout=300.0) as retry_client:
                    try:
                        download_response = await retry_client.get(download_url, headers=headers)
                    except httpx.TimeoutException:
                        await _fail(
                            f"Timeout lors du téléchargement du backup {remote_backup_id} "
                            f"depuis {download_url} (>300s). L'app a peut-être créé le backup "
                            "mais la transmission a échoué.",
                            status_code=504,
                            kind="timeout",
                        )
                    except httpx.RequestError as net_err:
                        await _fail(
                            f"Erreur réseau au retry de download ({download_url}): {net_err}",
                            status_code=502,
                        )

            if download_response.status_code != 200:
                detail = download_response.text[:500] if download_response.text else "Pas de détails"
                logger.error(
                    f"❌ Erreur téléchargement backup {slug}: {download_response.status_code} - {detail}"
                )
                await _fail(
                    f"L'application a retourné HTTP {download_response.status_code} sur {download_url} : {detail}",
                    status_code=502,
                )

            zip_content = download_response.content
            file_size = len(zip_content)
            logger.info(f"📥 Backup téléchargé pour {slug}: {human_readable_size(file_size)}")

        await _update_backup_progress(db, backup, "download_complete", extra_payload={
            "total_size_bytes": file_size,
        })

        # 3. Stocker le backup localement
        backup_id = str(remote_backup_id) if remote_backup_id else backup.backup_id
        checksum = compute_checksum(zip_content)

        app_dir = ensure_storage_dir(slug)
        filename = f"backup_{backup_id}.zip"
        storage_path = f"{slug}/{filename}"
        full_path = app_dir / filename

        await _update_backup_progress(db, backup, "storing")

        try:
            with open(full_path, "wb") as f:
                f.write(zip_content)
        except OSError as os_err:
            await _fail(
                f"Impossible d'écrire le fichier de backup {full_path}: {os_err}",
                status_code=500,
            )

        zip_metadata = extract_metadata_from_zip(zip_content)

        app_version = metadata_value(backup_info, "app_version") or zip_metadata.get("app_version")
        format_version = metadata_value(backup_info, "format_version") or zip_metadata.get("format_version")
        total_items = safe_int(metadata_value(backup_info, "total_items"), zip_metadata.get("total_items", 0))
        total_preferences = safe_int(
            metadata_value(backup_info, "total_preferences"), zip_metadata.get("total_preferences", 0)
        )
        total_files = safe_int(metadata_value(backup_info, "total_files"), zip_metadata.get("total_files", 0))
        total_tables = safe_int(metadata_value(backup_info, "total_tables"), zip_metadata.get("total_tables", 0))
        total_rows = safe_int(metadata_value(backup_info, "total_rows"), zip_metadata.get("total_rows", 0))
        includes_vaultwarden_snapshot = (
            parse_bool(metadata_value(backup_info, "includes_vaultwarden_snapshot"))
            or bool(zip_metadata.get("includes_vaultwarden_snapshot"))
        )
        includes_secret_bundle = (
            parse_bool(metadata_value(backup_info, "includes_secret_bundle"))
            or bool(zip_metadata.get("includes_secret_bundle"))
        )
        restore_requires_offline = (
            parse_bool(metadata_value(backup_info, "restore_requires_offline"))
            or bool(zip_metadata.get("restore_requires_offline"))
            or includes_vaultwarden_snapshot
        )
        content_summary = zip_metadata.get("content_summary", "")

        description = content_summary or (
            f"{description_prefix} par {created_by}" if description_prefix
            else f"Backup déclenché par {created_by}"
        )

        # Mise à jour finale de la ligne (qui a été créée à status=running)
        backup.backup_id = backup_id  # peut être différent du UUID temporaire
        backup.app_version = app_version
        backup.format_version = format_version
        backup.total_items = total_items
        backup.total_preferences = total_preferences
        backup.total_files = total_files
        backup.total_tables = total_tables
        backup.total_rows = total_rows
        backup.includes_vaultwarden_snapshot = includes_vaultwarden_snapshot
        backup.includes_secret_bundle = includes_secret_bundle
        backup.restore_requires_offline = restore_requires_offline
        backup.total_size_bytes = file_size
        backup.checksum = checksum
        backup.storage_path = storage_path
        backup.description = description
        backup.status = "success"
        backup.completed_at = utcnow()
        backup.progress_step = "done"
        backup.progress_percent = 100
        backup.error_message = None
        started = _naive_utc(backup.started_at)
        completed = _naive_utc(backup.completed_at)
        if started and completed:
            backup.duration_seconds = int(
                (completed - started).total_seconds()
            )

        app.last_backup_at = utcnow()
        app.last_backup_id = backup_id
        app.last_backup_status = "success"

        await db.commit()
        await db.refresh(backup)

        await apply_retention_policy(db, slug, app.backup_retention)

        logger.info(
            f"✅ Backup {backup_id} stocké pour {slug} "
            f"({human_readable_size(file_size)}, {backup.duration_seconds}s)"
        )

        await _emit_backup_event("backup_completed", {
            "backup_id": backup_id,
            "app_slug": slug,
            "duration_seconds": backup.duration_seconds,
            "total_size_bytes": file_size,
            "status": "success",
        })
        await _emit_backup_event("apps_updated", {"reason": "backup_completed", "slug": slug})

        return backup

    except BackupExecutionError:
        # _fail a déjà mis à jour la base et émis l'événement SSE.
        raise
    except Exception as exc:
        # Erreur inattendue : marquer en échec et propager.
        logger.error(f"💥 Erreur inattendue durant backup {slug}: {exc}", exc_info=True)
        try:
            await _fail(f"Erreur inattendue: {exc}", status_code=500)
        except BackupExecutionError:
            pass
        raise


async def _run_backup_job(backup_pk: int, app_id: int, created_by: str) -> None:
    """Tâche de fond exécutant un backup déclenché manuellement.

    Ouvre sa propre session DB pour ne pas dépendre de la requête HTTP qui a
    rendu 202 immédiatement.
    """
    from app.database import async_session_maker

    async with async_session_maker() as db:
        try:
            backup_res = await db.execute(
                select(AppBackup).where(AppBackup.id == backup_pk)
            )
            backup = backup_res.scalar_one_or_none()
            app_res = await db.execute(
                select(Application).where(Application.id == app_id)
            )
            app = app_res.scalar_one_or_none()

            if not backup or not app:
                logger.error(
                    f"❌ _run_backup_job introuvable: backup_pk={backup_pk}, app_id={app_id}"
                )
                return

            await perform_app_backup(
                db,
                app,
                created_by=created_by,
                created_by_type="hub",
                description_prefix="Backup déclenché manuellement depuis le HUB",
                backup=backup,
            )
        except BackupExecutionError as be:
            logger.error(f"❌ Backup async échoué: {be.message}")
        except Exception as exc:
            logger.error(f"💥 Erreur inattendue _run_backup_job: {exc}", exc_info=True)


@router.post(
    "/api/apps/{slug}/backup/trigger",
    response_model=BackupTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_backup(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Déclenche un backup en tâche de fond pour une application.

    Le HUB renvoie immédiatement HTTP 202 avec le backup_id et l'état initial.
    La progression est diffusée via SSE (`backup_progress`, `backup_completed`,
    `backup_failed`). L'UI doit s'abonner pour suivre l'avancement.

    Retourne 409 Conflict si un backup est déjà en cours pour cette app.
    """
    app = await get_app_or_404(db, slug)

    if getattr(app, "is_unmanaged", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cette application est non gérée (lien only) — sauvegarde indisponible",
        )

    if app.backup_mode == "disabled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Les sauvegardes sont désactivées pour cette application",
        )

    # Vérifier qu'aucun backup n'est déjà en cours pour cette app
    running_res = await db.execute(
        select(AppBackup)
        .where(AppBackup.app_slug == slug)
        .where(AppBackup.status.in_(["pending", "running"]))
        .limit(1)
    )
    in_progress = running_res.scalar_one_or_none()
    if in_progress is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Un backup est déjà en cours pour {slug} "
                f"(backup_id={in_progress.backup_id}, étape={in_progress.progress_step})."
            ),
        )

    # Créer la ligne AppBackup en statut 'pending' pour verrouiller
    started_at = utcnow()
    backup = AppBackup(
        backup_id=str(uuid.uuid4()),
        app_id=app.id,
        app_slug=slug,
        status="pending",
        started_at=started_at,
        progress_step="queued",
        progress_percent=0,
        created_by=current_user.username,
        created_by_type="hub",
        description="Backup déclenché manuellement depuis le HUB",
    )
    db.add(backup)
    app.last_backup_status = "running"
    await db.commit()
    await db.refresh(backup)

    # Émettre l'événement SSE 'backup_started'
    await _emit_backup_event("backup_started", {
        "backup_id": backup.backup_id,
        "app_slug": slug,
        "app_name": app.name,
        "started_at": started_at.isoformat(),
        "created_by": current_user.username,
    })

    # Lancer la tâche de fond (capturer les valeurs primitives, pas les ORM)
    backup_pk = backup.id
    app_id = app.id
    asyncio.create_task(_run_backup_job(backup_pk, app_id, current_user.username))

    return BackupTriggerResponse(
        success=True,
        message="Backup démarré, suivi en cours via SSE",
        backup_id=backup.backup_id,
        app_slug=slug,
        status="pending",
        started_at=started_at,
    )


# =============================================================================
# RESTAURATION VERS UNE APPLICATION
# =============================================================================

def validate_vaultwarden_restore_options(
    options: RestoreToAppRequest,
    backup: AppBackup | None = None,
):
    """Applique les garde-fous Hub avant une restauration Vaultwarden complète."""
    if options.restore_vaultwarden and not options.confirm_full_restore:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm_full_restore=true est requis pour restaurer le snapshot Vaultwarden complet"
        )

    if backup is None:
        return

    if options.restore_vaultwarden and not getattr(backup, "includes_vaultwarden_snapshot", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce backup ne contient pas de snapshot Vaultwarden complet"
        )

    if options.restore_secret_bundle and not getattr(backup, "includes_secret_bundle", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce backup ne contient pas de bundle de secrets chiffré"
        )


def build_restore_form_data(options: RestoreToAppRequest) -> dict:
    """Construit les champs multipart relayés à l'application cible."""
    return {
        "clear_existing": bool_to_form_value(options.clear_existing),
        "restore_items": bool_to_form_value(options.restore_items),
        "restore_preferences": bool_to_form_value(options.restore_preferences),
        "restore_files": bool_to_form_value(options.restore_files),
        "restore_test_data": bool_to_form_value(options.restore_test_data),
        "restore_vaultwarden": bool_to_form_value(options.restore_vaultwarden),
        "restore_secret_bundle": bool_to_form_value(options.restore_secret_bundle),
        "require_vaultwarden_offline": bool_to_form_value(options.require_vaultwarden_offline),
        "confirm_full_restore": bool_to_form_value(options.confirm_full_restore),
    }


def build_restore_response(result: dict, slug: str, backup_id: str) -> RestoreToAppResponse:
    """Normalise la réponse de l'application cible pour le client Hub."""
    errors = result.get("errors") or []
    success = bool(result.get("success", True)) and not errors
    return RestoreToAppResponse(
        success=success,
        message=result.get("message", "Restauration terminée"),
        app_slug=slug,
        backup_id=backup_id,
        items_restored=result.get("items_restored", 0),
        preferences_restored=result.get("preferences_restored", 0),
        files_restored=result.get("files_restored", 0),
        items_skipped=result.get("items_skipped", 0),
        files_skipped=result.get("files_skipped", 0),
        errors=errors,
        warnings=result.get("warnings", []),
        vaultwarden_snapshot_restored=result.get("vaultwarden_snapshot_restored", False),
        vaultwarden_restore=result.get("vaultwarden_restore"),
        secret_bundle_restored=result.get("secret_bundle_restored", False),
        secret_bundle_secret_names=result.get("secret_bundle_secret_names", []),
        accounts_reencrypted=result.get("accounts_reencrypted", 0),
        requires_vaultwarden_restart=result.get("requires_vaultwarden_restart", False),
    )


def restore_audit_identity(current_entity):
    """Retourne les identifiants audit user/service account selon l'appelant."""
    if isinstance(current_entity, User):
        return current_entity.id, None
    if isinstance(current_entity, ServiceAccount):
        return None, current_entity.id
    return None, None


async def check_backup_entity_access(
    slug: str,
    current_entity,
    db: AsyncSession,
) -> None:
    if isinstance(current_entity, User):
        if not current_entity.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acces administrateur requis pour les backups"
            )
        return
    elif isinstance(current_entity, ServiceAccount):
        sa_result = await db.execute(
            select(ServiceAccount)
            .options(selectinload(ServiceAccount.applications))
            .where(ServiceAccount.id == current_entity.id)
        )
        sa = sa_result.scalar_one_or_none()
        if not sa:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service account introuvable"
            )
        app_slugs = [app.slug for app in sa.applications]
        if slug not in app_slugs:
            logger.warning(
                "⛔ Service account '%s' (client_id=%s) "
                "a tente d'acceder aux backups de '%s' sans y etre autorise",
                sa.name, sa.client_id, slug
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Service account n'a pas acces a l'application '{slug}'"
            )
        return
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Type d'authentification non reconnu"
        )


async def log_restore_audit(
    db: AsyncSession,
    request: Request,
    current_entity,
    slug: str,
    backup_id: str,
    options: RestoreToAppRequest,
    backup: AppBackup | None,
    success: bool,
    response_status: int,
    message: str,
):
    """Trace une restauration sans inclure le contenu du ZIP ou des secrets."""
    user_id, service_account_id = restore_audit_identity(current_entity)
    action = "restore_vaultwarden_complete" if options.restore_vaultwarden else "restore_app_backup"
    details = {
        "app_slug": slug,
        "backup_id": backup_id,
        "success": success,
        "message": message,
        "clear_existing": options.clear_existing,
        "restore_items": options.restore_items,
        "restore_preferences": options.restore_preferences,
        "restore_files": options.restore_files,
        "restore_test_data": options.restore_test_data,
        "restore_vaultwarden": options.restore_vaultwarden,
        "restore_secret_bundle": options.restore_secret_bundle,
        "require_vaultwarden_offline": options.require_vaultwarden_offline,
        "confirm_full_restore": options.confirm_full_restore,
    }
    if backup is not None:
        details.update({
            "checksum": backup.checksum,
            "total_size_bytes": backup.total_size_bytes,
            "includes_vaultwarden_snapshot": getattr(backup, "includes_vaultwarden_snapshot", False),
            "includes_secret_bundle": getattr(backup, "includes_secret_bundle", False),
        })

    await log_action(
        db=db,
        user_id=user_id,
        service_account_id=service_account_id,
        action=action,
        target=slug,
        request=request,
        response_status=response_status,
        details=json.dumps(details, ensure_ascii=False),
    )


async def get_admin_or_app_service_account(
    slug: str,
    current_entity = Depends(get_current_entity)
) -> User | ServiceAccount:
    """
    Vérifie que l'appelant est soit un admin, soit un service account 
    associé à l'application spécifiée.
    
    Permet aux applications de déclencher leurs propres restaurations via M2M.
    """
    if isinstance(current_entity, User):
        if not current_entity.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès administrateur requis"
            )
        return current_entity
    elif isinstance(current_entity, ServiceAccount):
        # Vérifier que le SA a accès à cette application
        app_slugs = [app.slug for app in current_entity.applications]
        if slug not in app_slugs:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Service account n'a pas accès à l'application '{slug}'"
            )
        return current_entity
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Type d'authentification non reconnu"
        )


@router.post("/api/apps/{slug}/backup/{backup_id}/restore", response_model=RestoreToAppResponse)
async def restore_backup_to_app(
    slug: str,
    backup_id: str,
    request: Request,
    options: RestoreToAppRequest = RestoreToAppRequest(),
    db: AsyncSession = Depends(get_db),
    current_entity = Depends(get_current_entity)
):
    """
    Restaure un backup existant vers une application.
    
    Le HUB envoie le fichier de backup à l'application via POST /api/admin/restore.
    
    Authentification: 
    - Utilisateur admin du Hub
    - Service account associé à l'application (pour sync-from-hub M2M)
    
    Options:
    - **clear_existing**: Supprimer toutes les données existantes avant restauration
    - **restore_items**: Restaurer les items
    - **restore_preferences**: Restaurer les préférences utilisateur  
    - **restore_files**: Restaurer les fichiers uploadés
    - **restore_test_data**: Restaurer les données de test
    """
    import httpx
    from app.auth import create_access_token
    from datetime import timedelta
    
    # Vérifier les permissions (admin ou SA de l'app)
    if isinstance(current_entity, User):
        if not current_entity.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès administrateur requis"
            )
    elif isinstance(current_entity, ServiceAccount):
        # Vérifier que le SA a accès à cette application
        app_slugs = [app.slug for app in current_entity.applications]
        if slug not in app_slugs:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Service account n'a pas accès à l'application '{slug}'"
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Type d'authentification non reconnu"
        )
    
    app = await get_app_or_404(db, slug)
    backup = await get_backup_or_404(db, backup_id)
    
    if backup.app_slug != slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le backup ne correspond pas à l'application"
        )
    
    validate_vaultwarden_restore_options(options, backup)
    
    if not app.api_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'application n'a pas d'URL d'API configurée"
        )
    
    # Vérifier que le fichier existe
    file_path = get_backup_file_path_or_404(backup)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fichier de backup non trouvé sur le serveur"
        )
    
    # Obtenir un token M2M pour l'application
    sa_result = await db.execute(
        select(ServiceAccount)
        .join(ServiceAccount.applications)
        .where(Application.slug == slug)
        .limit(1)
    )
    sa = sa_result.scalar_one_or_none()
    
    if not sa:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucun service account trouvé pour cette application"
        )
    
    # Créer un token M2M avec rôle admin
    m2m_token, _, _ = create_access_token(
        username=sa.client_id,
        apps={slug: "admin"},
        expires_delta=timedelta(minutes=10),
        is_service_account=True
    )
    
    logger.info(f"🔄 Restauration backup {backup_id} vers {slug}")
    
    try:
        # Lire le fichier de backup
        backup_content = file_path.read_bytes()
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Envoyer le backup à l'application via multipart/form-data
            restore_url = f"{app.api_url.rstrip('/')}/api/admin/restore"
            
            files = {
                "file": (f"backup_{backup_id}.zip", backup_content, "application/zip")
            }
            data = build_restore_form_data(options)
            
            response = await client.post(
                restore_url,
                headers={"Authorization": f"Bearer {m2m_token}"},
                files=files,
                data=data
            )
            
            if response.status_code != 200:
                error_detail = response.text[:2000] if response.text else "Pas de détails"
                logger.error(f"❌ Erreur restauration: {response.status_code} - {error_detail}")
                await log_restore_audit(
                    db=db,
                    request=request,
                    current_entity=current_entity,
                    slug=slug,
                    backup_id=backup_id,
                    options=options,
                    backup=backup,
                    success=False,
                    response_status=response.status_code,
                    message=error_detail,
                )
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"L'application a retourné une erreur: {response.status_code} - {error_detail}"
                )
            
            result = response.json()
            restore_response = build_restore_response(result, slug, backup_id)
            
            logger.info(f"✅ Restauration terminée pour {slug}: {restore_response.items_restored} items")

            await log_restore_audit(
                db=db,
                request=request,
                current_entity=current_entity,
                slug=slug,
                backup_id=backup_id,
                options=options,
                backup=backup,
                success=restore_response.success,
                response_status=200 if restore_response.success else 502,
                message=restore_response.message,
            )
            await db.commit()

            return restore_response
            
    except httpx.RequestError as e:
        logger.error(f"❌ Erreur réseau lors de la restauration: {e}")
        await log_restore_audit(
            db=db,
            request=request,
            current_entity=current_entity,
            slug=slug,
            backup_id=backup_id,
            options=options,
            backup=backup,
            success=False,
            response_status=status.HTTP_502_BAD_GATEWAY,
            message=str(e),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de contacter l'application: {str(e)}"
        )


@router.post("/api/apps/{slug}/backup/restore/upload", response_model=RestoreToAppResponse)
async def restore_uploaded_backup_to_app(
    slug: str,
    request: Request,
    file: UploadFile = File(...),
    clear_existing: bool = Form(False),
    restore_items: bool = Form(True),
    restore_preferences: bool = Form(True),
    restore_files: bool = Form(True),
    restore_test_data: bool = Form(True),
    restore_vaultwarden: bool = Form(False),
    restore_secret_bundle: bool = Form(False),
    require_vaultwarden_offline: bool = Form(True),
    confirm_full_restore: bool = Form(False),
    password: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Restaure un backup uploadé vers une application.
    
    Permet d'importer un fichier de backup externe (depuis un autre environnement)
    et de le restaurer directement vers l'application.
    
    Le fichier peut être:
    - Un fichier .encrypted (exporté depuis le HUB, sera déchiffré automatiquement)
    - Un fichier .zip (backup brut non chiffré)
    """
    import httpx
    from app.auth import create_access_token
    from datetime import timedelta

    options = RestoreToAppRequest(
        clear_existing=clear_existing,
        restore_items=restore_items,
        restore_preferences=restore_preferences,
        restore_files=restore_files,
        restore_test_data=restore_test_data,
        restore_vaultwarden=restore_vaultwarden,
        restore_secret_bundle=restore_secret_bundle,
        require_vaultwarden_offline=require_vaultwarden_offline,
        confirm_full_restore=confirm_full_restore,
    )
    validate_vaultwarden_restore_options(options)
    backup_id_from_header = "uploaded"
    
    app = await get_app_or_404(db, slug)
    
    if not app.api_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'application n'a pas d'URL d'API configurée"
        )
    
    # Vérifier le type de fichier
    filename = file.filename or ""
    is_encrypted = filename.endswith('.encrypted')
    is_zip = filename.endswith('.zip')
    
    if not is_encrypted and not is_zip:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier doit être un .encrypted (exporté depuis le HUB) ou un .zip"
        )
    
    # Obtenir un token M2M pour l'application
    sa_result = await db.execute(
        select(ServiceAccount)
        .join(ServiceAccount.applications)
        .where(Application.slug == slug)
        .limit(1)
    )
    sa = sa_result.scalar_one_or_none()
    
    if not sa:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucun service account trouvé pour cette application"
        )
    
    # Créer un token M2M avec rôle admin
    m2m_token, _, _ = create_access_token(
        username=sa.client_id,
        apps={slug: "admin"},
        expires_delta=timedelta(minutes=10),
        is_service_account=True
    )
    
    logger.info(f"🔄 Restauration fichier uploadé vers {slug} par {current_user.username}")
    
    try:
        # Lire le fichier uploadé
        raw_content = await file.read()
        
        # Si c'est un fichier .encrypted, le déchiffrer
        if is_encrypted:
            logger.info(f"🔓 Déchiffrement du fichier .encrypted...")
            backup_content, header = decrypt_encrypted_package(raw_content, password=password)
            # Utiliser le nom du backup original si disponible
            backup_id_from_header = header.get("backup_id", "uploaded")
            output_filename = f"backup_{backup_id_from_header}.zip"
            logger.info(f"✅ Fichier déchiffré: {human_readable_size(len(backup_content))}")
        else:
            backup_content = raw_content
            output_filename = filename
            backup_id_from_header = "uploaded"
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            restore_url = f"{app.api_url.rstrip('/')}/api/admin/restore"
            
            files = {
                "file": (output_filename, backup_content, "application/zip")
            }
            data = build_restore_form_data(options)
            
            response = await client.post(
                restore_url,
                headers={"Authorization": f"Bearer {m2m_token}"},
                files=files,
                data=data
            )
            
            if response.status_code != 200:
                error_detail = response.text[:2000] if response.text else "Pas de détails"
                logger.error(f"❌ Erreur restauration upload: {response.status_code} - {error_detail}")
                await log_restore_audit(
                    db=db,
                    request=request,
                    current_entity=current_user,
                    slug=slug,
                    backup_id=backup_id_from_header,
                    options=options,
                    backup=None,
                    success=False,
                    response_status=response.status_code,
                    message=error_detail,
                )
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"L'application a retourné une erreur: {response.status_code} - {error_detail}"
                )
            
            result = response.json()
            restore_response = build_restore_response(result, slug, backup_id_from_header)
            
            logger.info(f"✅ Restauration upload terminée pour {slug}")

            await log_restore_audit(
                db=db,
                request=request,
                current_entity=current_user,
                slug=slug,
                backup_id=backup_id_from_header,
                options=options,
                backup=None,
                success=restore_response.success,
                response_status=200 if restore_response.success else 502,
                message=restore_response.message,
            )
            await db.commit()

            return restore_response
            
    except httpx.RequestError as e:
        logger.error(f"❌ Erreur réseau lors de la restauration: {e}")
        await log_restore_audit(
            db=db,
            request=request,
            current_entity=current_user,
            slug=slug,
            backup_id=backup_id_from_header,
            options=options,
            backup=None,
            success=False,
            response_status=status.HTTP_502_BAD_GATEWAY,
            message=str(e),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de contacter l'application: {str(e)}"
        )


@router.post("/api/apps/{slug}/backup/import", response_model=BackupImportResponse)
async def import_backup_file(
    slug: str,
    request: Request,
    file: UploadFile = File(...),
    password: Optional[str] = Form(None),
    store_backup: bool = Form(True),
    restore_now: bool = Form(False),
    clear_existing: bool = Form(False),
    restore_items: bool = Form(True),
    restore_preferences: bool = Form(True),
    restore_files: bool = Form(True),
    restore_test_data: bool = Form(True),
    restore_vaultwarden: bool = Form(False),
    restore_secret_bundle: bool = Form(False),
    require_vaultwarden_offline: bool = Form(True),
    confirm_full_restore: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Importe un backup externe (migration depuis un autre hub) :
    - .encrypted (v2 password ou v1 RSA) -> déchiffré avec `password` si nécessaire
    - .zip brut accepté également

    store_backup=True  : enregistre le ZIP déchiffré comme backup de l'app (visible dans la liste)
    restore_now=True   : restaure immédiatement le contenu vers l'application
    """
    import httpx
    from app.auth import create_access_token
    from datetime import timedelta

    options = RestoreToAppRequest(
        clear_existing=clear_existing,
        restore_items=restore_items,
        restore_preferences=restore_preferences,
        restore_files=restore_files,
        restore_test_data=restore_test_data,
        restore_vaultwarden=restore_vaultwarden,
        restore_secret_bundle=restore_secret_bundle,
        require_vaultwarden_offline=require_vaultwarden_offline,
        confirm_full_restore=confirm_full_restore,
    )
    validate_vaultwarden_restore_options(options)

    app = await get_app_or_404(db, slug)

    filename = file.filename or ""
    is_encrypted = filename.endswith('.encrypted')
    is_zip = filename.endswith('.zip')
    if not is_encrypted and not is_zip:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier doit être un .encrypted (exporté depuis un HUB) ou un .zip"
        )

    raw_content = await file.read()

    # Déchiffrement éventuel
    if is_encrypted:
        backup_content, header = decrypt_encrypted_package(raw_content, password=password)
        backup_id = header.get("backup_id") or str(uuid.uuid4())
        description = f"Importé depuis un autre hub (export du {header.get('encrypted_at', '?')})"
    else:
        backup_content = raw_content
        backup_id = str(uuid.uuid4())
        description = "Importé depuis un fichier zip externe"

    # Détection early d'un backup_id déjà présent (évite un 500 IntegrityError).
    # backup_id est globalement unique (contrainte DB) : pas de filtre sur app_slug.
    if store_backup:
        existing = await db.execute(
            select(AppBackup).where(AppBackup.backup_id == backup_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Un backup avec l'identifiant {backup_id} existe déjà pour cette application",
            )

    # Stockage comme backup de l'app
    stored_backup_id = None
    backup_row = None
    if store_backup:
        zip_metadata = extract_metadata_from_zip(backup_content)
        app_dir = ensure_storage_dir(slug)
        storage_filename = f"backup_{backup_id}.zip"
        storage_path = f"{slug}/{storage_filename}"
        (app_dir / storage_filename).write_bytes(backup_content)

        created_by = (
            current_user.username if not hasattr(current_user, "client_id") else current_user.name
        )
        backup = AppBackup(
            backup_id=backup_id,
            app_id=app.id,
            app_slug=slug,
            app_version=zip_metadata.get("app_version"),
            total_items=zip_metadata.get("total_items", 0),
            total_preferences=zip_metadata.get("total_preferences", 0),
            total_files=zip_metadata.get("total_files", 0),
            format_version=zip_metadata.get("format_version"),
            total_tables=zip_metadata.get("total_tables", 0),
            total_rows=zip_metadata.get("total_rows", 0),
            includes_vaultwarden_snapshot=bool(zip_metadata.get("includes_vaultwarden_snapshot")),
            includes_secret_bundle=bool(zip_metadata.get("includes_secret_bundle")),
            restore_requires_offline=bool(zip_metadata.get("restore_requires_offline")),
            total_size_bytes=len(backup_content),
            checksum=compute_checksum(backup_content),
            storage_path=storage_path,
            created_by=created_by,
            created_by_type="user",
            description=description,
            status="success",
            started_at=utcnow(),
            completed_at=utcnow(),
            duration_seconds=0,
            progress_step="done",
            progress_percent=100,
        )
        db.add(backup)
        app.last_backup_at = utcnow()
        app.last_backup_id = backup_id
        app.last_backup_status = "success"
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            (app_dir / storage_filename).unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Un backup avec l'identifiant {backup_id} existe déjà pour cette application",
            )
        await db.refresh(backup)
        stored_backup_id = backup_id
        backup_row = backup
        logger.info(f"📥 Backup importé et stocké pour {slug}: {backup_id} ({human_readable_size(len(backup_content))})")

    # Restauration immédiate vers l'application
    restore_response = None
    if restore_now:
        if not app.api_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="L'application n'a pas d'URL d'API configurée"
            )
        sa_result = await db.execute(
            select(ServiceAccount)
            .join(ServiceAccount.applications)
            .where(Application.slug == slug)
            .limit(1)
        )
        sa = sa_result.scalar_one_or_none()
        if not sa:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucun service account trouvé pour cette application"
            )

        m2m_token, _, _ = create_access_token(
            username=sa.client_id,
            apps={slug: "admin"},
            expires_delta=timedelta(minutes=10),
            is_service_account=True
        )

        output_filename = f"backup_{backup_id}.zip"
        try:
            async with httpx.AsyncClient(timeout=300.0) as http_client:
                restore_url = f"{app.api_url.rstrip('/')}/api/admin/restore"
                response = await http_client.post(
                    restore_url,
                    headers={"Authorization": f"Bearer {m2m_token}"},
                    files={"file": (output_filename, backup_content, "application/zip")},
                    data=build_restore_form_data(options),
                )
                if response.status_code != 200:
                    error_detail = response.text[:2000] if response.text else "Pas de détails"
                    logger.error(f"❌ Erreur restauration import: {response.status_code} - {error_detail}")
                    await log_restore_audit(
                        db=db,
                        request=request,
                        current_entity=current_user,
                        slug=slug,
                        backup_id=backup_id,
                        options=options,
                        backup=backup_row,
                        success=False,
                        response_status=response.status_code,
                        message=error_detail,
                    )
                    await db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"L'application a retourné une erreur: {response.status_code} - {error_detail}"
                    )
                result = response.json()
                restore_response = build_restore_response(result, slug, backup_id)

                logger.info(f"✅ Restauration après import terminée pour {slug}")

                await log_restore_audit(
                    db=db,
                    request=request,
                    current_entity=current_user,
                    slug=slug,
                    backup_id=backup_id,
                    options=options,
                    backup=backup_row,
                    success=restore_response.success,
                    response_status=200 if restore_response.success else 502,
                    message=restore_response.message,
                )
                await db.commit()
        except httpx.RequestError as e:
            logger.error(f"❌ Erreur réseau lors de la restauration: {e}")
            await log_restore_audit(
                db=db,
                request=request,
                current_entity=current_user,
                slug=slug,
                backup_id=backup_id,
                options=options,
                backup=backup_row,
                success=False,
                response_status=status.HTTP_502_BAD_GATEWAY,
                message=str(e),
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Impossible de contacter l'application: {str(e)}"
            )

    return BackupImportResponse(
        success=True,
        message=(
            f"Backup importé (stocké: {'oui' if stored_backup_id else 'non'}, "
            f"restauré: {'oui' if restore_response and restore_response.success else 'non'})"
        ),
        backup_stored=stored_backup_id is not None,
        backup_id=stored_backup_id,
        decrypted_size_bytes=len(backup_content),
        restore=restore_response,
    )


# =============================================================================
# VUE GLOBALE ADMIN
# =============================================================================

@router.get("/api/admin/backups", response_model=AllBackupsResponse)
async def get_all_backups_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Vue globale de tous les backups pour l'administration.
    
    Retourne le statut de sauvegarde de chaque application.
    """
    await ensure_hub_system_application(db)

    # Récupérer toutes les applications avec leurs stats de backup
    apps_result = await db.execute(
        select(Application)
        .options(selectinload(Application.backups))
        .order_by(Application.name)
    )
    apps = apps_result.scalars().all()

    app_statuses = []
    total_backups = 0
    total_size = 0

    for app in apps:
        backups = app.backups or []
        # Pour les compteurs/sizes globaux on ne compte que les backups réussis
        success_backups = [b for b in backups if (getattr(b, "status", "success") or "success") == "success"]
        backups_count = len(success_backups)
        app_total_size = sum(b.total_size_bytes or 0 for b in success_backups)

        total_backups += backups_count
        total_size += app_total_size

        # Backup en cours pour cette app ?
        running_backup = next(
            (b for b in backups if (b.status or "") in ("pending", "running")),
            None,
        )

        # Dernière durée + moyenne (5 derniers succès)
        recent_success = sorted(
            [b for b in success_backups if b.duration_seconds is not None],
            key=lambda b: b.created_at or utcnow(),
            reverse=True,
        )[:5]
        last_duration = recent_success[0].duration_seconds if recent_success else None
        avg_duration = (
            int(sum(b.duration_seconds for b in recent_success) / len(recent_success))
            if recent_success else None
        )

        app_statuses.append(AppBackupStatusResponse(
            app_id=app.id,
            app_slug=app.slug,
            app_name=app.name,
            is_system_app=bool(getattr(app, "is_system_app", False)),
            is_unmanaged=bool(getattr(app, "is_unmanaged", False)),
            backup_mode=_safe_backup_mode(app.backup_mode),
            backup_cron=app.backup_cron,
            backup_retention=app.backup_retention or 5,
            backup_quiesce=bool(getattr(app, "backup_quiesce", False)),
            last_backup_at=app.last_backup_at,
            last_backup_id=app.last_backup_id,
            last_backup_status=app.last_backup_status,
            last_backup_duration_seconds=last_duration,
            last_backup_duration_human=_format_duration_human_local(last_duration),
            avg_backup_duration_seconds=avg_duration,
            next_backup_at=app.next_backup_at,
            backups_count=backups_count,
            total_size_bytes=app_total_size,
            total_size_human=human_readable_size(app_total_size),
            has_running_backup=running_backup is not None,
            running_backup_id=running_backup.backup_id if running_backup else None,
        ))

    return AllBackupsResponse(
        apps=app_statuses,
        total_apps=len(apps),
        total_backups=total_backups,
        total_size_bytes=total_size,
        total_size_human=human_readable_size(total_size)
    )


def _format_duration_human_local(seconds: Optional[int]) -> str:
    """Helper local équivalent à schemas._format_duration_human (évite import circulaire)."""
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


@router.get("/api/admin/backups/in-progress", response_model=InProgressBackupsResponse)
async def get_in_progress_backups(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Liste les backups actuellement en cours d'exécution sur le Hub."""
    result = await db.execute(
        select(AppBackup)
        .where(AppBackup.status.in_(["pending", "running"]))
        .order_by(AppBackup.started_at.asc())
    )
    running = result.scalars().all()

    # Cache des noms d'app + moyennes de durée par slug
    items: list[InProgressBackupItem] = []
    for backup in running:
        # Nom de l'app
        app_name = backup.app_slug
        if backup.app_id:
            app_res = await db.execute(
                select(Application.name).where(Application.id == backup.app_id)
            )
            row = app_res.scalar_one_or_none()
            if row:
                app_name = row

        # Moyenne durée des 5 derniers succès
        avg_res = await db.execute(
            select(AppBackup.duration_seconds)
            .where(AppBackup.app_slug == backup.app_slug)
            .where(AppBackup.status == "success")
            .where(AppBackup.duration_seconds.isnot(None))
            .order_by(AppBackup.created_at.desc())
            .limit(5)
        )
        durations = [d for d in avg_res.scalars().all() if d is not None]
        avg_duration = int(sum(durations) / len(durations)) if durations else None

        elapsed = 0
        if backup.started_at:
            started = _naive_utc(backup.started_at)
            elapsed = max(0, int((utcnow() - started).total_seconds())) if started else 0

        items.append(InProgressBackupItem(
            backup_id=backup.backup_id,
            app_slug=backup.app_slug,
            app_name=app_name,
            status=backup.status or "running",
            progress_step=backup.progress_step or "queued",
            progress_percent=int(backup.progress_percent or 0),
            started_at=backup.started_at,
            elapsed_seconds=elapsed,
            avg_duration_seconds=avg_duration,
            avg_duration_human=_format_duration_human_local(avg_duration),
            created_by=backup.created_by,
            created_by_type=backup.created_by_type,
        ))

    return InProgressBackupsResponse(items=items, total=len(items))


@router.post("/api/admin/backups/{backup_id}/cancel")
async def cancel_running_backup(
    backup_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Kill-switch manuel : annule un backup bloqué en `pending` ou `running`.

    Marque la ligne en `failed` avec un message explicite, libère le verrou
    (qui empêchait un nouveau déclenchement via le 409) et diffuse un
    événement SSE `backup_failed` pour mettre à jour l'UI temps réel.

    Note : la tâche `asyncio` en arrière-plan n'est pas interrompue de force
    (Python n'offre pas d'annulation propre depuis l'extérieur), mais quand
    elle finira par revenir, elle écrira sur une ligne déjà marquée comme
    annulée — le statut "cancelled" sera donc préservé tant que la tâche
    n'a pas commit après le cancel. En pratique, ça libère immédiatement
    l'utilisateur côté UI.
    """
    result = await db.execute(
        select(AppBackup).where(AppBackup.backup_id == backup_id)
    )
    backup = result.scalar_one_or_none()
    if backup is None:
        raise HTTPException(status_code=404, detail="Backup introuvable")

    if backup.status not in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"Backup déjà terminé (statut: {backup.status})",
        )

    backup.status = "failed"
    backup.completed_at = utcnow()
    backup.error_message = (
        f"Annulé manuellement par {current_user.username} "
        f"(étape: {backup.progress_step or 'inconnue'})"
    )
    started = _naive_utc(backup.started_at)
    completed = _naive_utc(backup.completed_at)
    if started and completed:
        backup.duration_seconds = int((completed - started).total_seconds())

    # Réaligner l'application si besoin
    if backup.app_id:
        app_res = await db.execute(
            select(Application).where(Application.id == backup.app_id)
        )
        target_app = app_res.scalar_one_or_none()
        if target_app and target_app.last_backup_status == "running":
            target_app.last_backup_status = "failed"

    await db.commit()

    await log_action(
        db,
        user_id=current_user.id,
        action="cancel_backup",
        target=f"app_backup:{backup.backup_id}",
        details=(
            f"Annulation manuelle du backup {backup.backup_id} pour l'app "
            f"{backup.app_slug} (étape: {backup.progress_step or 'inconnue'})"
        ),
    )

    await _emit_backup_event("backup_failed", {
        "backup_id": backup.backup_id,
        "app_slug": backup.app_slug,
        "status": "failed",
        "error_message": backup.error_message,
        "duration_seconds": backup.duration_seconds,
        "cancelled": True,
    })

    return {
        "success": True,
        "message": f"Sauvegarde {backup_id} annulée",
        "backup_id": backup.backup_id,
        "duration_seconds": backup.duration_seconds,
    }


@router.get("/api/admin/backups/stats")
async def get_backup_storage_stats(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_admin_or_admin_sa)
):
    """
    Statistiques de stockage des sauvegardes.
    """
    # Stats globales depuis la base
    result = await db.execute(
        select(
            func.count(AppBackup.id).label("total_backups"),
            func.sum(AppBackup.total_size_bytes).label("total_size"),
            func.count(func.distinct(AppBackup.app_slug)).label("apps_with_backups")
        )
    )
    row = result.one()
    
    # Taille réelle sur disque
    disk_size = 0
    if STORAGE_BASE_DIR.exists():
        for f in STORAGE_BASE_DIR.rglob("*"):
            if f.is_file():
                disk_size += f.stat().st_size
    
    return {
        "total_backups": row.total_backups or 0,
        "total_size_bytes": row.total_size or 0,
        "total_size_human": human_readable_size(row.total_size or 0),
        "apps_with_backups": row.apps_with_backups or 0,
        "disk_size_bytes": disk_size,
        "disk_size_human": human_readable_size(disk_size),
        "storage_path": str(STORAGE_BASE_DIR)
    }


@router.get("/api/admin/backups/orphans")
async def get_orphan_backups(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Liste les backups orphelins (sans application associée).
    
    Ces backups appartiennent à des applications qui ont été supprimées.
    Ils peuvent être réassociés automatiquement si une application avec
    le même slug est recréée.
    """
    # Backups sans app_id
    result = await db.execute(
        select(AppBackup)
        .where(AppBackup.app_id.is_(None))
        .order_by(AppBackup.app_slug, AppBackup.created_at.desc())
    )
    orphan_backups = result.scalars().all()
    
    # Grouper par app_slug
    by_slug = {}
    for backup in orphan_backups:
        if backup.app_slug not in by_slug:
            by_slug[backup.app_slug] = {
                "app_slug": backup.app_slug,
                "backups": [],
                "total_size_bytes": 0
            }
        by_slug[backup.app_slug]["backups"].append({
            "backup_id": backup.backup_id,
            "created_at": backup.created_at.isoformat() if backup.created_at else None,
            "total_size_bytes": backup.total_size_bytes or 0,
            "total_size_human": human_readable_size(backup.total_size_bytes or 0),
            "created_by": backup.created_by,
            "description": backup.description
        })
        by_slug[backup.app_slug]["total_size_bytes"] += backup.total_size_bytes or 0
    
    # Ajouter la taille lisible et le count
    orphan_groups = []
    for slug, data in by_slug.items():
        data["backups_count"] = len(data["backups"])
        data["total_size_human"] = human_readable_size(data["total_size_bytes"])
        orphan_groups.append(data)
    
    return {
        "orphan_groups": orphan_groups,
        "total_orphan_backups": len(orphan_backups),
        "total_slugs": len(by_slug),
        "message": "Ces backups seront automatiquement réassociés si une application avec le même slug est créée."
    }


@router.delete("/api/admin/backups/orphans/{app_slug}")
async def delete_orphan_backups_by_slug(
    app_slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Supprime tous les backups orphelins pour un slug donné.
    
    Utile pour nettoyer les backups d'applications qui ne seront plus recréées.
    """
    # Trouver les backups orphelins pour ce slug
    result = await db.execute(
        select(AppBackup).where(
            AppBackup.app_slug == app_slug,
            AppBackup.app_id.is_(None)
        )
    )
    orphan_backups = result.scalars().all()
    
    if not orphan_backups:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aucun backup orphelin trouvé pour le slug '{app_slug}'"
        )
    
    deleted_count = 0
    total_size_freed = 0
    
    for backup in orphan_backups:
        # Supprimer les fichiers physiques
        total_size_freed += delete_backup_file_if_present(backup.storage_path)
        delete_backup_file_if_present(backup.encrypted_path)
        
        await db.delete(backup)
        deleted_count += 1
    
    # Supprimer le dossier de l'app s'il est vide
    app_dir = STORAGE_BASE_DIR / app_slug
    if app_dir.exists() and not any(app_dir.iterdir()):
        app_dir.rmdir()
    
    await db.commit()
    
    logger.info(f"🗑️ {deleted_count} backup(s) orphelin(s) supprimé(s) pour '{app_slug}' ({human_readable_size(total_size_freed)} libérés)")
    
    return {
        "message": f"{deleted_count} backup(s) orphelin(s) supprimé(s) pour '{app_slug}'",
        "deleted_count": deleted_count,
        "size_freed_bytes": total_size_freed,
        "size_freed_human": human_readable_size(total_size_freed)
    }


@router.get("/api/apps/{slug}/backup/orphans")
async def get_orphan_backups_for_slug(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_entity)
):
    """
    Liste les backups disponibles pour un slug, même si l'application n'existe pas encore.
    
    Utile pour une application qui veut savoir si des sauvegardes existent
    avant de s'enregistrer sur le HUB.
    """
    await check_backup_entity_access(slug, current_user, db)

    # Vérifier si l'application existe
    result = await db.execute(
        select(Application).where(Application.slug == slug)
    )
    app = result.scalar_one_or_none()
    
    # Récupérer tous les backups pour ce slug
    backups_result = await db.execute(
        select(AppBackup)
        .where(AppBackup.app_slug == slug)
        .order_by(AppBackup.created_at.desc())
    )
    backups = backups_result.scalars().all()
    
    return {
        "app_slug": slug,
        "app_exists": app is not None,
        "app_name": app.name if app else None,
        "backups": [
            {
                "backup_id": b.backup_id,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "total_size_bytes": b.total_size_bytes or 0,
                "total_size_human": human_readable_size(b.total_size_bytes or 0),
                "is_orphan": b.app_id is None,
                "app_version": b.app_version,
                "description": b.description
            }
            for b in backups
        ],
        "total_backups": len(backups),
        "orphan_backups": sum(1 for b in backups if b.app_id is None)
    }
