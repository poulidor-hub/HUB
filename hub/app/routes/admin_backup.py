"""
Routes d'export et d'import d'environnement du Hub (/api/admin/backup/*).

Réimplémentation (octobre 2026) : ces routes avaient été supprimées du backend
(commit e0708e0 — « remove obsolete env export », remplacées par le système de
snapshots) alors que les clients en dépendent toujours :
  - outils MCP `hub_backup_export_env` / `hub_backup_import_env` (template + apps) ;
  - module frontend `backup-import.js`.

Le symptôme : tout upload > 10 Mo se voyait répondre 413 par le middleware
anti-DoS (qui court-circuite avant le 404), masquant la disparition des routes.

La logique d'EXPORT est réutilisée de `app.hub_self_backup` (source unique déjà
utilisée par le flux AppBackup) ; l'IMPORT est reimplementé ici, adapté au
schéma actuel (uuid/oidc_sub/prenom/nom, paire de clés backup PRA, flags
include_*, chiffrement Fernet des secrets SA).
"""
import base64
import hashlib
import io
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, MissingGreenlet
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_admin
from app.database import get_db
from app.hub_self_backup import _build_zip_archive, _do_export
from app.models import (
    ApprovalStatus,
    AppBackup,
    AppRole,
    Application,
    ServiceAccount,
    User,
    UserAppRole,
    service_account_app_access,
    utcnow,
)
from app.schemas import (
    BackupExport,
    BackupImport,
    ImportMode,
    ImportResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/backup", tags=["backup"])

# Même stockage physique que le flux AppBackup (chemins relatifs « <slug>/<fichier> »).
STORAGE_BASE_DIR = Path("/code/storage/backups")


def _write_sa_secret_stored(
    name: str, client_id: str, stored_secret: str, app_slugs: list
) -> None:
    """
    Écrit le fichier de credentials d'un service account au format actuel
    (cf. `save_service_account_credentials`), en préservant la valeur TELLE QUE
    stockée dans l'export (« ENC:... » si chiffrée à la source, sinon clair) :
    on ne re-chiffre pas — le fichier cible doit rester lisible par le Hub
    courant avec SA propre SECRETS_ENCRYPTION_KEY.
    """
    try:
        from app.routes.service_accounts import SECRETS_DIR

        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-z0-9_-]", "_", name.lower())
        filepath = SECRETS_DIR / f"sa_{safe_name}.env"

        content = (
            f"# Service Account: {name}\n"
            "# Restauré depuis un import d'environnement - NE PAS COMMITER CE FICHIER !\n"
        )
        if stored_secret.startswith("ENC:"):
            content += "# 🔐 CLIENT_SECRET est chiffré avec Fernet\n"
        content += f"\nCLIENT_ID={client_id}\nCLIENT_SECRET={stored_secret}\n"
        if app_slugs:
            content += f"# Applications autorisées: {', '.join(app_slugs)}\n"

        filepath.write_text(content)
        filepath.chmod(0o600)
    except Exception as e:
        logger.warning(f"⚠️ Impossible d'écrire les credentials du SA {client_id[:16]}: {e}")


