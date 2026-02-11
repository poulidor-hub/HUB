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
- GET  /api/apps/{slug}/backup/{id}/export : Exporter un backup chiffré
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
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Request
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.database import get_db
from app.models import Application, AppBackup, ServiceAccount, User
from app.auth import get_current_admin, get_current_entity
from app.config import get_settings
from app.schemas import (
    AppBackupConfig, AppBackupConfigUpdate, AppBackupReceive,
    AppBackupResponse, AppBackupListResponse, AppBackupStatusResponse,
    AllBackupsResponse, BackupTriggerResponse, BackupExportResponse,
    BackupModeEnum, RestoreToAppRequest, RestoreToAppResponse
)

logger = logging.getLogger(__name__)
settings = get_settings()

# Configuration du stockage
STORAGE_BASE_DIR = Path("/code/storage/backups")
SECRETS_DIR = Path("/code/secrets")
JWT_PRIVATE_KEY_PATH = SECRETS_DIR / "jwt_private_key.pem"

router = APIRouter(tags=["App Backups"])


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
        "total_items": 0,
        "total_preferences": 0,
        "total_files": 0,
        "content_summary": ""
    }
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zf:
            # Chercher data.json ou manifest.json
            data_file = None
            for name in ['data.json', 'manifest.json', 'backup.json']:
                if name in zf.namelist():
                    data_file = name
                    break
            
            if data_file:
                with zf.open(data_file) as f:
                    data = json.load(f)
                    
                    # Extraire la version depuis metadata
                    metadata = data.get("metadata", {})
                    result["app_version"] = metadata.get("app_version") or metadata.get("version")
                    
                    # Extraire les compteurs depuis data
                    backup_data = data.get("data", {})
                    
                    # Compter les items
                    items = backup_data.get("items", [])
                    result["total_items"] = len(items)
                    
                    # Compter les préférences
                    prefs = backup_data.get("user_preferences", []) or backup_data.get("preferences", [])
                    result["total_preferences"] = len(prefs)
                    
                    # Compter les fichiers uploadés
                    files = backup_data.get("uploaded_files", []) or backup_data.get("files", [])
                    result["total_files"] = len(files)
                    
                    # Construire un résumé du contenu
                    summary_parts = []
                    
                    # Parcourir toutes les clés de data pour un résumé complet
                    for key, value in backup_data.items():
                        if isinstance(value, list) and len(value) > 0:
                            # Formater le nom de la clé pour l'affichage
                            display_name = key.replace("_", " ").title()
                            summary_parts.append(f"{len(value)} {display_name}")
                    
                    if summary_parts:
                        result["content_summary"] = ", ".join(summary_parts)
                    else:
                        result["content_summary"] = "Backup vide"
            else:
                # Pas de fichier de données trouvé, lister les fichiers du ZIP
                file_list = zf.namelist()
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
    3. Chiffre la clé AES avec la clé RSA privée (pour signature/authenticité)
    
    Returns:
        tuple: (encrypted_data, encrypted_key, nonce)
    """
    # Charger la clé privée RSA
    if not JWT_PRIVATE_KEY_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Clé de chiffrement non trouvée"
        )
    
    private_key_pem = JWT_PRIVATE_KEY_PATH.read_bytes()
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    
    # Générer une clé AES-256 aléatoire (32 bytes)
    aes_key = secrets.token_bytes(32)
    
    # Générer un nonce pour AES-GCM (12 bytes recommandés)
    nonce = secrets.token_bytes(12)
    
    # Chiffrer les données avec AES-256-GCM
    aesgcm = AESGCM(aes_key)
    encrypted_data = aesgcm.encrypt(nonce, data, None)
    
    # Chiffrer la clé AES avec RSA (OAEP padding)
    # Note: On utilise la clé publique extraite de la privée pour le chiffrement
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
        "version": "1.0",
        "algorithm": "RSA-OAEP-256+AES-256-GCM",
        "backup_id": backup_id,
        "app_slug": app_slug,
        "encrypted_at": datetime.utcnow().isoformat(),
        "encrypted_key_b64": base64.b64encode(encrypted_key).decode(),
        "nonce_b64": base64.b64encode(nonce).decode(),
        "original_checksum": compute_checksum(backup_data)
    }
    
    header_json = json.dumps(header).encode()
    header_length = len(header_json).to_bytes(4, byteorder='big')
    
    # Assembler le package
    return header_length + header_json + encrypted_data


def decrypt_encrypted_package(encrypted_package: bytes) -> tuple[bytes, dict]:
    """
    Déchiffre un package .encrypted créé par create_encrypted_package.
    
    Args:
        encrypted_package: Contenu du fichier .encrypted
        
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
        
        # 4. Charger la clé privée RSA
        if not JWT_PRIVATE_KEY_PATH.exists():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Clé de déchiffrement non trouvée"
            )
        
        private_key_pem = JWT_PRIVATE_KEY_PATH.read_bytes()
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        
        # 5. Décoder la clé AES chiffrée et le nonce depuis base64
        encrypted_key = base64.b64decode(header["encrypted_key_b64"])
        nonce = base64.b64decode(header["nonce_b64"])
        
        # 6. Déchiffrer la clé AES avec RSA
        aes_key = private_key.decrypt(
            encrypted_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        # 7. Déchiffrer les données avec AES-GCM
        aesgcm = AESGCM(aes_key)
        decrypted_data = aesgcm.decrypt(nonce, encrypted_data, None)
        
        # 8. Vérifier le checksum si présent
        if "original_checksum" in header:
            computed_checksum = compute_checksum(decrypted_data)
            if computed_checksum != header["original_checksum"]:
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


async def get_app_or_404(db: AsyncSession, slug: str) -> Application:
    """Récupère une application par son slug ou lève une 404"""
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
    """Applique la politique de rétention en supprimant les anciens backups"""
    if retention <= 0:
        return  # 0 = illimité
    
    # Compter les backups existants
    count_result = await db.execute(
        select(func.count(AppBackup.id)).where(AppBackup.app_slug == app_slug)
    )
    count = count_result.scalar() or 0
    
    if count <= retention:
        return
    
    # Supprimer les plus anciens
    to_delete = count - retention
    oldest_result = await db.execute(
        select(AppBackup)
        .where(AppBackup.app_slug == app_slug)
        .order_by(AppBackup.created_at.asc())
        .limit(to_delete)
    )
    oldest_backups = oldest_result.scalars().all()
    
    for backup in oldest_backups:
        # Supprimer les fichiers physiques
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
    backup_id: Optional[str] = None,
    app_version: Optional[str] = None,
    total_items: int = 0,
    total_preferences: int = 0,
    total_files: int = 0,
    checksum: Optional[str] = None,
    description: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_entity)
):
    """
    Reçoit un backup poussé par une application (sync-to-hub).
    
    L'application envoie son fichier ZIP de backup avec les métadonnées.
    Le HUB stocke le fichier et enregistre les métadonnées en base.
    
    Authentification: Token M2M (service account) ou utilisateur admin.
    """
    # Vérifier que l'application existe
    app = await get_app_or_404(db, slug)
    
    # Déterminer qui crée le backup
    if hasattr(current_user, 'client_id'):
        created_by = current_user.name
        created_by_type = "app"
    else:
        created_by = current_user.username
        created_by_type = "user"
    
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
        total_size_bytes=file_size,
        checksum=computed_checksum,
        storage_path=storage_path,
        created_by=created_by,
        created_by_type=created_by_type,
        description=final_description
    )
    
    db.add(backup)
    
    # Mettre à jour l'application
    app.last_backup_at = datetime.utcnow()
    app.last_backup_id = backup_id
    app.last_backup_status = "success"
    
    await db.commit()
    await db.refresh(backup)
    
    # Appliquer la politique de rétention
    await apply_retention_policy(db, slug, app.backup_retention)
    
    logger.info(f"📦 Backup reçu pour {slug}: {backup_id} ({human_readable_size(file_size)})")
    
    return AppBackupResponse.from_orm_model(backup)


