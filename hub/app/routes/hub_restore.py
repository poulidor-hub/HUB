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

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Hub Restore"])


async def import_environment(db: AsyncSession, data: BackupImport) -> ImportResult:
    """
    Importe un BackupImport en base, mode update par défaut.
    Best-effort : une erreur sur une entité est loggée et l'import continue.
    """
    result = ImportResult(success=True)
    await _import_users(db, data, result)
    await _import_applications(db, data, result)
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
            await db.rollback()


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