async def _do_import(
    backup: BackupImport,
    db: AsyncSession,
    current_user: User,
    zip_file: Optional[zipfile.ZipFile] = None,
) -> ImportResult:
    """
    Logique commune d'import d'environnement (applications, users, rôles,
    service accounts, clés, backups d'applications depuis une archive ZIP).
    """
    mode = backup.import_mode
    result = ImportResult(success=True)

    try:
        # 1. Applications (d'abord : référencées par users/SA/rôles)
        app_id_map: dict[str, int] = {}
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
                            detail=f"Application '{app_data.slug}' existe déjà",
                        )
                    if mode == ImportMode.skip:
                        result.apps_skipped += 1
                        app_id_map[app_data.slug] = existing_app.id
                        continue
                    # mode == update
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
                    if app_data.approval_status:
                        existing_app.approval_status = ApprovalStatus(app_data.approval_status)
                    existing_app.approved_at = app_data.approved_at
                    existing_app.rejection_reason = app_data.rejection_reason
                    result.apps_updated += 1
                    app_id_map[app_data.slug] = existing_app.id
                else:
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
                        approval_status=(
                            ApprovalStatus(app_data.approval_status)
                            if app_data.approval_status
                            else ApprovalStatus.approved
                        ),
                        approved_at=app_data.approved_at,
                        rejection_reason=app_data.rejection_reason,
                    )
                    db.add(new_app)
                    await db.flush()
                    app_id_map[app_data.slug] = new_app.id
                    result.apps_created += 1

                    for role_data in app_data.available_roles:
                        db.add(
                            AppRole(
                                app_id=new_app.id,
                                name=role_data.name,
                                description=role_data.description,
                            )
                        )
        else:
            result.warnings.append("Import des applications désactivé")

        # Résoudre les app_id manquants depuis la base (rôles/SA même sans import d'apps)
        for app_data in backup.applications:
            if app_data.slug not in app_id_map:
                existing = await db.execute(
                    select(Application).where(Application.slug == app_data.slug)
                )
                existing_app = existing.scalar_one_or_none()
                if existing_app:
                    app_id_map[app_data.slug] = existing_app.id

        # 2. Utilisateurs
        user_id_map: dict[str, int] = {}
        if backup.include_users:
            for user_data in backup.users:
                existing = await db.execute(
                    select(User).where(User.username == user_data.username)
                )
                existing_user = existing.scalar_one_or_none()

                if not existing_user and user_data.email:
                    existing_by_email = await db.execute(
                        select(User).where(User.email == user_data.email)
                    )
                    existing_user = existing_by_email.scalar_one_or_none()
                    if existing_user:
                        result.warnings.append(
                            f"User '{user_data.username}' matché par email "
                            f"'{user_data.email}' (username existant: "
                            f"'{existing_user.username}')"
                        )

                if existing_user:
                    if mode == ImportMode.error:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Utilisateur '{user_data.username}' existe déjà",
                        )
                    if mode == ImportMode.skip:
                        result.users_skipped += 1
                        user_id_map[user_data.username] = existing_user.id
                        continue
                    existing_user.email = user_data.email
                    existing_user.hashed_password = user_data.hashed_password
                    existing_user.is_active = user_data.is_active
                    existing_user.is_admin = user_data.is_admin
                    existing_user.must_change_password = user_data.must_change_password
                    existing_user.can_change_password = user_data.can_change_password
                    existing_user.prenom = user_data.prenom
                    existing_user.nom = user_data.nom
                    if user_data.uuid:
                        existing_user.uuid = user_data.uuid
                    if user_data.oidc_sub:
                        existing_user.oidc_sub = user_data.oidc_sub
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
                            can_change_password=user_data.can_change_password,
                            uuid=user_data.uuid,
                            oidc_sub=user_data.oidc_sub,
                            prenom=user_data.prenom,
                            nom=user_data.nom,
                        )
                        db.add(new_user)
                        await db.flush()
                        user_id_map[user_data.username] = new_user.id
                        result.users_created += 1
                    except IntegrityError as e:
                        await db.rollback()
                        result.warnings.append(
                            f"User '{user_data.username}' ignoré (conflit "
                            f"d'intégrité: {str(e).split(chr(10))[0]})"
                        )
                        result.users_skipped += 1
        else:
            result.warnings.append("Import des utilisateurs désactivé")

        # Résoudre les user_id manquants depuis la base
        for user_data in backup.users:
            if user_data.username not in user_id_map:
                existing = await db.execute(
                    select(User).where(User.username == user_data.username)
                )
                existing_user = existing.scalar_one_or_none()
                if existing_user:
                    user_id_map[user_data.username] = existing_user.id

        # 3. Associations user-app-role
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

                existing_uar = await db.execute(
                    select(UserAppRole).where(
                        UserAppRole.user_id == user_id,
                        UserAppRole.app_id == app_id,
                    )
                )
                if existing_uar.scalar_one_or_none():
                    if mode == ImportMode.update:
                        await db.execute(
                            UserAppRole.__table__.update()
                            .where(UserAppRole.user_id == user_id)
                            .where(UserAppRole.app_id == app_id)
                            .values(role=uar_data.role)
                        )
                        result.user_app_roles_updated += 1
                    continue

                db.add(
                    UserAppRole(
                        user_id=user_id,
                        app_id=app_id,
                        role=uar_data.role,
                        source=uar_data.source or "manual",
                    )
                )
                result.user_app_roles_created += 1
        else:
            result.warnings.append("Import des associations user-app désactivé")

        # 4. Service accounts
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
                            detail=f"Service Account '{sa_data.client_id}' existe déjà",
                        )
                    if mode == ImportMode.skip:
                        result.service_accounts_skipped += 1
                        continue
                    existing_sa.name = sa_data.name
                    existing_sa.description = sa_data.description
                    existing_sa.client_secret_hash = sa_data.client_secret_hash
                    existing_sa.is_active = sa_data.is_active

                    app_ids = [
                        app_id_map[slug] for slug in sa_data.app_slugs if slug in app_id_map
                    ]
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
                        is_active=sa_data.is_active,
                    )
                    db.add(new_sa)
                    await db.flush()

                    app_ids = [
                        app_id_map[slug] for slug in sa_data.app_slugs if slug in app_id_map
                    ]
                    for aid in app_ids:
                        await db.execute(
                            service_account_app_access.insert().values(
                                service_account_id=new_sa.id, app_id=aid
                            )
                        )
                    result.service_accounts_created += 1

                    if sa_data.client_secret_plain:
                        if sa_data.client_secret_plain.startswith("ENC:"):
                            result.warnings.append(
                                f"SA '{sa_data.client_id}': secret chiffré importé tel quel "
                                f"— la SECRETS_ENCRYPTION_KEY du Hub cible doit correspondre "
                                f"à celle du Hub source"
                            )
                        _write_sa_secret_stored(
                            sa_data.name,
                            sa_data.client_id,
                            sa_data.client_secret_plain,
                            sa_data.app_slugs,
                        )
        else:
            result.warnings.append("Import des service accounts désactivé")

        # 5. Clés (JWT + paire backup PRA)
        restore_keys = backup.restore_jwt_keys
        key_files = [
            ("jwt_private_key_b64", "jwt_private_key.pem"),
            ("jwt_public_key_b64", "jwt_public_key.pem"),
            ("backup_private_key_b64", "backup_private_key.pem"),
            ("backup_public_key_b64", "backup_public_key.pem"),
        ]
        if restore_keys:
            secrets_dir = Path("/code/secrets")
            any_restored = False
            for field, filename in key_files:
                payload_b64 = getattr(backup, field, None)
                if not payload_b64:
                    continue
                try:
                    (secrets_dir / filename).write_bytes(base64.b64decode(payload_b64))
                    any_restored = True
                except Exception as e:
                    result.errors.append(f"Erreur restauration {filename}: {e}")
            if any_restored:
                result.warnings.append(
                    "Clés restaurées - Redémarrage du Hub recommandé"
                )

        await db.commit()

        # 6. Backups d'applications (après commit : fichiers + lignes AppBackup)
        if backup.include_app_backups and backup.app_backups and zip_file:
            logger.info(f"📦 Import de {len(backup.app_backups)} backup(s) d'applications...")
            for ab_data in backup.app_backups:
                try:
                    existing_backup = await db.execute(
                        select(AppBackup).where(AppBackup.backup_id == ab_data.backup_id)
                    )
                    if existing_backup.scalar_one_or_none():
                        result.app_backups_skipped += 1
                        continue

                    if ab_data.storage_filename not in zip_file.namelist():
                        result.app_backups_errors += 1
                        result.warnings.append(
                            f"Backup {ab_data.backup_id} ({ab_data.app_slug}): "
                            f"fichier non trouvé dans l'archive"
                        )
                        continue

                    backup_content = zip_file.read(ab_data.storage_filename)

                    if ab_data.checksum:
                        computed = hashlib.sha256(backup_content).hexdigest()
                        if computed != ab_data.checksum:
                            result.app_backups_errors += 1
                            result.warnings.append(
                                f"Backup {ab_data.backup_id} ({ab_data.app_slug}): "
                                f"checksum invalide, ignoré"
                            )
                            continue

                    app_dir = STORAGE_BASE_DIR / ab_data.app_slug
                    app_dir.mkdir(parents=True, exist_ok=True)
                    filename = f"backup_{ab_data.backup_id}.zip"
                    storage_path = f"{ab_data.app_slug}/{filename}"
                    (app_dir / filename).write_bytes(backup_content)

                    app_id = app_id_map.get(ab_data.app_slug)
                    db.add(
                        AppBackup(
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
                            checksum=ab_data.checksum
                            or hashlib.sha256(backup_content).hexdigest(),
                            includes_vaultwarden_snapshot=ab_data.includes_vaultwarden_snapshot,
                            includes_secret_bundle=ab_data.includes_secret_bundle,
                            restore_requires_offline=ab_data.restore_requires_offline,
                            storage_path=storage_path,
                            created_by=ab_data.created_by or "import",
                            created_by_type=ab_data.created_by_type or "import",
                            created_at=ab_data.created_at or utcnow(),
                            description=ab_data.description,
                        )
                    )
                    result.app_backups_created += 1
                except Exception as e:
                    logger.error(f"❌ Erreur import backup {ab_data.backup_id}: {e}")
                    result.app_backups_errors += 1
                    result.warnings.append(
                        f"Backup {ab_data.backup_id} ({ab_data.app_slug}): {e}"
                    )

            await db.commit()

            # Politique de rétention pour chaque app importée
            imported_slugs = {ab.app_slug for ab in backup.app_backups}
            for slug in imported_slugs:
                app_result = await db.execute(
                    select(Application).where(Application.slug == slug)
                )
                app = app_result.scalar_one_or_none()
                retention = app.backup_retention if app else 5
                if retention > 0:
                    from app.routes.app_backups import apply_retention_policy

                    await apply_retention_policy(db, slug, retention)

            logger.info(
                f"📦 Import backups terminé: {result.app_backups_created} créé(s), "
                f"{result.app_backups_skipped} ignoré(s), {result.app_backups_errors} erreur(s)"
            )
        elif backup.app_backups and not zip_file:
            result.warnings.append(
                f"{len(backup.app_backups)} backup(s) d'applications référencé(s) mais "
                f"pas de fichier ZIP fourni — métadonnées ignorées"
            )

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        result.success = False
        result.errors.append(str(e))

    return result


