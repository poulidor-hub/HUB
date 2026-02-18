"""
Discovery API — Explorateur d'APIs pour administrateurs

Permet aux admins d'explorer les endpoints des applications enregistrées
via un proxy backend (élimine les problèmes CORS et la gestion manuelle
de tokens M2M côté client).

Architecture :
- Le backend crée/réutilise un Service Account interne "hub-discovery"
- Les requêtes admin passent par le proxy qui injecte le token M2M
- La spec OpenAPI est récupérée côté serveur
"""

import time
import re
import logging
from typing import Optional, List
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import (
    get_current_admin,
    get_password_hash,
    verify_password,
    create_access_token,
)
from app.database import get_db, async_session_maker
from app.models import (
    Application,
    ServiceAccount,
    service_account_app_access,
    User,
)
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/discovery", tags=["discovery"])

# ─── Constantes ───────────────────────────────────────────────────────
DISCOVERY_SA_NAME = "hub-discovery"
DISCOVERY_SA_DESCRIPTION = "Service Account interne utilisé par l'explorateur Discovery (géré automatiquement)"
OPENAPI_PATHS = ["/openapi.json", "/api/openapi.json", "/docs/openapi.json", "/swagger.json"]
PROXY_TIMEOUT = 30  # secondes
PROXY_MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5 Mo

# Cache en mémoire pour le token M2M du SA discovery
_discovery_token: Optional[str] = None
_discovery_token_expiry: float = 0  # timestamp epoch


# ─── Schemas ──────────────────────────────────────────────────────────

class DiscoveryAppItem(BaseModel):
    """Application visible dans l'explorateur Discovery"""
    id: int
    slug: str
    name: str
    description: Optional[str] = None
    api_url: str
    is_shared_service: bool = False
    is_active: bool = True
    is_remote: bool = False
    icon: Optional[str] = None
    color: Optional[str] = None

    class Config:
        from_attributes = True


class DiscoveryAppsResponse(BaseModel):
    apps: List[DiscoveryAppItem]
    total: int


class ProxyRequest(BaseModel):
    """Requête à relayer vers une app cible"""
    app_slug: str = Field(..., description="Slug de l'application cible")
    method: str = Field("GET", description="Méthode HTTP (GET, POST, PUT, PATCH, DELETE)")
    path: str = Field("/", description="Chemin de l'endpoint sur l'app cible")
    query_params: Optional[dict] = Field(None, description="Paramètres de requête")
    headers: Optional[dict] = Field(None, description="Headers supplémentaires")
    body: Optional[dict] = Field(None, description="Corps de la requête (JSON)")


class ProxyResponse(BaseModel):
    """Réponse du proxy"""
    status: int
    status_text: str = ""
    headers: dict = {}
    body: Optional[object] = None
    body_text: Optional[str] = None
    elapsed_ms: int = 0
    error: Optional[str] = None


# ─── Helpers internes ─────────────────────────────────────────────────

async def _ensure_discovery_sa(db: AsyncSession) -> ServiceAccount:
    """
    Crée ou récupère le Service Account interne hub-discovery.
    Ce SA a accès à toutes les applications pour pouvoir les explorer.
    """
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.name == DISCOVERY_SA_NAME)
    )
    sa = result.scalar_one_or_none()

    if sa is None:
        # Créer le SA
        client_id = ServiceAccount.generate_client_id()
        client_secret = ServiceAccount.generate_client_secret()

        sa = ServiceAccount(
            name=DISCOVERY_SA_NAME,
            client_id=client_id,
            client_secret_hash=get_password_hash(client_secret),
            description=DISCOVERY_SA_DESCRIPTION,
            is_active=True,
        )
        db.add(sa)
        await db.flush()

        # Sauvegarder le secret dans le fichier pour pouvoir le relire
        try:
            from app.routes.service_accounts import save_service_account_credentials
            save_service_account_credentials(DISCOVERY_SA_NAME, client_id, client_secret)
        except Exception as e:
            logger.warning(f"⚠️ Impossible de sauvegarder les credentials discovery: {e}")

        # Associer toutes les applications actives
        await _sync_discovery_sa_apps(db, sa)

        await db.commit()
        logger.info(f"✅ Service Account Discovery créé: {client_id}")

    return sa


