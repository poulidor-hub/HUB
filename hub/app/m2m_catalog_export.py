# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Agrégation du catalogue M2M du parc, côté Hub.

L'endpoint GET /api/apps/m2m-catalog/export (admin) appelle build_m2m_catalog_export()
qui, pour chaque app partagée :
  1. tente GET <api_url>/api/m2m/catalog (token SA minté par le Hub) -> tagging fiable
  2. sinon GET <api_url>/api/openapi.json -> heuristique (tagging approximatif)
  3. sinon marque l'app unreachable

Retourne un dict prêt à sérialiser en JSON (le blueprint).
"""
import asyncio
import logging
from datetime import timedelta
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token
from app.models import Application

logger = logging.getLogger(__name__)

HTTP_METHODS = ("get", "post", "put", "delete", "patch")
EXCLUDED_PATHS = {
    "/docs", "/redoc", "/openapi.json", "/api/openapi.json",
    "/api/m2m/catalog",
}
FETCH_TIMEOUT = 5.0  # secondes par app

M2M_USAGE_NOTES = {
    "description": "Comment consommer ces endpoints en M2M",
    "auth_mechanism": "OAuth2 client_credentials (token obtenu via hub_client.get_m2m_token())",
    "required_headers": [
        "Authorization: Bearer <m2m_token>",
        "X-Slug: <caller_app_slug>",
    ],
    "caller_method": "hub_client.call_discovered_service(slug, endpoint, method, json=...)",
    "key_rule": (
        "Seuls les endpoints avec auth_level='service' sont atteignables en M2M "
        "(Service Accounts). Les autres nécessitent un token utilisateur."
    ),
}


def _normalize_request_body(op):
    rb = op.get("requestBody")
    if not rb:
        return None
    json_media = rb.get("content", {}).get("application/json", {})
    return {"required": rb.get("required", False), "content_type": "application/json", "schema": json_media.get("schema")}


def _normalize_responses(op):
    out = {}
    for code, resp in op.get("responses", {}).items():
        out[code] = {"description": resp.get("description", ""), "schema": resp.get("content", {}).get("application/json", {}).get("schema")}
    return out


def apply_heuristic(openapi_spec: dict, slug: str) -> dict:
    """
    Transforme un spec OpenAPI brut en catalogue app avec tagging heuristique.

    Limites assumées (introspection_source = 'openapi-heuristic') :
      - path sous /api/m2m/ -> 'service'
      - operation a 'security' OU spec global a 'security' -> 'user'
      - sinon -> 'public'
    Ne distingue pas viewer/user/manager/admin.
    """
    global_security = openapi_spec.get("security")
    endpoints = []
    for path, methods in openapi_spec.get("paths", {}).items():
        if path in EXCLUDED_PATHS:
            continue
        for method, op in methods.items():
            if method not in HTTP_METHODS:
                continue
            if path.startswith("/api/m2m/"):
                level = "service"
            elif op.get("security") or global_security:
                level = "user"
            else:
                level = "public"
            endpoints.append({
                "path": path,
                "method": method.upper(),
                "summary": op.get("summary"),
                "description": op.get("description"),
                "auth_level": level,
                "tags": op.get("tags", []),
                "path_params": [],
                "query_params": [],
                "request_body": _normalize_request_body(op),
                "responses": _normalize_responses(op),
            })
    return {
        "slug": slug,
        "introspection_source": "openapi-heuristic",
        "endpoints": endpoints,
    }


async def _fetch_app_catalog(
    client: httpx.AsyncClient, api_url: str, slug: str
) -> Optional[dict]:
    """Tente GET <api_url>/api/m2m/catalog avec un token SA minté par le Hub."""
    token, _, _ = create_access_token(
        username=f"hub-catalog-{slug}",
        apps={slug: "service"},
        expires_delta=timedelta(minutes=5),
        is_service_account=True,
    )
    try:
        resp = await client.get(
            f"{api_url.rstrip('/')}/api/m2m/catalog",
            headers={"Authorization": f"Bearer {token}"},
            timeout=FETCH_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except (httpx.HTTPError, ValueError):
        return None


async def _fetch_app_openapi(
    client: httpx.AsyncClient, api_url: str
) -> Optional[dict]:
    """Tente GET <api_url>/api/openapi.json (public, pas d'auth)."""
    try:
        resp = await client.get(
            f"{api_url.rstrip('/')}/api/openapi.json", timeout=FETCH_TIMEOUT
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except (httpx.HTTPError, ValueError):
        return None


async def _build_one(client: httpx.AsyncClient, app: Application) -> dict:
    """Construit le catalogue d'une app : préférée -> fallback -> unreachable."""
    try:
        # 1. Chemin préféré : /api/m2m/catalog (tagging fiable)
        catalog = await _fetch_app_catalog(client, app.api_url, app.slug)
        if catalog is not None:
            catalog.setdefault("slug", app.slug)
            catalog["name"] = app.name
            catalog["description"] = app.description
            catalog["version"] = getattr(app, "version", None)
            catalog["is_shared"] = app.is_shared_service
            catalog["introspection_source"] = "m2m-catalog"
            return catalog

        # 2. Fallback : /api/openapi.json (heuristique)
        openapi_spec = await _fetch_app_openapi(client, app.api_url)
        if openapi_spec is not None:
            heuristic = apply_heuristic(openapi_spec, app.slug)
            heuristic["name"] = app.name
            heuristic["description"] = app.description
            heuristic["version"] = getattr(app, "version", None)
            heuristic["is_shared"] = app.is_shared_service
            return heuristic

        # 3. Injoignable
        return {
            "slug": app.slug,
            "name": app.name,
            "description": app.description,
            "version": getattr(app, "version", None),
            "is_shared": app.is_shared_service,
            "introspection_source": "unreachable",
            "error": "catalog et openapi injoignables",
            "endpoints": [],
        }
    except Exception as e:
        logger.exception("Erreur inattendue lors de l'introspection de %s", app.slug)
        return {
            "slug": app.slug,
            "name": app.name,
            "description": app.description,
            "version": getattr(app, "version", None),
            "is_shared": app.is_shared_service,
            "introspection_source": "unreachable",
            "error": str(e),
            "endpoints": [],
        }


async def build_m2m_catalog_export(db: AsyncSession) -> dict:
    """
    Agrège le catalogue M2M de toutes les apps partagées (is_shared_service=True,
    is_active=True). Retourne le blueprint complet.
    """
    result = await db.execute(
        select(Application)
        .where(
            Application.is_shared_service == True,  # noqa: E712
            Application.is_active == True,          # noqa: E712
        )
        .order_by(Application.name)
    )
    apps = result.scalars().all()

    apps_catalogs = []
    async with httpx.AsyncClient(verify=False) as client:
        raw_results = await asyncio.gather(
            *[_build_one(client, app) for app in apps],
            return_exceptions=True,
        )

    # Une app qui échoue ne doit pas faire planter tout l'export
    apps_catalogs = []
    for item in raw_results:
        if isinstance(item, Exception):
            logger.error("Échec d'introspection d'une app: %s", item)
            apps_catalogs.append({
                "slug": "unknown",
                "name": None,
                "description": None,
                "version": None,
                "is_shared": None,
                "introspection_source": "unreachable",
                "error": str(item),
                "endpoints": [],
            })
        else:
            apps_catalogs.append(item)

    # Compteurs
    endpoints_count = sum(len(c.get("endpoints", [])) for c in apps_catalogs)
    m2m_reachable_count = sum(
        1
        for c in apps_catalogs
        for e in c.get("endpoints", [])
        if e.get("auth_level") == "service"
    )

    return {
        "$schema": "m2m-catalog/v1",
        "export_metadata": {
            "generated_at": None,  # renseigné par l'endpoint au moment de la réponse
            "hub_version": None,   # idem
            "source_environment": "prod",
            "apps_count": len(apps_catalogs),
            "endpoints_count": endpoints_count,
            "m2m_reachable_count": m2m_reachable_count,
        },
        "m2m_usage_notes": M2M_USAGE_NOTES,
        "apps": apps_catalogs,
    }