@router.get("/export", response_model=BackupExport)
async def export_environment(
    include_jwt_keys: bool = False,
    include_sa_secrets: bool = True,
    include_app_backups: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Exporte l'environnement complet du Hub (JSON).

    - **include_jwt_keys**: inclure clés RSA JWT + paire backup PRA (⚠️ très sensible)
    - **include_sa_secrets**: inclure les secrets des service accounts
    - **include_app_backups**: inclure les métadonnées des 5 derniers backups par app
    """
    try:
        return await _do_export(
            db, current_user, include_jwt_keys, include_sa_secrets, include_app_backups
        )
    except MissingGreenlet as e:
        logger.error(f"❌ Erreur SQLAlchemy async pendant l'export: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors de l'export: relation DB non pré-chargée.",
        )


@router.get("/export/download")
async def download_export(
    include_jwt_keys: bool = True,
    include_sa_secrets: bool = True,
    include_app_backups: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Télécharge l'export : fichier JSON, ou archive ZIP (environment.json +
    fichiers de backups d'applications) si include_app_backups=true.
    """
    export_data = await _do_export(
        db, current_user, include_jwt_keys, include_sa_secrets, include_app_backups
    )

    timestamp = utcnow().strftime("%Y%m%d_%H%M%S")

    if include_app_backups:
        zip_buffer = _build_zip_archive(export_data)
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=hub_backup_{timestamp}.zip"
            },
        )

    return JSONResponse(
        content=export_data.model_dump(mode="json"),
        headers={
            "Content-Disposition": f"attachment; filename=hub_backup_{timestamp}.json",
            "Content-Type": "application/json",
        },
    )