async def _sync_discovery_sa_apps(db: AsyncSession, sa: ServiceAccount):
    """
    S'assure que le SA discovery a accès à toutes les apps actives.
    Interroge la table de jointure directement pour éviter les problèmes
    de lazy='raise' sur la relation ServiceAccount.applications.
    """
    # Toutes les apps actives
    result = await db.execute(
        select(Application.id).where(Application.is_active == True)
    )
    all_app_ids = {row[0] for row in result.all()}

    # Apps déjà associées au SA (via la table de jointure)
    result = await db.execute(
        select(service_account_app_access.c.app_id).where(
            service_account_app_access.c.service_account_id == sa.id
        )
    )
    current_app_ids = {row[0] for row in result.all()}

    # Ajouter les apps manquantes
    missing = all_app_ids - current_app_ids
    for app_id in missing:
        await db.execute(
            service_account_app_access.insert().values(
                service_account_id=sa.id, app_id=app_id
            )
        )

    await db.flush()


async def _get_discovery_token(db: AsyncSession) -> str:
    """
    Obtient ou renouvelle le token M2M du SA discovery.
    Utilise un cache en mémoire pour éviter de régénérer à chaque requête.
    """
    global _discovery_token, _discovery_token_expiry

    # Token encore valide ? (marge de 60s)
    if _discovery_token and time.time() < (_discovery_token_expiry - 60):
        return _discovery_token

    # S'assurer que le SA existe et a accès à toutes les apps
    sa = await _ensure_discovery_sa(db)

    # Re-synchroniser les apps (au cas où de nouvelles apps ont été ajoutées)
    await _sync_discovery_sa_apps(db, sa)
    await db.commit()

    # Récupérer les apps associées via la table de jointure (évite lazy='raise')
    result = await db.execute(
        select(Application.slug)
        .join(service_account_app_access, Application.id == service_account_app_access.c.app_id)
        .where(service_account_app_access.c.service_account_id == sa.id)
    )
    apps_dict = {row[0]: "service" for row in result.all()}

    token, expire, jti = create_access_token(
        username=sa.client_id,
        roles=["service_account"],
        apps=apps_dict,
        expires_delta=timedelta(hours=1),
        is_service_account=True,
    )

    _discovery_token = token
    _discovery_token_expiry = expire.timestamp() if hasattr(expire, 'timestamp') else time.time() + 3600

    logger.debug(f"🔑 Token Discovery M2M renouvelé (expire: {expire})")
    return _discovery_token


def _get_httpx_client():
    """Retourne un client httpx pour les appels proxy."""
    import httpx
    return httpx.AsyncClient(
        timeout=httpx.Timeout(PROXY_TIMEOUT),
        verify=False,  # Réseau interne, pas de vérification TLS
        follow_redirects=True,
        max_redirects=5,
    )


# ─── Routes ──────────────────────────────────────────────────────────