@router.get("/api/apps/{slug}/backup", response_model=AppBackupListResponse)
async def list_app_backups(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_entity)
):
    """
    Liste tous les backups d'une application.
    """
    app = await get_app_or_404(db, slug)
    
    result = await db.execute(
        select(AppBackup)
        .where(AppBackup.app_slug == slug)
        .order_by(AppBackup.created_at.desc())
    )
    backups = result.scalars().all()
    
    backup_config = AppBackupConfig(
        backup_mode=BackupModeEnum(app.backup_mode or "manual"),
        backup_cron=app.backup_cron,
        backup_retention=app.backup_retention or 5
    )
    
    return AppBackupListResponse(
        app_slug=slug,
        app_name=app.name,
        backups=[AppBackupResponse.from_orm_model(b) for b in backups],
        total=len(backups),
        backup_config=backup_config
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
    - GET /api/apps/{slug}/backup/{backup_id}/export : pour créer une version chiffrée
    - GET /api/apps/{slug}/backup/{backup_id}/export/download : pour télécharger la version chiffrée
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Téléchargement des backups non chiffrés désactivé. Utilisez /export pour obtenir une version chiffrée."
    )


@router.get("/api/apps/{slug}/backup/{backup_id}/export", response_model=BackupExportResponse)
async def export_encrypted_backup(
    slug: str,
    backup_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Exporte un backup chiffré avec la clé RSA du HUB.
    
    Le fichier exporté est chiffré avec un schéma hybride:
    - Les données sont chiffrées avec AES-256-GCM
    - La clé AES est chiffrée avec la clé RSA publique du HUB
    
    Le fichier ne peut être déchiffré qu'avec la clé privée RSA du HUB.
    """
    backup = await get_backup_or_404(db, backup_id)
    
    if backup.app_slug != slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le backup ne correspond pas à l'application"
        )
    
    # Lire le fichier original
    file_path = STORAGE_BASE_DIR / backup.storage_path
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fichier de backup non trouvé"
        )
    
    original_data = file_path.read_bytes()
    
    # Chiffrer
    try:
        encrypted_package = create_encrypted_package(original_data, backup_id, slug)
    except Exception as e:
        logger.error(f"Erreur de chiffrement: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors du chiffrement: {str(e)}"
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
    storage_path = STORAGE_BASE_DIR / backup.storage_path
    if storage_path.exists():
        storage_path.unlink()
    
    if backup.encrypted_path:
        encrypted_path = STORAGE_BASE_DIR / backup.encrypted_path
        if encrypted_path.exists():
            encrypted_path.unlink()
    
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
    current_user: User = Depends(get_current_admin)
):
    """
    Récupère la configuration de sauvegarde d'une application.
    """
    app = await get_app_or_404(db, slug)
    
    return AppBackupConfig(
        backup_mode=BackupModeEnum(app.backup_mode or "manual"),
        backup_cron=app.backup_cron,
        backup_retention=app.backup_retention or 5
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
                cron = croniter(config.backup_cron, datetime.utcnow())
                app.next_backup_at = cron.get_next(datetime)
        else:
            app.backup_cron = None
            app.next_backup_at = None
    
    if config.backup_retention is not None:
        app.backup_retention = config.backup_retention
        # Appliquer immédiatement la nouvelle rétention
        await apply_retention_policy(db, slug, config.backup_retention)
    
    await db.commit()
    await db.refresh(app)
    
    logger.info(f"⚙️ Configuration backup mise à jour pour {slug}: mode={app.backup_mode}, cron={app.backup_cron}")
    
    return AppBackupConfig(
        backup_mode=BackupModeEnum(app.backup_mode or "manual"),
        backup_cron=app.backup_cron,
        backup_retention=app.backup_retention or 5
    )


@router.post("/api/apps/{slug}/backup/trigger", response_model=BackupTriggerResponse)
async def trigger_backup(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Déclenche manuellement un backup pour une application.
    
    Le HUB appelle l'endpoint POST /api/admin/backup de l'application
    puis récupère le résultat via GET /api/admin/backup/download
    et stocke le fichier localement.
    """
    import httpx
    from app.auth import create_access_token
    from datetime import timedelta
    
    app = await get_app_or_404(db, slug)
    
    if app.backup_mode == "disabled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Les sauvegardes sont désactivées pour cette application"
        )
    
    if not app.api_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'application n'a pas d'URL d'API configurée"
        )
    
    # Marquer comme en cours
    app.last_backup_status = "running"
    await db.commit()
    
    try:
        # Obtenir un token M2M pour l'application
        # On utilise le service account de l'app si disponible
        sa_result = await db.execute(
            select(ServiceAccount)
            .join(ServiceAccount.applications)
            .where(Application.slug == slug)
            .limit(1)
        )
        sa = sa_result.scalar_one_or_none()
        
        if not sa:
            app.last_backup_status = "failed"
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucun service account trouvé pour cette application. Créez-en un avec accès à l'app."
            )
        
        # Créer un token M2M avec rôle admin pour l'application cible
        m2m_token, _, _ = create_access_token(
            username=sa.client_id,
            roles=["service_account"],
            apps={slug: "admin"},  # Rôle admin pour pouvoir déclencher le backup
            expires_delta=timedelta(minutes=10),
            is_service_account=True
        )
        
        logger.info(f"🔄 Déclenchement backup pour {slug} via {app.api_url}")
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            # 1. Appeler POST /api/admin/backup pour créer le backup
            create_url = f"{app.api_url.rstrip('/')}/api/admin/backup"
            logger.debug(f"📡 POST {create_url}")
            
            create_response = await client.post(
                create_url,
                headers={"Authorization": f"Bearer {m2m_token}"}
            )
            
            if create_response.status_code != 200:
                error_detail = create_response.text[:200] if create_response.text else "Pas de détails"
                logger.error(f"❌ Erreur création backup: {create_response.status_code} - {error_detail}")
                app.last_backup_status = "failed"
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"L'application a retourné une erreur: {create_response.status_code}"
                )
            
            backup_info = create_response.json()
            # Le backup_id peut être à la racine ou dans metadata selon l'implémentation de l'app
            remote_backup_id = backup_info.get("backup_id") or backup_info.get("metadata", {}).get("backup_id")
            logger.info(f"✅ Backup créé sur l'app: {remote_backup_id}")
            
            # 2. Télécharger le fichier ZIP
            download_url = f"{app.api_url.rstrip('/')}/api/admin/backup/download?backup_id={remote_backup_id}"
            logger.debug(f"📡 GET {download_url}")
            
            download_response = await client.get(
                download_url,
                headers={"Authorization": f"Bearer {m2m_token}"}
            )
            
            if download_response.status_code != 200:
                logger.error(f"❌ Erreur téléchargement backup: {download_response.status_code}")
                app.last_backup_status = "failed"
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Impossible de télécharger le backup: {download_response.status_code}"
                )
            
            zip_content = download_response.content
            file_size = len(zip_content)
            logger.info(f"📥 Backup téléchargé: {human_readable_size(file_size)}")
        
        # 3. Stocker le backup localement (comme receive_backup)
        backup_id = remote_backup_id or str(uuid.uuid4())
        checksum = compute_checksum(zip_content)
        
        # Créer le dossier de stockage
        app_dir = ensure_storage_dir(slug)
        
        # Nom du fichier
        filename = f"backup_{backup_id}.zip"
        storage_path = f"{slug}/{filename}"
        full_path = app_dir / filename
        
        # Sauvegarder le fichier
        with open(full_path, "wb") as f:
            f.write(zip_content)
        
        # Extraire les métadonnées du ZIP
        zip_metadata = extract_metadata_from_zip(zip_content)
        
        # Extraire les métadonnées - priorité: backup_info API > ZIP > défaut
        app_version = backup_info.get("app_version") or zip_metadata.get("app_version")
        total_items = backup_info.get("total_items") if backup_info.get("total_items") is not None else zip_metadata.get("total_items", 0)
        total_preferences = backup_info.get("total_preferences") if backup_info.get("total_preferences") is not None else zip_metadata.get("total_preferences", 0)
        total_files = backup_info.get("total_files") if backup_info.get("total_files") is not None else zip_metadata.get("total_files", 0)
        content_summary = zip_metadata.get("content_summary", "")
        
        # Créer l'enregistrement en base
        backup = AppBackup(
            backup_id=backup_id,
            app_id=app.id,
            app_slug=slug,
            app_version=app_version,
            total_items=total_items,
            total_preferences=total_preferences,
            total_files=total_files,
            total_size_bytes=file_size,
            checksum=checksum,
            storage_path=storage_path,
            created_by=current_user.username,
            created_by_type="hub",  # Indique que c'est le HUB qui a déclenché
            description=content_summary or f"Backup déclenché manuellement depuis le HUB par {current_user.username}"
        )
        
        db.add(backup)
        
        # Mettre à jour l'application
        app.last_backup_at = datetime.utcnow()
        app.last_backup_id = backup_id
        app.last_backup_status = "success"
        
        await db.commit()
        await db.refresh(backup)
        
        # Appliquer la politique de rétention
        await apply_retention_policy(db, slug, app.backup_retention)
        
        logger.info(f"✅ Backup {backup_id} stocké pour {slug} ({human_readable_size(file_size)})")
        
        return BackupTriggerResponse(
            success=True,
            message=f"Backup créé avec succès ({human_readable_size(file_size)})",
            backup_id=backup_id,
            app_slug=slug
        )
        
    except HTTPException:
        raise
    except httpx.RequestError as e:
        app.last_backup_status = "failed"
        await db.commit()
        logger.error(f"❌ Erreur réseau lors du backup pour {slug}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de contacter l'application: {str(e)}"
        )
    except Exception as e:
        app.last_backup_status = "failed"
        await db.commit()
        logger.error(f"❌ Erreur lors du déclenchement du backup pour {slug}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# =============================================================================
# RESTAURATION VERS UNE APPLICATION
# =============================================================================

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
    
    if not app.api_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'application n'a pas d'URL d'API configurée"
        )
    
    # Vérifier que le fichier existe
    file_path = STORAGE_BASE_DIR / backup.storage_path
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
        roles=["service_account"],
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
            data = {
                "clear_existing": str(options.clear_existing).lower(),
                "restore_items": str(options.restore_items).lower(),
                "restore_preferences": str(options.restore_preferences).lower(),
                "restore_files": str(options.restore_files).lower(),
                "restore_test_data": str(options.restore_test_data).lower()
            }
            
            response = await client.post(
                restore_url,
                headers={"Authorization": f"Bearer {m2m_token}"},
                files=files,
                data=data
            )
            
            if response.status_code != 200:
                error_detail = response.text[:200] if response.text else "Pas de détails"
                logger.error(f"❌ Erreur restauration: {response.status_code} - {error_detail}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"L'application a retourné une erreur: {response.status_code} - {error_detail}"
                )
            
            result = response.json()
            
            logger.info(f"✅ Restauration terminée pour {slug}: {result.get('items_restored', 0)} items")
            
            return RestoreToAppResponse(
                success=result.get("success", True),
                message=result.get("message", "Restauration terminée"),
                app_slug=slug,
                backup_id=backup_id,
                items_restored=result.get("items_restored", 0),
                preferences_restored=result.get("preferences_restored", 0),
                files_restored=result.get("files_restored", 0),
                items_skipped=result.get("items_skipped", 0),
                files_skipped=result.get("files_skipped", 0),
                errors=result.get("errors", []),
                warnings=result.get("warnings", [])
            )
            
    except httpx.RequestError as e:
        logger.error(f"❌ Erreur réseau lors de la restauration: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de contacter l'application: {str(e)}"
        )


