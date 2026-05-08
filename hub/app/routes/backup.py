"""
Routes d'export et import pour la sauvegarde/restauration d'environnement
Permet de cloner un environnement complet : users, apps, service accounts, config
"""
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import MissingGreenlet, IntegrityError
from typing import Optional
from datetime import datetime, timezone
import json
import os
import io
import uuid
import base64
import zipfile
import hashlib
from pathlib import Path

import logging

from app.database import get_db
from app.models import User, Application, UserAppRole, ServiceAccount, AppRole, ApprovalStatus, AppBackup, service_account_app_access, utcnow
from app.auth import get_current_admin, get_password_hash
from app.schemas import (
    BackupExport, BackupImport, ImportResult, ImportMode,
    UserExport, AppExport, ServiceAccountExport, UserAppRoleExport, AppRoleExport,
    AppBackupExport
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/backup", tags=["backup"])


# Chemins des fichiers de secrets
SECRETS_DIR = Path("/code/secrets")
SERVICE_ACCOUNTS_DIR = SECRETS_DIR / "service_accounts"
JWT_PRIVATE_KEY_PATH = SECRETS_DIR / "jwt_private_key.pem"
JWT_PUBLIC_KEY_PATH = SECRETS_DIR / "jwt_public_key.pem"
STORAGE_BASE_DIR = Path("/code/storage/backups")

MAX_BACKUPS_PER_APP_EXPORT = 5  # Limite de backups par app dans l'export


@router.get("/export", response_model=BackupExport)
async def export_environment(
    include_jwt_keys: bool = False,
    include_sa_secrets: bool = True,
    include_app_backups: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Exporte l'environnement complet du Hub.
    
    - **include_jwt_keys**: Inclure les clés RSA JWT (⚠️ désactivé par défaut — fuite = tous tokens compromis)
    - **include_sa_secrets**: Inclure les secrets des service accounts depuis les fichiers .env
    - **include_app_backups**: Inclure les 5 derniers backups de chaque application (métadonnées uniquement dans le JSON)
    
    ⚠️ Le fichier d'export contient des données sensibles, stockez-le de manière sécurisée !
    """
    
    try:
        return await _do_export(db, current_user, include_jwt_keys, include_sa_secrets, include_app_backups)
    except MissingGreenlet as e:
        logger.error(f"❌ Erreur SQLAlchemy async pendant l'export: {e}")
        logger.error("Vérifiez que toutes les relations accédées sont chargées via selectinload()")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors de l'export: une relation DB n'a pas été correctement pré-chargée. Consultez les logs."
        )


async def _do_export(
    db: AsyncSession,
    current_user: User,
    include_jwt_keys: bool,
    include_sa_secrets: bool,
    include_app_backups: bool = False
) -> BackupExport:
    
    # 1. Export des utilisateurs avec leurs mots de passe hashés
    # Note: pas de selectinload(app_roles) ici — on n'exporte que les champs scalaires.
    # Charger app_roles polluerait l'identity map avec des Application sans available_roles,
    # ce qui causerait des erreurs greenlet_spawn lors de la requête Applications.
    users_result = await db.execute(select(User))
    users = users_result.scalars().all()
    
    users_export = [
        UserExport(
            username=user.username,
            email=user.email,
            hashed_password=user.hashed_password,
            is_active=user.is_active,
            is_admin=user.is_admin,
            must_change_password=user.must_change_password or False,
            can_change_password=user.can_change_password if user.can_change_password is not None else True,
            created_at=user.created_at
        )
        for user in users
    ]
    
    # 2. Export des applications
    # populate_existing=True force le rechargement même si des Application sont déjà
    # dans l'identity map (chargées par get_current_admin sans available_roles)
    apps_result = await db.execute(
        select(Application).options(
            selectinload(Application.available_roles),
            selectinload(Application.approved_by)
        ).execution_options(populate_existing=True)
    )
    apps = apps_result.scalars().all()
    
    apps_export = [
        AppExport(
            name=app.name,
            slug=app.slug,
            description=app.description,
            icon=app.icon,
            color=app.color,
            api_url=app.api_url,
            frontend_url=app.frontend_url,
            callback_url=app.callback_url,
            is_active=app.is_active,
            requires_auth=app.requires_auth,
            is_shared_service=app.is_shared_service,
            approval_status=app.approval_status.value if app.approval_status else "approved",
            approved_by_username=app.approved_by.username if app.approved_by else None,
            approved_at=app.approved_at,
            rejection_reason=app.rejection_reason,
            created_at=app.created_at,
            available_roles=[
                AppRoleExport(name=role.name, description=role.description)
                for role in app.available_roles
            ]
        )
        for app in apps
    ]
    
    # 3. Export des associations user-app-role
    user_app_roles_result = await db.execute(
        select(UserAppRole)
        .options(selectinload(UserAppRole.user), selectinload(UserAppRole.application))
    )
    user_app_roles = user_app_roles_result.scalars().all()
    
    user_app_roles_export = [
        UserAppRoleExport(
            username=uar.user.username,
            app_slug=uar.application.slug,
            role=uar.role,
            source=uar.source or "manual"
        )
        for uar in user_app_roles
    ]
    
    # 4. Export des service accounts
    sa_result = await db.execute(
        select(ServiceAccount).options(selectinload(ServiceAccount.applications))
    )
    service_accounts = sa_result.scalars().all()
    
    service_accounts_export = []
    for sa in service_accounts:
        # Récupérer le secret depuis le fichier .env si demandé
        client_secret_plain = None
        if include_sa_secrets:
            client_secret_plain = _read_sa_secret(sa.client_id)
        
        service_accounts_export.append(
            ServiceAccountExport(
                name=sa.name,
                client_id=sa.client_id,
                client_secret_hash=sa.client_secret_hash,
                client_secret_plain=client_secret_plain,
                description=sa.description,
                is_active=sa.is_active,
                created_at=sa.created_at,
                app_slugs=[app.slug for app in sa.applications]
            )
        )
    
    # 5. Export des clés JWT (encodées en base64)
    jwt_private_key = None
    jwt_public_key = None
    if include_jwt_keys:
        logger.warning(f"⚠️  Export JWT keys demandé par {current_user.username} — la clé privée sera incluse dans le backup")
        if JWT_PRIVATE_KEY_PATH.exists():
            jwt_private_key = base64.b64encode(JWT_PRIVATE_KEY_PATH.read_bytes()).decode()
        if JWT_PUBLIC_KEY_PATH.exists():
            jwt_public_key = base64.b64encode(JWT_PUBLIC_KEY_PATH.read_bytes()).decode()
    
    # 6. Export des backups d'applications (5 derniers par app)
    app_backups_export = []
    if include_app_backups:
        logger.info(f"📦 Export des backups d'applications demandé par {current_user.username}")
        # Récupérer tous les slugs d'apps existantes
        app_slugs = [app.slug for app in apps]
        
        for app_slug in app_slugs:
            # Récupérer les 5 derniers backups pour cette app
            backups_result = await db.execute(
                select(AppBackup)
                .where(AppBackup.app_slug == app_slug)
                .order_by(AppBackup.created_at.desc())
                .limit(MAX_BACKUPS_PER_APP_EXPORT)
            )
            backups = backups_result.scalars().all()
            
            for backup in backups:
                backup_status = getattr(backup, "status", "success") or "success"
                if backup_status != "success":
                    logger.warning(
                        f"⚠️ Backup {backup.backup_id} ({app_slug}) : statut {backup_status}, ignoré pour l'export"
                    )
                    continue
                if not backup.storage_path:
                    logger.warning(
                        f"⚠️ Backup {backup.backup_id} ({app_slug}) : aucun fichier physique enregistré, ignoré"
                    )
                    continue

                full_path = STORAGE_BASE_DIR / backup.storage_path
                if not full_path.exists():
                    logger.warning(f"⚠️ Backup {backup.backup_id} ({app_slug}) : fichier physique introuvable, ignoré")
                    continue
                
                storage_filename = f"backups/{backup.storage_path}"
                app_backups_export.append(
                    AppBackupExport(
                        backup_id=backup.backup_id,
                        app_slug=backup.app_slug,
                        app_version=backup.app_version,
                        format_version=getattr(backup, "format_version", None),
                        total_items=backup.total_items or 0,
                        total_preferences=backup.total_preferences or 0,
                        total_files=backup.total_files or 0,
                        total_tables=getattr(backup, "total_tables", 0) or 0,
                        total_rows=getattr(backup, "total_rows", 0) or 0,
                        total_size_bytes=backup.total_size_bytes or 0,
                        checksum=backup.checksum,
                        includes_vaultwarden_snapshot=bool(getattr(backup, "includes_vaultwarden_snapshot", False)),
                        includes_secret_bundle=bool(getattr(backup, "includes_secret_bundle", False)),
                        restore_requires_offline=bool(getattr(backup, "restore_requires_offline", False)),
                        storage_filename=storage_filename,
                        created_by=backup.created_by,
                        created_by_type=backup.created_by_type,
                        created_at=backup.created_at,
                        description=backup.description
                    )
                )
        
        logger.info(f"📦 {len(app_backups_export)} backup(s) d'applications inclus dans l'export")
    
    return BackupExport(
        version="1.0",
        exported_at=utcnow(),
        exported_by=current_user.username,
        users=users_export,
        applications=apps_export,
        user_app_roles=user_app_roles_export,
        service_accounts=service_accounts_export,
        app_backups=app_backups_export,
        jwt_private_key_b64=jwt_private_key,
        jwt_public_key_b64=jwt_public_key
    )


@router.post("/import", response_model=ImportResult)
async def import_environment(
    backup: BackupImport,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Importe un environnement depuis un fichier de backup JSON.
    
    Modes disponibles:
    - **skip**: Ignorer les entités existantes
    - **update**: Mettre à jour les entités existantes
    - **error**: Échouer si une entité existe déjà
    
    ⚠️ Cette opération peut modifier des données existantes !
    """
    return await _do_import(backup, db, current_user)


async def _do_import(
    backup: BackupImport,
    db: AsyncSession,
    current_user: User,
    zip_file: Optional[zipfile.ZipFile] = None
) -> ImportResult:
    """
    Logique commune d'import d'environnement.
    
    Args:
        backup: Données de backup parsées
        db: Session DB
        current_user: Admin courant
        zip_file: Archive ZIP ouverte (si import depuis ZIP, pour extraire les fichiers de backup)
    """
    mode = backup.import_mode
    result = ImportResult(
        success=True,
        users_created=0,
        users_updated=0,
        users_skipped=0,
        apps_created=0,
        apps_updated=0,
        apps_skipped=0,
        service_accounts_created=0,
        service_accounts_updated=0,
        service_accounts_skipped=0,
        user_app_roles_created=0,
        app_backups_created=0,
        app_backups_skipped=0,
        app_backups_errors=0,
        errors=[],
        warnings=[]
    )
    
    try:
        # 1. Import des applications (d'abord car référencées par users et SA)
        app_id_map = {}  # slug -> id
        if backup.include_applications:
            for app_data in backup.applications:
                existing = await db.execute(
                    select(Application).where(Application.slug == app_data.slug)
                )
                existing_app = existing.scalar_one_or_none()
                
                if existing_app:
                    if mode == ImportMode.error:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Application '{app_data.slug}' existe déjà"
                        )
                    elif mode == ImportMode.skip:
                        result.apps_skipped += 1
                        app_id_map[app_data.slug] = existing_app.id
                        continue
                    elif mode == ImportMode.update:
                        # Mise à jour
                        existing_app.name = app_data.name
                        existing_app.description = app_data.description
                        existing_app.icon = app_data.icon
                        existing_app.color = app_data.color
                        existing_app.api_url = app_data.api_url
                        existing_app.frontend_url = app_data.frontend_url
                        existing_app.callback_url = app_data.callback_url
                        existing_app.is_active = app_data.is_active
                        existing_app.requires_auth = app_data.requires_auth
                        existing_app.is_shared_service = app_data.is_shared_service
                        # Restaurer le statut d'approbation
                        if app_data.approval_status:
                            existing_app.approval_status = ApprovalStatus(app_data.approval_status)
                        existing_app.approved_at = app_data.approved_at
                        existing_app.rejection_reason = app_data.rejection_reason
                        result.apps_updated += 1
                        app_id_map[app_data.slug] = existing_app.id
                else:
                    # Création
                    new_app = Application(
                        name=app_data.name,
                        slug=app_data.slug,
                        description=app_data.description,
                        icon=app_data.icon,
                        color=app_data.color,
                        api_url=app_data.api_url,
                        frontend_url=app_data.frontend_url,
                        callback_url=app_data.callback_url,
                        is_active=app_data.is_active,
                        requires_auth=app_data.requires_auth,
                        is_shared_service=app_data.is_shared_service,
                        approval_status=ApprovalStatus(app_data.approval_status) if app_data.approval_status else ApprovalStatus.approved,
                        approved_at=app_data.approved_at,
                        rejection_reason=app_data.rejection_reason
                    )
                    db.add(new_app)
                    await db.flush()
                    app_id_map[app_data.slug] = new_app.id
                    result.apps_created += 1
                    
                    # Créer les rôles disponibles
                    for role_data in app_data.available_roles:
                        app_role = AppRole(
                            app_id=new_app.id,
                            name=role_data.name,
                            description=role_data.description
                        )
                        db.add(app_role)
        else:
            result.warnings.append("Import des applications désactivé")
        
        # Résoudre les app_id manquants depuis la base (nécessaire pour roles/SA même si apps non importées)
        for app_data in backup.applications:
            if app_data.slug not in app_id_map:
                existing = await db.execute(
                    select(Application).where(Application.slug == app_data.slug)
                )
                existing_app = existing.scalar_one_or_none()
                if existing_app:
                    app_id_map[app_data.slug] = existing_app.id
        
        # 2. Import des utilisateurs
        user_id_map = {}  # username -> id
        if backup.include_users:
            for user_data in backup.users:
                existing = await db.execute(
                    select(User).where(User.username == user_data.username)
                )
                existing_user = existing.scalar_one_or_none()
                
                # Si non trouvé par username, vérifier par email (contrainte unique)
                if not existing_user and user_data.email:
                    existing_by_email = await db.execute(
                        select(User).where(User.email == user_data.email)
                    )
                    existing_user = existing_by_email.scalar_one_or_none()
                    if existing_user:
                        result.warnings.append(
                            f"User '{user_data.username}' matché par email '{user_data.email}' "
                            f"(username existant: '{existing_user.username}')"
                        )
                
                if existing_user:
                    if mode == ImportMode.error:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Utilisateur '{user_data.username}' existe déjà"
                        )
                    elif mode == ImportMode.skip:
                        result.users_skipped += 1
                        user_id_map[user_data.username] = existing_user.id
                        continue
                    elif mode == ImportMode.update:
                        existing_user.email = user_data.email
                        existing_user.hashed_password = user_data.hashed_password
                        existing_user.is_active = user_data.is_active
                        existing_user.is_admin = user_data.is_admin
                        existing_user.must_change_password = user_data.must_change_password
                        existing_user.can_change_password = user_data.can_change_password
                        result.users_updated += 1
                        user_id_map[user_data.username] = existing_user.id
                else:
                    try:
                        new_user = User(
                            username=user_data.username,
                            email=user_data.email,
                            hashed_password=user_data.hashed_password,
                            is_active=user_data.is_active,
                            is_admin=user_data.is_admin,
                            must_change_password=user_data.must_change_password,
                            can_change_password=user_data.can_change_password
                        )
                        db.add(new_user)
                        await db.flush()
                        user_id_map[user_data.username] = new_user.id
                        result.users_created += 1
                    except IntegrityError as e:
                        await db.rollback()
                        result.warnings.append(
                            f"User '{user_data.username}' ignoré (conflit d'intégrité: {str(e).split(chr(10))[0]})"
                        )
                        result.users_skipped += 1
        else:
            result.warnings.append("Import des utilisateurs désactivé")
        
        # Résoudre les user_id manquants depuis la base (nécessaire pour roles même si users non importés)
        for user_data in backup.users:
            if user_data.username not in user_id_map:
                existing = await db.execute(
                    select(User).where(User.username == user_data.username)
                )
                existing_user = existing.scalar_one_or_none()
                if existing_user:
                    user_id_map[user_data.username] = existing_user.id
        
        # 3. Import des associations user-app-role
        if backup.include_user_app_roles:
            for uar_data in backup.user_app_roles:
                user_id = user_id_map.get(uar_data.username)
                app_id = app_id_map.get(uar_data.app_slug)
                
                if not user_id:
                    result.warnings.append(f"User '{uar_data.username}' non trouvé pour role")
                    continue
                if not app_id:
                    result.warnings.append(f"App '{uar_data.app_slug}' non trouvée pour role")
                    continue
                
                # Vérifier si l'association existe
                existing_uar = await db.execute(
                    select(UserAppRole).where(
                        UserAppRole.user_id == user_id,
                        UserAppRole.app_id == app_id
                    )
                )
                if existing_uar.scalar_one_or_none():
                    if mode == ImportMode.update:
                        # Mettre à jour le rôle
                        await db.execute(
                            UserAppRole.__table__.update()
                            .where(UserAppRole.user_id == user_id)
                            .where(UserAppRole.app_id == app_id)
                            .values(role=uar_data.role)
                        )
                    continue
                
                new_uar = UserAppRole(
                    user_id=user_id,
                    app_id=app_id,
                    role=uar_data.role,
                    source=uar_data.source if hasattr(uar_data, 'source') else "manual"
                )
                db.add(new_uar)
                result.user_app_roles_created += 1
        else:
            result.warnings.append("Import des associations user-app désactivé")
        
        # 4. Import des service accounts
        if backup.include_service_accounts:
            for sa_data in backup.service_accounts:
                existing = await db.execute(
                    select(ServiceAccount)
                    .options(selectinload(ServiceAccount.applications))
                    .where(ServiceAccount.client_id == sa_data.client_id)
                )
                existing_sa = existing.scalar_one_or_none()
                
                if existing_sa:
                    if mode == ImportMode.error:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Service Account '{sa_data.client_id}' existe déjà"
                        )
                    elif mode == ImportMode.skip:
                        result.service_accounts_skipped += 1
                        continue
                    elif mode == ImportMode.update:
                        existing_sa.name = sa_data.name
                        existing_sa.description = sa_data.description
                        existing_sa.client_secret_hash = sa_data.client_secret_hash
                        existing_sa.is_active = sa_data.is_active
                        
                        # Mettre à jour les applications
                        app_ids = [app_id_map[slug] for slug in sa_data.app_slugs if slug in app_id_map]
                        apps_to_add = await db.execute(
                            select(Application).where(Application.id.in_(app_ids))
                        )
                        existing_sa.applications = list(apps_to_add.scalars().all())
                        
                        result.service_accounts_updated += 1
                else:
                    new_sa = ServiceAccount(
                        name=sa_data.name,
                        client_id=sa_data.client_id,
                        client_secret_hash=sa_data.client_secret_hash,
                        description=sa_data.description,
                        is_active=sa_data.is_active
                    )
                    db.add(new_sa)
                    await db.flush()
                    
                    # Associer les applications via la table d'association directement
                    app_ids = [app_id_map[slug] for slug in sa_data.app_slugs if slug in app_id_map]
                    for aid in app_ids:
                        await db.execute(
                            service_account_app_access.insert().values(
                                service_account_id=new_sa.id, app_id=aid
                            )
                        )
                    
                    result.service_accounts_created += 1
                    
                    # Sauvegarder le secret en clair si disponible
                    if sa_data.client_secret_plain:
                        _write_sa_secret(sa_data.client_id, sa_data.client_secret_plain, sa_data.app_slugs)
        else:
            result.warnings.append("Import des service accounts désactivé")
        
        # 5. Import des clés JWT
        if backup.jwt_private_key_b64 and backup.jwt_public_key_b64:
            if backup.restore_jwt_keys:
                try:
                    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
                    JWT_PRIVATE_KEY_PATH.write_bytes(base64.b64decode(backup.jwt_private_key_b64))
                    JWT_PUBLIC_KEY_PATH.write_bytes(base64.b64decode(backup.jwt_public_key_b64))
                    result.warnings.append("Clés JWT restaurées - Redémarrage du Hub recommandé")
                except Exception as e:
                    result.errors.append(f"Erreur lors de la restauration des clés JWT: {str(e)}")
        
        await db.commit()
        
        # 6. Import des backups d'applications (après commit pour avoir les app_id)
        if backup.include_app_backups and backup.app_backups and zip_file:
            logger.info(f"📦 Import de {len(backup.app_backups)} backup(s) d'applications...")
            for ab_data in backup.app_backups:
                try:
                    # Vérifier si un backup avec le même backup_id existe déjà
                    existing_backup = await db.execute(
                        select(AppBackup).where(AppBackup.backup_id == ab_data.backup_id)
                    )
                    if existing_backup.scalar_one_or_none():
                        result.app_backups_skipped += 1
                        continue
                    
                    # Extraire le fichier depuis l'archive ZIP
                    if ab_data.storage_filename not in zip_file.namelist():
                        logger.warning(f"⚠️ Fichier {ab_data.storage_filename} non trouvé dans l'archive")
                        result.app_backups_errors += 1
                        result.warnings.append(f"Backup {ab_data.backup_id} ({ab_data.app_slug}): fichier non trouvé dans l'archive")
                        continue
                    
                    backup_content = zip_file.read(ab_data.storage_filename)
                    
                    # Vérifier le checksum si disponible
                    if ab_data.checksum:
                        computed_checksum = hashlib.sha256(backup_content).hexdigest()
                        if computed_checksum != ab_data.checksum:
                            logger.warning(f"⚠️ Checksum invalide pour backup {ab_data.backup_id}")
                            result.app_backups_errors += 1
                            result.warnings.append(f"Backup {ab_data.backup_id} ({ab_data.app_slug}): checksum invalide, ignoré")
                            continue
                    
                    # Créer le dossier de stockage
                    app_dir = STORAGE_BASE_DIR / ab_data.app_slug
                    app_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Écrire le fichier sur disque
                    filename = f"backup_{ab_data.backup_id}.zip"
                    storage_path = f"{ab_data.app_slug}/{filename}"
                    full_path = app_dir / filename
                    full_path.write_bytes(backup_content)
                    
                    # Résoudre l'app_id
                    app_id = app_id_map.get(ab_data.app_slug)
                    
                    # Créer l'enregistrement en BDD
                    new_backup = AppBackup(
                        backup_id=ab_data.backup_id,
                        app_id=app_id,
                        app_slug=ab_data.app_slug,
                        app_version=ab_data.app_version,
                        format_version=ab_data.format_version,
                        total_items=ab_data.total_items,
                        total_preferences=ab_data.total_preferences,
                        total_files=ab_data.total_files,
                        total_tables=ab_data.total_tables,
                        total_rows=ab_data.total_rows,
                        total_size_bytes=len(backup_content),
                        checksum=ab_data.checksum or hashlib.sha256(backup_content).hexdigest(),
                        includes_vaultwarden_snapshot=ab_data.includes_vaultwarden_snapshot,
                        includes_secret_bundle=ab_data.includes_secret_bundle,
                        restore_requires_offline=ab_data.restore_requires_offline,
                        storage_path=storage_path,
                        created_by=ab_data.created_by or "import",
                        created_by_type=ab_data.created_by_type or "import",
                        created_at=ab_data.created_at or utcnow(),
                        description=ab_data.description
                    )
                    db.add(new_backup)
                    result.app_backups_created += 1
                    
                except Exception as e:
                    logger.error(f"❌ Erreur import backup {ab_data.backup_id}: {e}")
                    result.app_backups_errors += 1
                    result.warnings.append(f"Backup {ab_data.backup_id} ({ab_data.app_slug}): {str(e)}")
            
            await db.commit()
            
            # Appliquer la politique de rétention pour chaque app importée
            imported_slugs = set(ab.app_slug for ab in backup.app_backups)
            for slug in imported_slugs:
                app_result = await db.execute(
                    select(Application).where(Application.slug == slug)
                )
                app = app_result.scalar_one_or_none()
                retention = app.backup_retention if app else 5
                if retention > 0:
                    from app.routes.app_backups import apply_retention_policy
                    await apply_retention_policy(db, slug, retention)
            
            logger.info(f"📦 Import backups terminé: {result.app_backups_created} créé(s), {result.app_backups_skipped} ignoré(s), {result.app_backups_errors} erreur(s)")
        elif backup.app_backups and not zip_file:
            result.warnings.append(f"{len(backup.app_backups)} backup(s) d'applications référencé(s) mais pas de fichier ZIP fourni — métadonnées ignorées")
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        result.success = False
        result.errors.append(str(e))
    
    return result