@router.get("/apps", response_model=DiscoveryAppsResponse)
async def get_discovery_apps(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Liste toutes les applications actives pour l'explorateur Discovery.
    Réservé aux administrateurs.
    """
    result = await db.execute(
        select(Application)
        .where(Application.is_active == True)
        .order_by(Application.name)
    )
    apps = result.scalars().all()

    items = [
        DiscoveryAppItem(
            id=app.id,
            slug=app.slug,
            name=app.name,
            description=app.description,
            api_url=app.api_url,
            is_shared_service=app.is_shared_service,
            is_active=app.is_active,
            is_remote=getattr(app, "is_remote", False) or False,
            icon=app.icon,
            color=app.color,
        )
        for app in apps
    ]

    return DiscoveryAppsResponse(apps=items, total=len(items))


@router.get("/apps/{slug}/openapi")
async def get_app_openapi(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Récupère la spécification OpenAPI d'une application cible via proxy backend.
    Élimine les problèmes CORS en passant par le serveur.
    """
    # Trouver l'application
    result = await db.execute(
        select(Application).where(Application.slug == slug, Application.is_active == True)
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"Application '{slug}' non trouvée")

    # Obtenir le token M2M
    token = await _get_discovery_token(db)
    base_url = app.api_url.rstrip("/")

    async with _get_httpx_client() as client:
        for path in OPENAPI_PATHS:
            try:
                url = base_url + path
                resp = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
                if resp.status_code == 200:
                    try:
                        spec = resp.json()
                        logger.info(f"✅ Spec OpenAPI chargée pour {slug} depuis {path}")
                        return {"spec": spec, "source": path}
                    except Exception:
                        continue
            except Exception as e:
                logger.debug(f"OpenAPI tentative {path} pour {slug}: {e}")
                continue

    # Aucune spec trouvée — pas une erreur fatale
    return {"spec": None, "source": None, "message": f"Aucune spécification OpenAPI trouvée pour '{slug}'"}


@router.post("/proxy", response_model=ProxyResponse)
async def proxy_request(
    req: ProxyRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Proxy une requête vers une application cible.
    
    Le backend :
    1. Résout l'api_url depuis le slug
    2. Injecte le token M2M du SA discovery
    3. Exécute la requête via httpx
    4. Retourne le résultat (status, headers, body)
    """
    # Valider la méthode
    method = req.method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        raise HTTPException(status_code=400, detail=f"Méthode HTTP invalide: {method}")

    # Trouver l'application
    result = await db.execute(
        select(Application).where(Application.slug == req.app_slug, Application.is_active == True)
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"Application '{req.app_slug}' non trouvée")

    # Obtenir le token M2M
    token = await _get_discovery_token(db)

    # Construire l'URL
    base_url = app.api_url.rstrip("/")
    path = req.path if req.path.startswith("/") else f"/{req.path}"
    url = base_url + path

    # Headers
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if req.headers:
        # Ne pas écraser Authorization
        for k, v in req.headers.items():
            if k.lower() != "authorization":
                headers[k] = v

    # Exécuter la requête
    start = time.time()
    try:
        async with _get_httpx_client() as client:
            resp = await client.request(
                method=method,
                url=url,
                params=req.query_params,
                headers=headers,
                json=req.body if req.body and method in ("POST", "PUT", "PATCH") else None,
            )

        elapsed_ms = int((time.time() - start) * 1000)

        # Limiter la taille de la réponse
        content = resp.content
        if len(content) > PROXY_MAX_RESPONSE_SIZE:
            return ProxyResponse(
                status=resp.status_code,
                status_text=resp.reason_phrase or "",
                headers=dict(resp.headers),
                body=None,
                body_text=f"[Réponse trop volumineuse: {len(content)} octets, limite: {PROXY_MAX_RESPONSE_SIZE}]",
                elapsed_ms=elapsed_ms,
            )

        # Parser le body
        body = None
        body_text = None
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = resp.json()
            except Exception:
                body_text = resp.text
        else:
            body_text = resp.text

        # Filtrer les headers sensibles pour la réponse
        safe_headers = {}
        for k, v in resp.headers.items():
            if k.lower() not in ("set-cookie", "authorization"):
                safe_headers[k] = v

        return ProxyResponse(
            status=resp.status_code,
            status_text=resp.reason_phrase or "",
            headers=safe_headers,
            body=body,
            body_text=body_text,
            elapsed_ms=elapsed_ms,
        )

    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.warning(f"⚠️ Erreur proxy vers {req.app_slug}{path}: {e}")
        return ProxyResponse(
            status=0,
            status_text="Erreur de connexion",
            elapsed_ms=elapsed_ms,
            error=str(e),
        )


# ─── Startup helper ──────────────────────────────────────────────────

async def init_discovery_service_account():
    """
    Appelée au démarrage de l'application pour s'assurer
    que le SA discovery existe. Non bloquante.
    """
    try:
        async with async_session_maker() as db:
            await _ensure_discovery_sa(db)
            await db.commit()
            logger.info("✅ Service Account Discovery prêt")
    except Exception as e:
        logger.warning(f"⚠️ Impossible d'initialiser le SA Discovery: {e}")
