"""
Routes de gestion des Service Accounts (M2M Authentication)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List
from pathlib import Path
import re
import logging

from app.database import get_db
from app.models import ServiceAccount, Application
from app.schemas import (
    ServiceAccountCreate,
    ServiceAccountUpdate,
    ServiceAccountResponse,
    ServiceAccountCreated
)
from app.auth import get_current_admin, get_password_hash

router = APIRouter(prefix="/api/service-accounts", tags=["Service Accounts"])

# Répertoire des secrets pour les service accounts (en écriture)
SECRETS_DIR = Path("/code/secrets/service_accounts")
logger = logging.getLogger(__name__)


def save_service_account_credentials(name: str, client_id: str, client_secret: str, app_slugs: list = None):
    """
    Sauvegarde automatiquement les credentials d'un service account dans un fichier .env
    
    Le fichier sera créé dans /code/secrets/sa_<name>.env
    """
    try:
        # Créer le répertoire si nécessaire
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        
        # Nettoyer le nom pour le fichier (slug)
        safe_name = re.sub(r'[^a-z0-9_-]', '_', name.lower())
        filename = SECRETS_DIR / f"sa_{safe_name}.env"
        
        # Contenu du fichier
        content = f"""# Service Account: {name}
# Généré automatiquement - NE PAS COMMITER CE FICHIER !
# Ajoutez secrets/sa_*.env à votre .gitignore

CLIENT_ID={client_id}
CLIENT_SECRET={client_secret}
"""
        if app_slugs:
            content += f"# Applications autorisées: {', '.join(app_slugs)}\n"
        
        filename.write_text(content)
        logger.info(f"✅ Credentials sauvegardés dans {filename}")
        return str(filename)
    except Exception as e:
        logger.warning(f"⚠️ Impossible de sauvegarder les credentials: {e}")
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
    
    await db.delete(sa)
    await db.commit()


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
    
    return ServiceAccountCreated(
        id=sa.id,
        name=sa.name,
        description=sa.description,
        client_id=sa.client_id,
        client_secret=new_secret,  # ⚠️ Affiché une seule fois!
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
