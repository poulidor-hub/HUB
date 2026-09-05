"""
Self-backup du Hub: export de configuration au format ZIP pour le flux AppBackup.
Extrait de backup.py — conserve uniquement le necessaire pour create_hub_backup_archive().
"""
import base64
import hashlib
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import User, Application, UserAppRole, ServiceAccount, AppBackup, utcnow
from app.schemas import (
    BackupExport, UserExport, AppExport, ServiceAccountExport,
    UserAppRoleExport, AppRoleExport, AppBackupExport,
)

logger = logging.getLogger(__name__)

SECRETS_DIR = Path("/code/secrets")
SERVICE_ACCOUNTS_DIR = SECRETS_DIR / "service_accounts"
JWT_PRIVATE_KEY_PATH = SECRETS_DIR / "jwt_private_key.pem"
JWT_PUBLIC_KEY_PATH = SECRETS_DIR / "jwt_public_key.pem"
STORAGE_BASE_DIR = Path("/code/storage/backups")
MAX_BACKUPS_PER_APP_EXPORT = 5


def _read_sa_secret(client_id: str) -> Optional[str]:
    if not SERVICE_ACCOUNTS_DIR.exists():
        return None
    for env_file in SERVICE_ACCOUNTS_DIR.glob("sa_*.env"):
        try:
            content = env_file.read_text()
            for line in content.split('\n'):
                if line.startswith('CLIENT_ID=') and client_id in line:
                    for line2 in content.split('\n'):
                        if line2.startswith('CLIENT_SECRET='):
                            return line2.split('=', 1)[1].strip()
        except Exception:
            continue
    return None


async def _do_export(
    db: AsyncSession,
    current_user,
    include_jwt_keys: bool,
    include_sa_secrets: bool,
    include_app_backups: bool = False,
) -> BackupExport:
    users_result = await db.execute(select(User))
    users = users_result.scalars().all()

    users_export = [
        UserExport(
            username=user.username, email=user.email,
            hashed_password=user.hashed_password,
            is_active=user.is_active, is_admin=user.is_admin,
            must_change_password=user.must_change_password or False,
            can_change_password=user.can_change_password if user.can_change_password is not None else True,
            created_at=user.created_at,
            uuid=user.uuid,
            oidc_sub=user.oidc_sub,
            prenom=user.prenom,
            nom=user.nom,
        )
        for user in users
    ]

    apps_result = await db.execute(
        select(Application).options(
            selectinload(Application.available_roles),
            selectinload(Application.approved_by),
        ).execution_options(populate_existing=True)
    )
    apps = apps_result.scalars().all()

    apps_export = [
        AppExport(
            name=app.name, slug=app.slug, description=app.description,
            icon=app.icon, color=app.color,
            api_url=app.api_url, frontend_url=app.frontend_url,
            callback_url=app.callback_url,
            is_active=app.is_active, requires_auth=app.requires_auth,
            is_shared_service=app.is_shared_service,
            approval_status=app.approval_status.value if app.approval_status else "approved",
            approved_by_username=app.approved_by.username if app.approved_by else None,
            approved_at=app.approved_at,
            rejection_reason=app.rejection_reason,
            created_at=app.created_at,
            available_roles=[
                AppRoleExport(name=role.name, description=role.description)
                for role in app.available_roles
            ],
        )
        for app in apps
    ]

    user_app_roles_result = await db.execute(
        select(UserAppRole).options(
            selectinload(UserAppRole.user), selectinload(UserAppRole.application),
        )
    )
    user_app_roles = user_app_roles_result.scalars().all()

    user_app_roles_export = [
        UserAppRoleExport(
            username=uar.user.username, app_slug=uar.application.slug,
            role=uar.role, source=uar.source or "manual",
        )
        for uar in user_app_roles
    ]

    sa_result = await db.execute(
        select(ServiceAccount).options(selectinload(ServiceAccount.applications))
    )
    service_accounts = sa_result.scalars().all()

    service_accounts_export = []
    for sa in service_accounts:
        client_secret_plain = None
        if include_sa_secrets:
            client_secret_plain = _read_sa_secret(sa.client_id)
        service_accounts_export.append(
            ServiceAccountExport(
                name=sa.name, client_id=sa.client_id,
                client_secret_hash=sa.client_secret_hash,
                client_secret_plain=client_secret_plain,
                description=sa.description, is_active=sa.is_active,
                created_at=sa.created_at,
                app_slugs=[app.slug for app in sa.applications],
            )
        )

    jwt_private_key = None
    jwt_public_key = None
    if include_jwt_keys:
        logger.warning(f"Export JWT keys demande par {current_user.username}")
        if JWT_PRIVATE_KEY_PATH.exists():
            jwt_private_key = base64.b64encode(JWT_PRIVATE_KEY_PATH.read_bytes()).decode()
        if JWT_PUBLIC_KEY_PATH.exists():
            jwt_public_key = base64.b64encode(JWT_PUBLIC_KEY_PATH.read_bytes()).decode()

    app_backups_export = []
    if include_app_backups:
        app_slugs = [app.slug for app in apps]
        for app_slug in app_slugs:
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
                    continue
                if not backup.storage_path:
                    continue
                full_path = STORAGE_BASE_DIR / backup.storage_path
                if not full_path.exists():
                    continue
                storage_filename = f"backups/{backup.storage_path}"
                app_backups_export.append(
                    AppBackupExport(
                        backup_id=backup.backup_id, app_slug=backup.app_slug,
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
                        created_by=backup.created_by, created_by_type=backup.created_by_type,
                        created_at=backup.created_at, description=backup.description,
                    )
                )
        logger.info(f"{len(app_backups_export)} backup(s) inclus dans l'export")

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
        jwt_public_key_b64=jwt_public_key,
    )


def _build_zip_archive(export_data: BackupExport) -> io.BytesIO:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        env_json = export_data.model_dump(mode='json')
        zf.writestr("environment.json", json.dumps(env_json, indent=2, ensure_ascii=False))
        for ab in export_data.app_backups:
            if not ab.storage_filename:
                continue
            relative_path = ab.storage_filename.replace("backups/", "", 1)
            full_path = STORAGE_BASE_DIR / relative_path
            if full_path.exists():
                zf.write(str(full_path), ab.storage_filename)
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
    class ExportActor:
        username = created_by

    export_data = await _do_export(
        db, ExportActor(),
        include_jwt_keys, include_sa_secrets, include_app_backups,
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
