"""
Restauration self-backup du HUB : import d'un fichier environment.json.

Endpoint POST /api/admin/restore — accepte admin et service accounts (M2M).
La logique d'import est isolée dans import_environment() pour être testable
sans couche HTTP.
"""
import io
import json
import zipfile
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import User, Application, UserAppRole, ServiceAccount, utcnow
from app.schemas import (
    BackupExport,
    BackupImport,
    ImportMode,
    ImportResult,
    UserExport,
    AppExport,
    ServiceAccountExport,
    UserAppRoleExport,
)
from app.auth import get_current_entity, get_password_hash
from app.routes.service_accounts import save_service_account_credentials

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Hub Restore"])
SECRETS_DIR = Path("/code/secrets/service_accounts")


async def import_environment(db: AsyncSession, data: BackupImport) -> ImportResult:
    """
    Importe un BackupImport en base, mode update par défaut.
    Best-effort : une erreur sur une entité est loggée et l'import continue.
    """
    result = ImportResult(success=True)
    await _import_users(db, data, result)
    await _import_applications(db, data, result)
    await _import_service_accounts(db, data, result)
    await _import_user_app_roles(db, data, result)
    await db.commit()
    return result


async def _import_users(db: AsyncSession, data: BackupImport, result: ImportResult) -> None:
    for user_export in data.users:
        created = False
        updated = False
        try:
            async with db.begin_nested():
                existing = (await db.execute(
                    select(User).where(User.username == user_export.username)
                )).scalar_one_or_none()
                if existing is None:
                    db.add(User(
                        username=user_export.username,
                        email=user_export.email,
                        hashed_password=user_export.hashed_password,
                        is_active=user_export.is_active,
                        is_admin=user_export.is_admin,
                        must_change_password=user_export.must_change_password,
                        can_change_password=user_export.can_change_password,
                    ))
                    created = True
                else:
                    existing.email = user_export.email
                    existing.hashed_password = user_export.hashed_password
                    existing.is_active = user_export.is_active
                    existing.is_admin = user_export.is_admin
                    existing.must_change_password = user_export.must_change_password
                    existing.can_change_password = user_export.can_change_password
                    updated = True
            # Savepoint committed — count the result
            if created:
                result.users_created += 1
            if updated:
                result.users_updated += 1
        except Exception as e:
            logger.warning(f"Import user {user_export.username} échoué: {e}")
            result.errors.append(f"user {user_export.username}: {e}")
            # savepoint auto-rolled back by begin_nested() on exception


async def _import_applications(db: AsyncSession, data: BackupImport, result: ImportResult) -> None:
    for app_export in data.applications:
        created = False
        updated = False
        try:
            async with db.begin_nested():
                existing = (await db.execute(
                    select(Application).where(Application.slug == app_export.slug)
                )).scalar_one_or_none()
                if existing is None:
                    db.add(Application(
                        name=app_export.name,
                        slug=app_export.slug,
                        description=app_export.description,
                        icon=app_export.icon,
                        color=app_export.color,
                        api_url=app_export.api_url,
                        frontend_url=app_export.frontend_url,
                        callback_url=app_export.callback_url,
                        is_active=app_export.is_active,
                        requires_auth=app_export.requires_auth,
                        is_shared_service=app_export.is_shared_service,
                    ))
                    created = True
                else:
                    existing.name = app_export.name
                    if app_export.description is not None:
                        existing.description = app_export.description
                    existing.icon = app_export.icon
                    existing.color = app_export.color
                    existing.api_url = app_export.api_url
                    if app_export.frontend_url is not None:
                        existing.frontend_url = app_export.frontend_url
                    if app_export.callback_url is not None:
                        existing.callback_url = app_export.callback_url
                    existing.is_active = app_export.is_active
                    existing.requires_auth = app_export.requires_auth
                    existing.is_shared_service = app_export.is_shared_service
                    updated = True
            if created:
                result.apps_created += 1
            if updated:
                result.apps_updated += 1
        except Exception as e:
            logger.warning(f"Import app {app_export.slug} échoué: {e}")
            result.errors.append(f"app {app_export.slug}: {e}")
            # savepoint auto-rolled back by begin_nested() on exception


