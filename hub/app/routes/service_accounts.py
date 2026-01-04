"""
Routes de gestion des Service Accounts (M2M Authentication)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
from pathlib import Path
import re
import logging
import base64

from app.database import get_db
from app.routes.events import notify_event, notify_stats_update
from app.models import ServiceAccount, Application
from app.schemas import (
    ServiceAccountCreate,
    ServiceAccountUpdate,
    ServiceAccountResponse,
    ServiceAccountCreated
)
from app.auth import get_current_admin, get_password_hash, get_settings

router = APIRouter(prefix="/api/service-accounts", tags=["Service Accounts"])

# Répertoire des secrets pour les service accounts (en écriture)
SECRETS_DIR = Path("/code/secrets/service_accounts")
logger = logging.getLogger(__name__)
settings = get_settings()


# ===== Chiffrement des secrets =====

def get_fernet_cipher():
    """
    Retourne une instance Fernet pour chiffrer/déchiffrer les secrets.
    Retourne None si la clé n'est pas configurée.
    """
    if not settings.secrets_encryption_key:
        return None
    
    try:
        from cryptography.fernet import Fernet
        return Fernet(settings.secrets_encryption_key.encode())
    except Exception as e:
        logger.warning(f"⚠️ Impossible d'initialiser le chiffrement Fernet: {e}")
        return None


def encrypt_secret(secret: str) -> str:
    """
    Chiffre un secret avec Fernet si la clé est configurée.
    Retourne le secret en clair si pas de clé.
    
    Format chiffré: ENC:base64_encrypted_data
    """
    cipher = get_fernet_cipher()
    if not cipher:
        return secret
    
    try:
        encrypted = cipher.encrypt(secret.encode())
        return f"ENC:{encrypted.decode()}"
    except Exception as e:
        logger.warning(f"⚠️ Échec du chiffrement: {e}")
        return secret


def decrypt_secret(encrypted_secret: str) -> str:
    """
    Déchiffre un secret si il est préfixé par 'ENC:'.
    Retourne le secret tel quel si non chiffré.
    """
    if not encrypted_secret.startswith("ENC:"):
        return encrypted_secret
    
    cipher = get_fernet_cipher()
    if not cipher:
        logger.warning("⚠️ Secret chiffré trouvé mais pas de clé de déchiffrement configurée")
        return encrypted_secret
    
    try:
        encrypted_data = encrypted_secret[4:]  # Enlever "ENC:"
        decrypted = cipher.decrypt(encrypted_data.encode())
        return decrypted.decode()
    except Exception as e:
        logger.warning(f"⚠️ Échec du déchiffrement: {e}")
        return encrypted_secret


def delete_service_account_credentials(name: str) -> bool:
    """
    Supprime le fichier .env des credentials d'un service account.
    
    Args:
        name: Nom/slug du service account (utilisé pour construire le nom du fichier)
    
    Returns:
        True si le fichier a été supprimé, False sinon
    """
    try:
        # Même logique de nommage que save_service_account_credentials
        safe_name = re.sub(r'[^a-z0-9_-]', '_', name.lower())
        filename = SECRETS_DIR / f"sa_{safe_name}.env"
        
        if filename.exists():
            filename.unlink()
            logger.info(f"🗑️ Fichier credentials supprimé: {filename}")
            return True
        else:
            logger.debug(f"Fichier credentials non trouvé (déjà supprimé?): {filename}")
            return False
    except Exception as e:
        logger.warning(f"⚠️ Impossible de supprimer le fichier credentials: {e}")
        return False


def save_service_account_credentials(name: str, client_id: str, client_secret: str, app_slugs: list = None):
    """
    Sauvegarde automatiquement les credentials d'un service account dans un fichier .env
    
    Le fichier sera créé dans /code/secrets/sa_<name>.env
    
    SÉCURITÉ: Si SECRETS_ENCRYPTION_KEY est configurée, le client_secret sera chiffré
    avec Fernet et préfixé par 'ENC:'. Sinon, il sera stocké en clair.
    """
    try:
        # Créer le répertoire si nécessaire
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        
        # Nettoyer le nom pour le fichier (slug)
        safe_name = re.sub(r'[^a-z0-9_-]', '_', name.lower())
        filename = SECRETS_DIR / f"sa_{safe_name}.env"
        
        # Chiffrer le client_secret si la clé est configurée
        encrypted_secret = encrypt_secret(client_secret)
        is_encrypted = encrypted_secret.startswith("ENC:")
        
        # Contenu du fichier
        content = f"""# Service Account: {name}
