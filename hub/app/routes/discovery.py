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


class M2MEndpointParam(BaseModel):
    """Paramètre d'un endpoint M2M"""
    name: str
    location: str = "query"  # query, path, header
    type: str = "string"
    required: bool = False
    description: str = ""


class M2MEndpointDetail(BaseModel):
    """Détail d'un endpoint pour le setup M2M"""
    method: str
    path: str
    summary: str = ""
    description: str = ""
    tags: List[str] = []
    parameters: List[M2MEndpointParam] = []
    request_body_schema: Optional[dict] = None


class M2MSetupResponse(BaseModel):
    """Réponse complète du setup M2M pour une app"""
    app_slug: str
    app_name: str
    app_description: str = ""
    api_url: str
    hub_url: str
    token_url: str
    endpoints: List[M2MEndpointDetail] = []
    markdown: str


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
    Récupère la spécification OpenAPI d'une application cible.
    
    Stratégie (priorité décroissante) :
    1. Spec stockée en base (poussée par l'app via POST /me/openapi ou auto-registration)
    2. Fetch live depuis l'app (/openapi.json, /api/openapi.json, etc.)
    
    La spec stockée est déjà filtrée (endpoints techniques exclus).
    La spec fetchée en live est filtrée automatiquement avant retour.
    """
    from app.routes.apps import filter_openapi_spec

    # Trouver l'application
    result = await db.execute(
        select(Application).where(Application.slug == slug, Application.is_active == True)
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"Application '{slug}' non trouvée")

    # 1. Priorité : spec stockée en base
    if app.api_endpoints and isinstance(app.api_endpoints, dict) and app.api_endpoints.get("paths"):
        logger.info(f"✅ Spec OpenAPI pour {slug} depuis la base (poussée le {app.api_endpoints_updated_at})")
        return {
            "spec": app.api_endpoints,
            "source": "stored",
            "updated_at": str(app.api_endpoints_updated_at) if app.api_endpoints_updated_at else None,
        }

    # 2. Fallback : fetch live depuis l'app
    try:
        token = await _get_discovery_token(db)
    except Exception as e:
        logger.warning(f"⚠️ Impossible d'obtenir le token discovery pour {slug}: {e}")
        return {"spec": None, "source": None, "message": f"Impossible d'obtenir le token discovery: {e}"}

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
                        raw_spec = resp.json()
                        # Filtrer les endpoints techniques
                        filtered_spec, total_ep, filtered_ep = filter_openapi_spec(raw_spec)
                        logger.info(
                            f"✅ Spec OpenAPI fetchée pour {slug} depuis {path}: "
                            f"{total_ep - filtered_ep} endpoints métier, {filtered_ep} techniques exclus"
                        )
                        return {"spec": filtered_spec, "source": path}
                    except Exception:
                        continue
            except Exception as e:
                logger.debug(f"OpenAPI tentative {path} pour {slug}: {e}")
                continue

    # Aucune spec trouvée — pas une erreur fatale
    return {"spec": None, "source": None, "message": f"Aucune spécification OpenAPI trouvée pour '{slug}'"}


# ─── M2M Setup ────────────────────────────────────────────────────────

def _extract_endpoints_from_spec(spec: dict) -> List[M2MEndpointDetail]:
    """Extrait les endpoints détaillés depuis une spec OpenAPI filtrée."""
    endpoints = []
    if not spec or "paths" not in spec:
        return endpoints

    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete", "head", "options"):
                continue
            if not isinstance(details, dict):
                continue

            params = []
            for p in details.get("parameters", []):
                params.append(M2MEndpointParam(
                    name=p.get("name", ""),
                    location=p.get("in", "query"),
                    type=_resolve_param_type(p.get("schema", {})),
                    required=p.get("required", False),
                    description=p.get("description", ""),
                ))

            request_body_schema = None
            rb = details.get("requestBody", {})
            if rb:
                content = rb.get("content", {})
                json_schema = content.get("application/json", {}).get("schema")
                if json_schema:
                    request_body_schema = _resolve_schema(json_schema, spec)

            endpoints.append(M2MEndpointDetail(
                method=method.upper(),
                path=path,
                summary=details.get("summary", ""),
                description=details.get("description", ""),
                tags=details.get("tags", []),
                parameters=params,
                request_body_schema=request_body_schema,
            ))

    # Trier par path puis par méthode
    method_order = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4, "HEAD": 5, "OPTIONS": 6}
    endpoints.sort(key=lambda e: (e.path, method_order.get(e.method, 9)))
    return endpoints


def _resolve_param_type(schema: dict) -> str:
    """Résout le type d'un paramètre depuis son schema OpenAPI."""
    if not schema:
        return "string"
    t = schema.get("type", "")
    fmt = schema.get("format", "")
    if fmt:
        return f"{t} ({fmt})"
    return t or "string"


def _resolve_schema(schema: dict, spec: dict, depth: int = 0) -> dict:
    """Résout les $ref dans un schema OpenAPI (profondeur limitée)."""
    if depth > 5 or not isinstance(schema, dict):
        return schema

    ref = schema.get("$ref")
    if ref and isinstance(ref, str):
        parts = ref.lstrip("#/").split("/")
        resolved = spec
        for part in parts:
            if isinstance(resolved, dict):
                resolved = resolved.get(part, {})
            else:
                return schema
        return _resolve_schema(resolved, spec, depth + 1) if isinstance(resolved, dict) else schema

    # Résoudre les propriétés imbriquées
    result = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {k: _resolve_schema(v, spec, depth + 1) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            result[key] = _resolve_schema(value, spec, depth + 1)
        elif key == "allOf" and isinstance(value, list):
            merged = {}
            for item in value:
                resolved_item = _resolve_schema(item, spec, depth + 1)
                if isinstance(resolved_item, dict):
                    for k, v in resolved_item.items():
                        if k == "properties" and "properties" in merged:
                            merged["properties"].update(v)
                        else:
                            merged[k] = v
            result.update(merged)
        else:
            result[key] = value

    return result


def _generate_body_example(schema: dict) -> str:
    """Génère un exemple JSON minimal depuis un schema résolu."""
    if not schema:
        return "{}"
    props = schema.get("properties", {})
    if not props:
        return "{}"
    example = {}
    required_fields = set(schema.get("required", []))
    for name, prop in props.items():
        t = prop.get("type", "string")
        desc = prop.get("description", "")
        if t == "string":
            example[name] = desc if desc else f"<{name}>"
        elif t == "integer":
            example[name] = 0
        elif t == "number":
            example[name] = 0.0
        elif t == "boolean":
            example[name] = True
        elif t == "array":
            example[name] = []
        elif t == "object":
            example[name] = {}
        else:
            example[name] = f"<{name}>"
    import json
    return json.dumps(example, indent=2, ensure_ascii=False)


def _generate_m2m_markdown(
    app_slug: str,
    app_name: str,
    app_description: str,
    api_url: str,
    hub_url: str,
    token_url: str,
    endpoints: List[M2MEndpointDetail],
) -> str:
    """Génère le bloc Markdown complet pour le setup M2M."""
    lines = []

    # ─── En-tête ─────────────────────────────────────────────────
    lines.append(f"# M2M Setup — {app_name} ({app_slug})")
    lines.append("")
    if app_description:
        lines.append(f"> {app_description}")
        lines.append("")

    # ─── Informations de connexion ──────────────────────────────
    lines.append("## Informations de connexion")
    lines.append("")
    lines.append(f"- **HUB URL** : `{hub_url}`")
    lines.append(f"- **Token endpoint** : `POST {token_url}`")
    lines.append(f"- **API cible** : `{api_url}`")
    lines.append(f"- **App slug** : `{app_slug}`")
    lines.append("")

    # ─── Authentification M2M ───────────────────────────────────
    lines.append("## Authentification M2M")
    lines.append("")
    lines.append("```python")
    lines.append("import httpx")
    lines.append("")
    lines.append("# Remplacez par vos credentials de service account")
    lines.append('CLIENT_ID = "<votre_client_id>"')
    lines.append('CLIENT_SECRET = "<votre_client_secret>"')
    lines.append(f'HUB_URL = "{hub_url}"')
    lines.append(f'API_URL = "{api_url}"')
    lines.append("")
    lines.append("# Obtenir un token M2M")
    lines.append('token_resp = httpx.post(f"{HUB_URL}/api/auth/token", data={')
    lines.append('    "grant_type": "client_credentials",')
    lines.append('    "client_id": CLIENT_ID,')
    lines.append('    "client_secret": CLIENT_SECRET,')
    lines.append("})")
    lines.append("token_resp.raise_for_status()")
    lines.append('token = token_resp.json()["access_token"]')
    lines.append('headers = {"Authorization": f"Bearer {token}"}')
    lines.append("```")
    lines.append("")

    # ─── Endpoints disponibles ──────────────────────────────────
    if not endpoints:
        lines.append("## Endpoints disponibles")
        lines.append("")
        lines.append("_Aucun endpoint documenté pour cette application._")
        lines.append("L'application n'a pas encore poussé sa spécification OpenAPI au Hub.")
        lines.append("")
        lines.append("Vous pouvez quand même appeler l'API manuellement :")
        lines.append("")
        lines.append("```python")
        lines.append(f'resp = httpx.get(f"{{API_URL}}/api/...", headers=headers)')
        lines.append("print(resp.json())")
        lines.append("```")
        lines.append("")
    else:
        lines.append(f"## Endpoints disponibles ({len(endpoints)})")
        lines.append("")

        # Grouper par tag
        tagged: dict[str, list[M2MEndpointDetail]] = {}
        for ep in endpoints:
            tag = ep.tags[0] if ep.tags else "Général"
            tagged.setdefault(tag, []).append(ep)

        for tag, tag_endpoints in tagged.items():
            lines.append(f"### {tag}")
            lines.append("")

            for ep in tag_endpoints:
                lines.append(f"#### `{ep.method} {ep.path}`")
                if ep.summary:
                    lines.append(f"_{ep.summary}_")
                if ep.description and ep.description != ep.summary:
                    lines.append(f"\n{ep.description}")
                lines.append("")

                # Paramètres
                params_query = [p for p in ep.parameters if p.location == "query"]
                params_path = [p for p in ep.parameters if p.location == "path"]

                if params_path:
                    lines.append("**Paramètres de chemin :**")
                    lines.append("")
                    lines.append("| Nom | Type | Description |")
                    lines.append("|-----|------|-------------|")
                    for p in params_path:
                        lines.append(f"| `{p.name}` | {p.type} | {p.description} |")
                    lines.append("")

                if params_query:
                    lines.append("**Paramètres query :**")
                    lines.append("")
                    lines.append("| Nom | Type | Requis | Description |")
                    lines.append("|-----|------|--------|-------------|")
                    for p in params_query:
                        requis = "✅" if p.required else ""
                        lines.append(f"| `{p.name}` | {p.type} | {requis} | {p.description} |")
                    lines.append("")

                # Body
                if ep.request_body_schema:
                    body_example = _generate_body_example(ep.request_body_schema)
                    lines.append("**Body (JSON) :**")
                    lines.append("")
                    lines.append("```json")
                    lines.append(body_example)
                    lines.append("```")
                    lines.append("")

                # Exemple d'appel
                lines.append("**Exemple :**")
                lines.append("")
                lines.append("```python")
                method_lower = ep.method.lower()
                path_fmt = ep.path.replace("{", "{").replace("}", "}")
                if ep.request_body_schema:
                    lines.append(f'resp = httpx.{method_lower}(f"{{API_URL}}{path_fmt}", headers=headers, json={{')
                    body_props = ep.request_body_schema.get("properties", {})
                    for pname in list(body_props.keys())[:3]:
                        lines.append(f'    "{pname}": ...,')
                    lines.append("})")
                elif params_query:
                    qparams = ", ".join(f'"{p.name}": ...' for p in params_query[:3])
                    lines.append(f'resp = httpx.{method_lower}(f"{{API_URL}}{path_fmt}", headers=headers, params={{{qparams}}})')
                else:
                    lines.append(f'resp = httpx.{method_lower}(f"{{API_URL}}{path_fmt}", headers=headers)')
                lines.append("print(resp.json())")
                lines.append("```")
                lines.append("")

    # ─── Notes ──────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Le token M2M expire au bout de **15 minutes** par défaut. Rafraîchissez-le avant expiration.")
    lines.append("- Les credentials `CLIENT_ID` / `CLIENT_SECRET` sont ceux de votre **Service Account** (à demander à l'administrateur du Hub).")
    lines.append(f"- Votre Service Account doit avoir accès à l'application `{app_slug}`.")
    lines.append(f"- L'API cible (`{api_url}`) doit être accessible depuis votre réseau.")
    lines.append("")

    return "\n".join(lines)


@router.get("/apps/{slug}/m2m-setup", response_model=M2MSetupResponse)
async def get_m2m_setup(
    slug: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Génère un bloc Markdown complet pour le setup M2M avec une app cible.
    
    Contient : informations de connexion, code d'authentification Python,
    liste des endpoints avec paramètres et exemples d'appel.
    
    Destiné au copier-coller vers un IA coder ou un fichier de documentation.
    Réservé aux administrateurs.
    """
    from app.routes.apps import filter_openapi_spec

    # Trouver l'application
    result = await db.execute(
        select(Application).where(Application.slug == slug, Application.is_active == True)
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"Application '{slug}' non trouvée")

    # Construire les URLs
    hub_host = settings.get_hub_host()
    hub_url = f"http://{hub_host}:8000"
    token_url = f"{hub_url}/api/auth/token"

    # Charger la spec OpenAPI (même logique que /openapi)
    spec = None
    if app.api_endpoints and isinstance(app.api_endpoints, dict) and app.api_endpoints.get("paths"):
        spec = app.api_endpoints
    else:
        # Fallback : fetch live
        try:
            token = await _get_discovery_token(db)
            base_url = app.api_url.rstrip("/")
            async with _get_httpx_client() as client:
                for path in OPENAPI_PATHS:
                    try:
                        resp = await client.get(
                            base_url + path,
                            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                        )
                        if resp.status_code == 200:
                            raw_spec = resp.json()
                            spec, _, _ = filter_openapi_spec(raw_spec)
                            break
                    except Exception:
                        continue
        except Exception as e:
            logger.debug(f"Pas de spec OpenAPI pour {slug}: {e}")

    # Extraire les endpoints
    endpoints = _extract_endpoints_from_spec(spec) if spec else []

    # Générer le Markdown
    markdown = _generate_m2m_markdown(
        app_slug=app.slug,
        app_name=app.name,
        app_description=app.description or "",
        api_url=app.api_url,
        hub_url=hub_url,
        token_url=token_url,
        endpoints=endpoints,
    )

    return M2MSetupResponse(
        app_slug=app.slug,
        app_name=app.name,
        app_description=app.description or "",
        api_url=app.api_url,
        hub_url=hub_url,
        token_url=token_url,
        endpoints=endpoints,
        markdown=markdown,
    )


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