@router.post("/api/apps/{slug}/backup/restore/upload", response_model=RestoreToAppResponse)
async def restore_uploaded_backup_to_app(
    slug: str,
    file: UploadFile = File(...),
    clear_existing: bool = False,
    restore_items: bool = True,
    restore_preferences: bool = True,
    restore_files: bool = True,
    restore_test_data: bool = True,
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
        roles=["service_account"],
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
            backup_content, header = decrypt_encrypted_package(raw_content)
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
            data = {
                "clear_existing": str(clear_existing).lower(),
                "restore_items": str(restore_items).lower(),
                "restore_preferences": str(restore_preferences).lower(),
                "restore_files": str(restore_files).lower(),
                "restore_test_data": str(restore_test_data).lower()
            }
            
            response = await client.post(
                restore_url,
                headers={"Authorization": f"Bearer {m2m_token}"},
                files=files,
                data=data
            )
            
            if response.status_code != 200:
                error_detail = response.text[:200] if response.text else "Pas de détails"
                logger.error(f"❌ Erreur restauration upload: {response.status_code} - {error_detail}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"L'application a retourné une erreur: {response.status_code} - {error_detail}"
                )
            
            result = response.json()
            
            logger.info(f"✅ Restauration upload terminée pour {slug}")
            
            return RestoreToAppResponse(
                success=result.get("success", True),
                message=result.get("message", "Restauration terminée"),
                app_slug=slug,
                backup_id=backup_id_from_header,
                items_restored=result.get("items_restored", 0),
                preferences_restored=result.get("preferences_restored", 0),
                files_restored=result.get("files_restored", 0),
                items_skipped=result.get("items_skipped", 0),
                files_skipped=result.get("files_skipped", 0),
                errors=result.get("errors", []),
                warnings=result.get("warnings", [])
            )
            
    except httpx.RequestError as e:
        logger.error(f"❌ Erreur réseau lors de la restauration: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de contacter l'application: {str(e)}"
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
        backups_count = len(backups)
        app_total_size = sum(b.total_size_bytes or 0 for b in backups)
        
        total_backups += backups_count
        total_size += app_total_size
        
        app_statuses.append(AppBackupStatusResponse(
            app_slug=app.slug,
            app_name=app.name,
            backup_mode=BackupModeEnum(app.backup_mode or "manual"),
            backup_cron=app.backup_cron,
            backup_retention=app.backup_retention or 5,
            last_backup_at=app.last_backup_at,
            last_backup_id=app.last_backup_id,
            last_backup_status=app.last_backup_status,
            next_backup_at=app.next_backup_at,
            backups_count=backups_count,
            total_size_bytes=app_total_size,
            total_size_human=human_readable_size(app_total_size)
        ))
    
    return AllBackupsResponse(
        apps=app_statuses,
        total_apps=len(apps),
        total_backups=total_backups,
        total_size_bytes=total_size,
        total_size_human=human_readable_size(total_size)
    )


@router.get("/api/admin/backups/stats")
async def get_backup_storage_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
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
        storage_path = STORAGE_BASE_DIR / backup.storage_path
        if storage_path.exists():
            total_size_freed += storage_path.stat().st_size
            storage_path.unlink()
        
        if backup.encrypted_path:
            encrypted_path = STORAGE_BASE_DIR / backup.encrypted_path
            if encrypted_path.exists():
                encrypted_path.unlink()
        
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