# Généré automatiquement - NE PAS COMMITER CE FICHIER !
# Ajoutez secrets/sa_*.env à votre .gitignore
"""
        if is_encrypted:
            content += "# 🔐 CLIENT_SECRET est chiffré avec Fernet\n"
        else:
            content += "# ⚠️ CLIENT_SECRET est en CLAIR - Configurez SECRETS_ENCRYPTION_KEY pour le chiffrer\n"
        
        content += f"""
CLIENT_ID={client_id}
CLIENT_SECRET={encrypted_secret}
"""
        if app_slugs:
            content += f"# Applications autorisées: {', '.join(app_slugs)}\n"
        
        filename.write_text(content)
        
        if is_encrypted:
            logger.info(f"✅ Credentials sauvegardés (chiffrés) dans {filename}")
        else:
            logger.warning(f"⚠️ Credentials sauvegardés EN CLAIR dans {filename} (configurez SECRETS_ENCRYPTION_KEY)")
        
        return str(filename)
    except Exception as e:
        logger.warning(f"⚠️ Impossible de sauvegarder les credentials: {e}")
        return None


def read_service_account_credentials(name: str) -> Optional[dict]:
    """
    Lit et déchiffre les credentials d'un service account depuis un fichier .env
    
    Returns:
        dict avec client_id et client_secret (déchiffré) ou None si non trouvé
    """
    try:
        safe_name = re.sub(r'[^a-z0-9_-]', '_', name.lower())
        filename = SECRETS_DIR / f"sa_{safe_name}.env"
        
        if not filename.exists():
            return None
        
        content = filename.read_text()
        credentials = {}
        
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("CLIENT_ID="):
                credentials["client_id"] = line.split("=", 1)[1]
            elif line.startswith("CLIENT_SECRET="):
                encrypted_secret = line.split("=", 1)[1]
                credentials["client_secret"] = decrypt_secret(encrypted_secret)
        
        return credentials if "client_id" in credentials else None
    except Exception as e:
        logger.warning(f"⚠️ Impossible de lire les credentials: {e}")
        return None


@router.get("/", response_model=List[ServiceAccountResponse])
async def list_service_accounts(
    db: AsyncSession = Depends(get_db),
    _admin = Depends(get_current_admin)
):
    """Liste tous les service accounts (admin uniquement)"""
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .order_by(ServiceAccount.name)
    )
    return result.scalars().all()


@router.get("/status")
async def get_service_accounts_status(
    db: AsyncSession = Depends(get_db),
    _admin = Depends(get_current_admin)
):
    """
    🔍 Vérifie le statut des Service Accounts (admin uniquement).
    
    Pour chaque SA, vérifie :
    - Si le fichier de credentials existe
    - Si les credentials sont valides (test d'authentification)
    """
    from datetime import datetime
    
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .order_by(ServiceAccount.name)
    )
    service_accounts = result.scalars().all()
    
    status_results = []
    
    for sa in service_accounts:
        status_data = {
            "id": sa.id,
            "name": sa.name,
            "client_id": sa.client_id,
            "is_active": sa.is_active,
            "applications": [{"id": app.id, "slug": app.slug, "name": app.name} for app in sa.applications],
            "last_used_at": sa.last_used_at.isoformat() if sa.last_used_at else None,
            "credentials_file": None,
            "credentials_status": "unknown",
            "last_check": datetime.utcnow().isoformat()
        }
        
        # Vérifier si le fichier credentials existe
        if sa.applications:
            app_slug = sa.applications[0].slug
            safe_name = re.sub(r'[^a-z0-9_-]', '_', app_slug.lower())
            credentials_path = SECRETS_DIR / f"sa_{safe_name}.env"
            
            if credentials_path.exists():
                status_data["credentials_file"] = str(credentials_path)
                status_data["credentials_status"] = "file_exists"
            else:
                status_data["credentials_status"] = "file_missing"
        else:
            status_data["credentials_status"] = "no_app"
        
        status_results.append(status_data)
    
    # Statistiques
    active_count = sum(1 for s in status_results if s["is_active"])
    file_exists_count = sum(1 for s in status_results if s["credentials_status"] == "file_exists")
    
    return {
        "service_accounts": status_results,
        "summary": {
            "total": len(status_results),
            "active": active_count,
            "inactive": len(status_results) - active_count,
            "with_credentials": file_exists_count,
            "missing_credentials": len(status_results) - file_exists_count
        },
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/{service_account_id}", response_model=ServiceAccountResponse)
async def get_service_account(
    service_account_id: int,
    db: AsyncSession = Depends(get_db),
    _admin = Depends(get_current_admin)
):
    """Récupère un service account par son ID (admin uniquement)"""
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.id == service_account_id)
    )
    sa = result.scalar_one_or_none()
    
    if not sa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service account non trouvé"
        )
    
    return sa


@router.post("/", response_model=ServiceAccountCreated, status_code=status.HTTP_201_CREATED)
async def create_service_account(
    sa_data: ServiceAccountCreate,
    db: AsyncSession = Depends(get_db),
    _admin = Depends(get_current_admin)
):
    """
    Crée un nouveau service account (admin uniquement).
    
    Les credentials sont automatiquement sauvegardés dans /code/secrets/sa_<name>.env
    """
    # Générer les credentials
    client_id = ServiceAccount.generate_client_id()
    client_secret = ServiceAccount.generate_client_secret()
    
    # Créer le service account
    sa = ServiceAccount(
        name=sa_data.name,
        description=sa_data.description,
        client_id=client_id,
        client_secret_hash=get_password_hash(client_secret)
    )
    
    # Assigner les applications si spécifiées
    app_slugs = []
    if sa_data.app_ids:
        result = await db.execute(
            select(Application).where(Application.id.in_(sa_data.app_ids))
        )
        apps = result.scalars().all()
        sa.applications = list(apps)
        app_slugs = [app.slug for app in apps]
    
    # Ajouter automatiquement les applications partagées (is_shared_service=True)
    shared_result = await db.execute(
        select(Application).where(Application.is_shared_service == True)
    )
    shared_apps = shared_result.scalars().all()
    for shared_app in shared_apps:
        if shared_app not in sa.applications:
            sa.applications.append(shared_app)
            app_slugs.append(shared_app.slug)
    
    db.add(sa)
    await db.commit()
    await db.refresh(sa)
    
    # 💾 Sauvegarder automatiquement les credentials
    secrets_file = save_service_account_credentials(
        name=sa_data.name,
        client_id=client_id,
        client_secret=client_secret,
        app_slugs=app_slugs
    )
    
    # Recharger avec les relations
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.id == sa.id)
    )
    sa = result.scalar_one()
    
    # Notifier les clients SSE du nouveau service account
    notify_event("service_accounts_updated", {"action": "created", "sa_id": sa.id, "sa_name": sa.name})
    notify_stats_update()
    
    # Retourner avec le secret en clair (une seule fois!)
    return ServiceAccountCreated(
        id=sa.id,
        name=sa.name,
        description=sa.description,
        client_id=sa.client_id,
        client_secret=client_secret,
        secrets_file=secrets_file,  # 📁 Indique où les credentials sont sauvegardés
        is_active=sa.is_active,
        created_at=sa.created_at,
        last_used_at=sa.last_used_at,
        applications=[
            {"id": app.id, "name": app.name, "slug": app.slug, "icon": app.icon, "color": app.color}
            for app in sa.applications
        ]
    )


@router.put("/{service_account_id}", response_model=ServiceAccountResponse)
async def update_service_account(
    service_account_id: int,
    sa_data: ServiceAccountUpdate,
    db: AsyncSession = Depends(get_db),
    _admin = Depends(get_current_admin)
):
    """Met à jour un service account (admin uniquement)"""
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.id == service_account_id)
    )
    sa = result.scalar_one_or_none()
    
    if not sa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service account non trouvé"
        )
    
    update_data = sa_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(sa, field, value)
    
    await db.commit()
    await db.refresh(sa)
    
    # Notifier les clients SSE de la modification
    notify_event("service_accounts_updated", {"action": "updated", "sa_id": sa.id, "sa_name": sa.name})
    
    return sa


@router.delete("/{service_account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service_account(
    service_account_id: int,
    db: AsyncSession = Depends(get_db),
    _admin = Depends(get_current_admin)
):
    """Supprime un service account (admin uniquement)"""
    result = await db.execute(
        select(ServiceAccount).where(ServiceAccount.id == service_account_id)
    )
    sa = result.scalar_one_or_none()
    
    if not sa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service account non trouvé"
        )
    
    sa_name = sa.name  # Sauvegarder avant suppression
    await db.delete(sa)
    await db.commit()
    
    # Notifier les clients SSE de la suppression
    notify_event("service_accounts_updated", {"action": "deleted", "sa_id": service_account_id, "sa_name": sa_name})
    notify_stats_update()


@router.post("/{service_account_id}/regenerate-secret", response_model=ServiceAccountCreated)
async def regenerate_client_secret(
    service_account_id: int,
    db: AsyncSession = Depends(get_db),
    _admin = Depends(get_current_admin)
):
    """
    Régénère le client_secret d'un service account (admin uniquement).
    
    ⚠️ L'ancien secret sera immédiatement invalidé !
    ⚠️ Le nouveau secret est retourné UNE SEULE FOIS.
    ⚠️ Le fichier sa_*.env est automatiquement mis à jour.
    """
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.id == service_account_id)
    )
    sa = result.scalar_one_or_none()
    
    if not sa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service account non trouvé"
        )
    
    # Générer un nouveau secret
    new_secret = ServiceAccount.generate_client_secret()
    sa.client_secret_hash = get_password_hash(new_secret)
    
    await db.commit()
    
    # Mettre à jour le fichier de credentials si l'application principale existe
    secrets_file = None
    if sa.applications:
        # Utiliser le slug de la première application comme nom de fichier
        app_slug = sa.applications[0].slug
        app_slugs = [app.slug for app in sa.applications]
        secrets_file = save_service_account_credentials(
            name=app_slug,
            client_id=sa.client_id,
            client_secret=new_secret,
            app_slugs=app_slugs
        )
        logger.info(f"🔄 Secret régénéré pour SA '{sa.name}', fichier mis à jour: {secrets_file}")
    
    return ServiceAccountCreated(
        id=sa.id,
        name=sa.name,
        description=sa.description,
        client_id=sa.client_id,
        client_secret=new_secret,  # ⚠️ Affiché une seule fois!
        secrets_file=secrets_file,  # 📁 Fichier mis à jour
        is_active=sa.is_active,
        created_at=sa.created_at,
        last_used_at=sa.last_used_at,
        applications=[
            {"id": app.id, "name": app.name, "slug": app.slug, "icon": app.icon, "color": app.color}
            for app in sa.applications
        ]
    )


@router.put("/{service_account_id}/apps")
async def update_service_account_apps(
    service_account_id: int,
    app_ids: List[int],
    db: AsyncSession = Depends(get_db),
    _admin = Depends(get_current_admin)
):
    """Met à jour les applications autorisées pour un service account (admin uniquement)"""
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.id == service_account_id)
    )
    sa = result.scalar_one_or_none()
    
    if not sa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service account non trouvé"
        )
    
    # Récupérer les applications
    result = await db.execute(
        select(Application).where(Application.id.in_(app_ids))
    )
    apps = result.scalars().all()
    
    sa.applications = list(apps)
    await db.commit()
    
    return {
        "message": "Applications mises à jour",
        "apps": [{"id": app.id, "name": app.name, "slug": app.slug} for app in sa.applications]
    }