@router.get("/export/download")
async def download_export(
    include_jwt_keys: bool = True,
    include_sa_secrets: bool = True,
    include_app_backups: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Télécharge l'export en tant que fichier.
    
    - Sans backups d'applications: fichier JSON classique
    - Avec backups d'applications: archive ZIP contenant environment.json + fichiers de backup
    """
    export_data = await _do_export(
        db, current_user, include_jwt_keys, include_sa_secrets, include_app_backups
    )
    
    timestamp = utcnow().strftime('%Y%m%d_%H%M%S')
    
    if include_app_backups:
        # Générer une archive ZIP
        zip_buffer = _build_zip_archive(export_data)
        filename = f"hub_backup_{timestamp}.zip"
        
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    else:
        # Export JSON classique
        filename = f"hub_backup_{timestamp}.json"
        return JSONResponse(
            content=export_data.model_dump(mode='json'),
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "application/json"
            }
        )


def _build_zip_archive(export_data: BackupExport) -> io.BytesIO:
    """
    Construit une archive ZIP contenant :
    - environment.json : les métadonnées de l'environnement
    - backups/{slug}/backup_{uuid}.zip : les fichiers de backup des applications
    """
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. Écrire le JSON d'environnement
        env_json = export_data.model_dump(mode='json')
        zf.writestr("environment.json", json.dumps(env_json, indent=2, ensure_ascii=False))
        
        # 2. Ajouter les fichiers de backup des applications
        for ab in export_data.app_backups:
            if not ab.storage_filename:
                logger.warning(f"⚠️ Backup {ab.backup_id} sans storage_filename, ignoré dans l'archive")
                continue
            # Le storage_filename est sous la forme "backups/{slug}/backup_{uuid}.zip"
            # Le fichier physique est dans STORAGE_BASE_DIR/{slug}/backup_{uuid}.zip
            # On doit extraire le chemin relatif depuis le storage_filename
            relative_path = ab.storage_filename.replace("backups/", "", 1)  # "{slug}/backup_{uuid}.zip"
            full_path = STORAGE_BASE_DIR / relative_path
            
            if full_path.exists():
                zf.write(str(full_path), ab.storage_filename)
            else:
                logger.warning(f"⚠️ Fichier backup introuvable lors de la création de l'archive: {full_path}")
    
    zip_buffer.seek(0)
    return zip_buffer


async def create_hub_backup_archive(
    db: AsyncSession,
    *,
    backup_id: str,
    created_by: str,
    include_jwt_keys: bool = False,
    include_sa_secrets: bool = True,
    include_app_backups: bool = False,
) -> dict:
    """Crée une archive ZIP de configuration du Hub pour le flux AppBackup."""

    class ExportActor:
        username = created_by

    export_data = await _do_export(
        db,
        ExportActor(),
        include_jwt_keys,
        include_sa_secrets,
        include_app_backups,
    )
    zip_buffer = _build_zip_archive(export_data)
    content = zip_buffer.getvalue()

    app_dir = STORAGE_BASE_DIR / "hub"
    app_dir.mkdir(parents=True, exist_ok=True)
    filename = f"backup_{backup_id}.zip"
    storage_path = f"hub/{filename}"
    full_path = app_dir / filename
    with open(full_path, "wb") as archive_file:
        archive_file.write(content)

    total_items = (
        len(export_data.users)
        + len(export_data.applications)
        + len(export_data.service_accounts)
        + len(export_data.user_app_roles)
    )
    total_rows = total_items + len(export_data.app_backups)

    return {
        "storage_path": storage_path,
        "total_size_bytes": len(content),
        "checksum": hashlib.sha256(content).hexdigest(),
        "format_version": export_data.version,
        "total_items": total_items,
        "total_files": 1,
        "total_tables": 5,
        "total_rows": total_rows,
        "description": "Export de configuration Hub",
    }


@router.post("/import/upload", response_model=ImportResult)
async def import_from_upload(
    file: UploadFile = File(..., description="Fichier JSON ou archive ZIP d'export"),
    import_mode: str = Form("skip"),
    restore_jwt_keys: bool = Form(False),
    include_users: bool = Form(True),
    include_applications: bool = Form(True),
    include_user_app_roles: bool = Form(True),
    include_service_accounts: bool = Form(True),
    include_app_backups: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Importe un environnement depuis un fichier uploadé (JSON ou ZIP).
    
    - **Fichier JSON** : import classique (sans backups d'applications)
    - **Archive ZIP** : import complet avec backups d'applications
    
    L'archive ZIP doit contenir un fichier `environment.json` et optionnellement
    un dossier `backups/` avec les fichiers ZIP des backups d'applications.
    """
    content = await file.read()
    
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fichier vide"
        )
    
    zip_file_obj = None
    
    try:
        # Détecter le format : ZIP ou JSON
        is_zip = content[:4] == b'PK\x03\x04'
        
        if is_zip:
            # Archive ZIP
            try:
                zip_file_obj = zipfile.ZipFile(io.BytesIO(content), 'r')
            except zipfile.BadZipFile:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Archive ZIP invalide"
                )
            
            if "environment.json" not in zip_file_obj.namelist():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="L'archive ZIP ne contient pas de fichier environment.json"
                )
            
            env_json = json.loads(zip_file_obj.read("environment.json"))
        else:
            # Fichier JSON
            try:
                env_json = json.loads(content)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Le fichier n'est ni un JSON valide ni une archive ZIP"
                )
        
        # Parser les données avec le mode d'import
        try:
            mode = ImportMode(import_mode)
        except ValueError:
            mode = ImportMode.skip
        
        backup_data = BackupImport(
            import_mode=mode,
            restore_jwt_keys=restore_jwt_keys,
            include_users=include_users,
            include_applications=include_applications,
            include_user_app_roles=include_user_app_roles,
            include_service_accounts=include_service_accounts,
            include_app_backups=include_app_backups,
            users=env_json.get("users", []),
            applications=env_json.get("applications", []),
            user_app_roles=env_json.get("user_app_roles", []),
            service_accounts=env_json.get("service_accounts", []),
            app_backups=env_json.get("app_backups", []),
            jwt_private_key_b64=env_json.get("jwt_private_key_b64"),
            jwt_public_key_b64=env_json.get("jwt_public_key_b64")
        )
        
        return await _do_import(backup_data, db, current_user, zip_file=zip_file_obj)
        
    finally:
        if zip_file_obj:
            zip_file_obj.close()


