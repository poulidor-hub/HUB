"""
Routes d'export et import pour la sauvegarde/restauration d'environnement
Permet de cloner un environnement complet : users, apps, service accounts, config
"""
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import Optional
from datetime import datetime
import json
import os
import base64
from pathlib import Path

from app.database import get_db
from app.models import User, Application, UserAppRole, ServiceAccount, AppRole, ApprovalStatus
from app.auth import get_current_admin, get_password_hash
from app.schemas import (
    BackupExport, BackupImport, ImportResult, ImportMode,
    UserExport, AppExport, ServiceAccountExport, UserAppRoleExport, AppRoleExport
)

router = APIRouter(prefix="/api/admin/backup", tags=["backup"])


# Chemins des fichiers de secrets
SECRETS_DIR = Path("/code/secrets")
SERVICE_ACCOUNTS_DIR = SECRETS_DIR / "service_accounts"
JWT_PRIVATE_KEY_PATH = SECRETS_DIR / "jwt_private_key.pem"
JWT_PUBLIC_KEY_PATH = SECRETS_DIR / "jwt_public_key.pem"


@router.get("/export", response_model=BackupExport)
async def export_environment(
    include_jwt_keys: bool = True,
    include_sa_secrets: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Exporte l'environnement complet du Hub.
    
    - **include_jwt_keys**: Inclure les clés RSA JWT (nécessaire pour conserver les sessions)
    - **include_sa_secrets**: Inclure les secrets des service accounts depuis les fichiers .env
    
    ⚠️ Le fichier d'export contient des données sensibles, stockez-le de manière sécurisée !
    """
    
    # 1. Export des utilisateurs avec leurs mots de passe hashés
    users_result = await db.execute(
        select(User).options(selectinload(User.app_roles).selectinload(UserAppRole.application))
    )
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
    apps_result = await db.execute(
        select(Application).options(
            selectinload(Application.available_roles),
            selectinload(Application.approved_by)
        )
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
            role=uar.role
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
        if JWT_PRIVATE_KEY_PATH.exists():
            jwt_private_key = base64.b64encode(JWT_PRIVATE_KEY_PATH.read_bytes()).decode()
        if JWT_PUBLIC_KEY_PATH.exists():
            jwt_public_key = base64.b64encode(JWT_PUBLIC_KEY_PATH.read_bytes()).decode()
    
    return BackupExport(
        version="1.0",
        exported_at=datetime.utcnow(),
        exported_by=current_user.username,
        users=users_export,
        applications=apps_export,
        user_app_roles=user_app_roles_export,
        service_accounts=service_accounts_export,
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
    Importe un environnement depuis un fichier de backup.
    
    Modes disponibles:
    - **skip**: Ignorer les entités existantes
    - **update**: Mettre à jour les entités existantes
    - **error**: Échouer si une entité existe déjà
    
    ⚠️ Cette opération peut modifier des données existantes !
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
        errors=[],
        warnings=[]
    )
    
    try:
        # 1. Import des applications (d'abord car référencées par users et SA)
        app_id_map = {}  # slug -> id
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
        
        # 2. Import des utilisateurs
        user_id_map = {}  # username -> id
        for user_data in backup.users:
            existing = await db.execute(
                select(User).where(User.username == user_data.username)
            )
            existing_user = existing.scalar_one_or_none()
            
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
        
        # 3. Import des associations user-app-role
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
                role=uar_data.role
            )
            db.add(new_uar)
            result.user_app_roles_created += 1
        
        # 4. Import des service accounts
        for sa_data in backup.service_accounts:
            existing = await db.execute(
                select(ServiceAccount).where(ServiceAccount.client_id == sa_data.client_id)
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
                
                # Associer les applications
                app_ids = [app_id_map[slug] for slug in sa_data.app_slugs if slug in app_id_map]
                if app_ids:
                    apps_to_add = await db.execute(
                        select(Application).where(Application.id.in_(app_ids))
                    )
                    new_sa.applications = list(apps_to_add.scalars().all())
                
                result.service_accounts_created += 1
                
                # Sauvegarder le secret en clair si disponible
                if sa_data.client_secret_plain:
                    _write_sa_secret(sa_data.client_id, sa_data.client_secret_plain, sa_data.app_slugs)
        
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Télécharge l'export en tant que fichier JSON.
    """
    export_data = await export_environment(
        include_jwt_keys=include_jwt_keys,
        include_sa_secrets=include_sa_secrets,
        db=db,
        current_user=current_user
    )
    
    filename = f"hub_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    
    return JSONResponse(
        content=export_data.model_dump(mode='json'),
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": "application/json"
        }
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
# Généré lors de l'import le {datetime.utcnow().isoformat()}
"""
        filepath.write_text(content)
    except Exception:
        pass  # Silencieux en cas d'erreur
