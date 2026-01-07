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
from pathlib import Path
from datetime import datetime
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
    BackupModeEnum
)

logger = logging.getLogger(__name__)
settings = get_settings()

# Configuration du stockage
STORAGE_BASE_DIR = Path("/code/storage/backups")
SECRETS_DIR = Path("/code/secrets")
JWT_PRIVATE_KEY_PATH = SECRETS_DIR / "jwt_private_key.pem"

router = APIRouter(tags=["App Backups"])


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
        app_version=app_version,
        total_items=total_items,
        total_preferences=total_preferences,
        total_files=total_files,
        total_size_bytes=file_size,
        checksum=computed_checksum,
        storage_path=storage_path,
        created_by=created_by,
        created_by_type=created_by_type,
        description=description
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
    Récupère et télécharge le dernier backup d'une application.
    
    Utilisé par les applications pour sync-from-hub.
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
    
    # Chemin du fichier
    file_path = STORAGE_BASE_DIR / backup.storage_path
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fichier de backup non trouvé sur le serveur"
        )
    
    return FileResponse(
        path=str(file_path),
        filename=f"backup_{slug}_{backup.backup_id}.zip",
        media_type="application/zip",
        headers={
            "X-Backup-ID": backup.backup_id,
            "X-Backup-Checksum": backup.checksum or "",
            "X-Backup-Created-At": backup.created_at.isoformat()
        }
    )


@router.get("/api/apps/{slug}/backup/{backup_id}/download")
async def download_backup(
    slug: str,
    backup_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Télécharge un backup spécifique (non chiffré).
    
    Réservé aux administrateurs du HUB.
    """
    backup = await get_backup_or_404(db, backup_id)
    
    if backup.app_slug != slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le backup ne correspond pas à l'application"
        )
    
    file_path = STORAGE_BASE_DIR / backup.storage_path
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fichier de backup non trouvé"
        )
    
    return FileResponse(
        path=str(file_path),
        filename=f"backup_{slug}_{backup_id}.zip",
        media_type="application/zip"
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
    puis récupère le résultat via GET /api/admin/backup/download.
    """
    import httpx
    
    app = await get_app_or_404(db, slug)
    
    if app.backup_mode == "disabled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Les sauvegardes sont désactivées pour cette application"
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucun service account trouvé pour cette application"
            )
        
        # TODO: Implémenter l'appel réel à l'application
        # Pour l'instant, on retourne un message d'information
        
        return BackupTriggerResponse(
            success=False,
            message="Déclenchement manuel non encore implémenté. L'application doit pousser ses backups via sync-to-hub.",
            backup_id=None,
            app_slug=slug
        )
        
    except HTTPException:
        raise
    except Exception as e:
        app.last_backup_status = "failed"
        await db.commit()
        logger.error(f"Erreur lors du déclenchement du backup pour {slug}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
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