@router.post("/import/preview")
async def preview_import_file(
    file: UploadFile = File(..., description="Fichier JSON ou archive ZIP d'export"),
    current_user: User = Depends(get_current_admin)
):
    """
    Prévisualise le contenu d'un fichier d'import (JSON ou ZIP) sans l'importer.
    Retourne les compteurs et métadonnées pour afficher un aperçu dans le frontend.
    """
    content = await file.read()
    
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fichier vide"
        )
    
    try:
        is_zip = content[:4] == b'PK\x03\x04'
        
        if is_zip:
            try:
                with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
                    if "environment.json" not in zf.namelist():
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="L'archive ZIP ne contient pas de fichier environment.json"
                        )
                    env_json = json.loads(zf.read("environment.json"))
            except zipfile.BadZipFile:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Archive ZIP invalide"
                )
        else:
            try:
                env_json = json.loads(content)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Le fichier n'est ni un JSON valide ni une archive ZIP"
                )
        
        # Compter les backups par app
        app_backups = env_json.get("app_backups", [])
        backups_by_app = {}
        for ab in app_backups:
            slug = ab.get("app_slug", "inconnu")
            backups_by_app[slug] = backups_by_app.get(slug, 0) + 1
        
        return {
            "format": "zip" if is_zip else "json",
            "version": env_json.get("version", "–"),
            "exported_at": env_json.get("exported_at"),
            "exported_by": env_json.get("exported_by", "–"),
            "users_count": len(env_json.get("users", [])),
            "apps_count": len(env_json.get("applications", [])),
            "service_accounts_count": len(env_json.get("service_accounts", [])),
            "user_app_roles_count": len(env_json.get("user_app_roles", [])),
            "has_jwt_keys": bool(env_json.get("jwt_private_key_b64")),
            "app_backups_count": len(app_backups),
            "app_backups_by_app": backups_by_app
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erreur lors de l'analyse du fichier: {str(e)}"
        )