@router.post("/import", response_model=ImportResult)
async def import_environment(
    backup: BackupImport,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Importe un environnement depuis un payload JSON (modes: skip / update / error).
    """
    return await _do_import(backup, db, current_user)


def _parse_env_payload(content: bytes) -> tuple[dict, Optional[zipfile.ZipFile]]:
    """
    Détecte le format (JSON ou ZIP), retourne (environment.json parsé, ZipFile
    ouverte ou None). Lève un 400 si le contenu est vide/invalide.
    """
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Fichier vide"
        )

    if content[:4] == b"PK\x03\x04":
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Archive ZIP invalide"
            )
        if "environment.json" not in zf.namelist():
            zf.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="L'archive ZIP ne contient pas de fichier environment.json",
            )
        return json.loads(zf.read("environment.json")), zf

    try:
        return json.loads(content), None
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier n'est ni un JSON valide ni une archive ZIP",
        )


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
    current_user: User = Depends(get_current_admin),
):
    """
    Importe un environnement depuis un fichier uploadé (JSON ou ZIP).

    L'archive ZIP doit contenir `environment.json` et optionnellement un
    dossier `backups/` avec les ZIP des backups d'applications.
    """
    content = await file.read()
    env_json, zip_file_obj = _parse_env_payload(content)

    try:
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
            jwt_public_key_b64=env_json.get("jwt_public_key_b64"),
            backup_private_key_b64=env_json.get("backup_private_key_b64"),
            backup_public_key_b64=env_json.get("backup_public_key_b64"),
        )
        return await _do_import(backup_data, db, current_user, zip_file=zip_file_obj)
    finally:
        if zip_file_obj:
            zip_file_obj.close()


@router.post("/import/preview")
async def preview_import_file(
    file: UploadFile = File(..., description="Fichier JSON ou archive ZIP d'export"),
    current_user: User = Depends(get_current_admin),
):
    """
    Prévisualise un fichier d'import (JSON ou ZIP) sans l'importer :
    compteurs et métadonnées pour l'aperçu frontend.
    """
    content = await file.read()
    env_json, zf = _parse_env_payload(content)
    if zf:
        zf.close()

    app_backups = env_json.get("app_backups", [])
    backups_by_app: dict[str, int] = {}
    for ab in app_backups:
        slug = ab.get("app_slug", "inconnu")
        backups_by_app[slug] = backups_by_app.get(slug, 0) + 1

    return {
        "format": "zip" if zf else "json",
        "version": env_json.get("version", "–"),
        "exported_at": env_json.get("exported_at"),
        "exported_by": env_json.get("exported_by", "–"),
        "users_count": len(env_json.get("users", [])),
        "apps_count": len(env_json.get("applications", [])),
        "service_accounts_count": len(env_json.get("service_accounts", [])),
        "user_app_roles_count": len(env_json.get("user_app_roles", [])),
        "has_jwt_keys": bool(env_json.get("jwt_private_key_b64")),
        "app_backups_count": len(app_backups),
        "app_backups_by_app": backups_by_app,
    }