async def _import_service_accounts(
    db: AsyncSession, data: BackupImport, result: ImportResult
) -> None:
    for sa_export in data.service_accounts:
        if not sa_export.client_secret_plain:
            msg = f"SA {sa_export.client_id} sans client_secret_plain — ignoré"
            logger.warning(msg)
            result.warnings.append(msg)
            result.service_accounts_skipped += 1
            continue

        created = False
        updated = False
        try:
            async with db.begin_nested():
                existing = (await db.execute(
                    select(ServiceAccount).where(
                        ServiceAccount.client_id == sa_export.client_id
                    )
                )).scalar_one_or_none()

                app_slugs = sa_export.app_slugs or []
                apps = []
                if app_slugs:
                    apps_result = await db.execute(
                        select(Application).where(Application.slug.in_(app_slugs))
                    )
                    apps = list(apps_result.scalars().all())

                if existing is None:
                    sa = ServiceAccount(
                        name=sa_export.name,
                        description=sa_export.description,
                        client_id=sa_export.client_id,
                        client_secret_hash=get_password_hash(sa_export.client_secret_plain),
                        is_active=sa_export.is_active,
                    )
                    sa.applications = apps
                    db.add(sa)
                    created = True
                else:
                    existing.name = sa_export.name
                    if sa_export.description is not None:
                        existing.description = sa_export.description
                    existing.is_active = sa_export.is_active
                    existing.client_secret_hash = get_password_hash(
                        sa_export.client_secret_plain
                    )
                    existing.applications = apps
                    updated = True
            if created:
                result.service_accounts_created += 1
            if updated:
                result.service_accounts_updated += 1

            file_name = sa_export.app_slugs[0] if sa_export.app_slugs else sa_export.name
            save_service_account_credentials(
                name=file_name,
                client_id=sa_export.client_id,
                client_secret=sa_export.client_secret_plain,
                app_slugs=app_slugs,
            )
        except Exception as e:
            logger.warning(f"Import SA {sa_export.client_id} échoué: {e}")
            result.errors.append(f"service_account {sa_export.client_id}: {e}")
            # savepoint auto-rolled back by begin_nested() on exception


async def _import_user_app_roles(
    db: AsyncSession, data: BackupImport, result: ImportResult
) -> None:
    for role_export in data.user_app_roles:
        created = False
        try:
            user = (await db.execute(
                select(User).where(User.username == role_export.username)
            )).scalar_one_or_none()
            app = (await db.execute(
                select(Application).where(Application.slug == role_export.app_slug)
            )).scalar_one_or_none()
            if not user:
                result.warnings.append(
                    f"role {role_export.username}->{role_export.app_slug}: user manquant"
                )
                continue
            if not app:
                result.warnings.append(
                    f"role {role_export.username}->{role_export.app_slug}: app manquante"
                )
                continue

            async with db.begin_nested():
                existing = (await db.execute(
                    select(UserAppRole).where(
                        UserAppRole.user_id == user.id,
                        UserAppRole.app_id == app.id,
                    )
                )).scalar_one_or_none()
                if existing is None:
                    db.add(UserAppRole(
                        user_id=user.id,
                        app_id=app.id,
                        role=role_export.role,
                        source=role_export.source,
                    ))
                    created = True
                else:
                    existing.role = role_export.role
                    existing.source = role_export.source
            if created:
                result.user_app_roles_created += 1
        except Exception as e:
            logger.warning(
                f"Import role {role_export.username}->{role_export.app_slug} échoué: {e}"
            )
            result.errors.append(
                f"role {role_export.username}->{role_export.app_slug}: {e}"
            )
            # savepoint auto-rolled back by begin_nested() on exception


def build_backup_import_from_zip_bytes(zip_bytes: bytes) -> BackupImport:
    """
    Lit un ZIP contenant environment.json et retourne un BackupImport.
    Lève HTTPException 400 en cas d'erreur de format.
    """
    try:
        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            if "environment.json" not in zf.namelist():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="environment.json non trouvé dans l'archive",
                )
            raw = zf.read("environment.json")
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fichier ZIP corrompu ou invalide",
        )
    try:
        env = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"environment.json invalide: {e}",
        )
    # Convertir BackupExport (export) en BackupImport (update par défaut)
    return BackupImport(
        import_mode=ImportMode.update,
        users=env.get("users", []),
        applications=env.get("applications", []),
        user_app_roles=env.get("user_app_roles", []),
        service_accounts=env.get("service_accounts", []),
        app_backups=env.get("app_backups", []),
        jwt_private_key_b64=env.get("jwt_private_key_b64"),
        jwt_public_key_b64=env.get("jwt_public_key_b64"),
    )