def _read_sa_secret(client_id: str) -> Optional[str]:
    """Lit le secret d'un service account depuis son fichier .env"""
    if not SERVICE_ACCOUNTS_DIR.exists():
        return None
    
    # Chercher le fichier correspondant au client_id
    for env_file in SERVICE_ACCOUNTS_DIR.glob("sa_*.env"):
        try:
            content = env_file.read_text()
            for line in content.split('\n'):
                if line.startswith('CLIENT_ID=') and client_id in line:
                    # Trouvé le bon fichier, chercher le secret
                    for line2 in content.split('\n'):
                        if line2.startswith('CLIENT_SECRET='):
                            return line2.split('=', 1)[1].strip()
        except Exception:
            continue
    return None


def _write_sa_secret(client_id: str, client_secret: str, app_slugs: list):
    """Écrit le secret d'un service account dans un fichier .env"""
    try:
        SERVICE_ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
        
        # Générer le nom du fichier basé sur les apps
        if app_slugs:
            app_name = app_slugs[0].replace('-', '_')
            filename = f"sa_{app_name}.env"
        else:
            filename = f"sa_{client_id[:16]}.env"
        
        filepath = SERVICE_ACCOUNTS_DIR / filename
        
        content = f"""CLIENT_ID={client_id}
CLIENT_SECRET={client_secret}
# Applications autorisées: {', '.join(app_slugs)}
# Généré lors de l'import le {utcnow().isoformat()}
"""
        filepath.write_text(content)
    except Exception as e:
        logger.warning(f"⚠️ Impossible d'écrire le secret du service account {client_id[:16]}: {e}")
