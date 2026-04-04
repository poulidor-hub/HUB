"""
Tests pour l'Explorateur Discovery (nouveau proxy-based Discovery)
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient

from tests.conftest import get_auth_header


# Helper mock pour _get_discovery_token et _get_httpx_client
def _mock_discovery_token():
    """Patch _get_discovery_token pour retourner un token factice."""
    return patch(
        "app.routes.discovery._get_discovery_token",
        new_callable=AsyncMock,
        return_value="mock-discovery-token-for-tests",
    )


def _mock_httpx_client(mock_response):
    """Patch _get_httpx_client pour retourner un client httpx mocké."""
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_response)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return patch("app.routes.discovery._get_httpx_client", return_value=mock_client), mock_client


def _make_mock_response(status=200, body=None, text="", content=b"", headers=None, reason="OK"):
    """Crée un mock httpx.Response."""
    mock = MagicMock()
    mock.status_code = status
    mock.reason_phrase = reason
    mock.headers = headers or {"content-type": "application/json"}
    mock.content = content or (text.encode() if text else b"")
    mock.text = text
    if body is not None:
        mock.json = lambda: body
    else:
        mock.json = MagicMock(side_effect=ValueError("No JSON"))
    return mock


# ================================================================
# GET /api/discovery/apps — Liste toutes les apps actives (admin)
# ================================================================

class TestDiscoveryApps:
    """Tests pour l'endpoint GET /api/discovery/apps"""

    @pytest.mark.asyncio
    async def test_get_discovery_apps_as_admin(self, client: AsyncClient, admin_user, admin_token, test_app):
        """Admin peut lister toutes les apps actives"""
        response = await client.get(
            "/api/discovery/apps",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert "apps" in data
        assert "total" in data
        assert data["total"] >= 1
        # Vérifier que l'app de test est dans la liste
        slugs = [a["slug"] for a in data["apps"]]
        assert "test-app" in slugs

    @pytest.mark.asyncio
    async def test_get_discovery_apps_fields(self, client: AsyncClient, admin_user, admin_token, test_app):
        """Vérifie les champs retournés pour chaque app"""
        response = await client.get(
            "/api/discovery/apps",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        app_item = response.json()["apps"][0]
        required_fields = ["id", "slug", "name", "api_url", "is_shared_service", "is_active"]
        for field in required_fields:
            assert field in app_item, f"Champ manquant: {field}"

    @pytest.mark.asyncio
    async def test_get_discovery_apps_as_user_forbidden(self, client: AsyncClient, test_user, user_token):
        """Un utilisateur non-admin ne peut pas accéder au Discovery"""
        response = await client.get(
            "/api/discovery/apps",
            headers=get_auth_header(user_token),
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_discovery_apps_unauthenticated(self, client: AsyncClient):
        """Accès sans token → 401"""
        response = await client.get("/api/discovery/apps")
        assert response.status_code == 401


# ================================================================
# GET /api/discovery/apps/{slug}/openapi — Proxy spec OpenAPI
# ================================================================

class TestDiscoveryOpenApi:
    """Tests pour l'endpoint GET /api/discovery/apps/{slug}/openapi"""

    @pytest.mark.asyncio
    async def test_get_openapi_not_found_app(self, client: AsyncClient, admin_user, admin_token):
        """App inexistante → 404"""
        response = await client.get(
            "/api/discovery/apps/nonexistent/openapi",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_openapi_no_spec(self, client: AsyncClient, admin_user, admin_token, test_app):
        """
        App existante mais pas de spec OpenAPI disponible → retourne spec=null
        (le service cible ne répond pas)
        """
        mock_resp = _make_mock_response(status=404, text="Not Found", reason="Not Found")
        httpx_patch, _ = _mock_httpx_client(mock_resp)

        with httpx_patch, _mock_discovery_token():
            response = await client.get(
                "/api/discovery/apps/test-app/openapi",
                headers=get_auth_header(admin_token),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["spec"] is None

    @pytest.mark.asyncio
    async def test_get_openapi_as_user_forbidden(self, client: AsyncClient, test_user, user_token, test_app):
        """Non-admin → 403"""
        response = await client.get(
            "/api/discovery/apps/test-app/openapi",
            headers=get_auth_header(user_token),
        )
        assert response.status_code == 403


# ================================================================
# POST /api/discovery/proxy — Proxy de requêtes
# ================================================================

class TestDiscoveryProxy:
    """Tests pour l'endpoint POST /api/discovery/proxy"""

    @pytest.mark.asyncio
    async def test_proxy_success(self, client: AsyncClient, admin_user, admin_token, test_app):
        """Proxy réussi vers un service cible"""
        mock_resp = _make_mock_response(
            status=200,
            body=[{"id": 1, "name": "test"}],
            text='[{"id": 1, "name": "test"}]',
            content=b'[{"id": 1, "name": "test"}]',
        )
        httpx_patch, mock_client = _mock_httpx_client(mock_resp)

        with httpx_patch, _mock_discovery_token():
            response = await client.post(
                "/api/discovery/proxy",
                headers=get_auth_header(admin_token),
                json={
                    "app_slug": "test-app",
                    "method": "GET",
                    "path": "/api/items",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == 200
        assert data["body"] == [{"id": 1, "name": "test"}]
        assert data["elapsed_ms"] >= 0

    @pytest.mark.asyncio
    async def test_proxy_not_found_app(self, client: AsyncClient, admin_user, admin_token):
        """Slug d'app inexistant → 404"""
        response = await client.post(
            "/api/discovery/proxy",
            headers=get_auth_header(admin_token),
            json={
                "app_slug": "nonexistent",
                "method": "GET",
                "path": "/api/test",
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_proxy_invalid_method(self, client: AsyncClient, admin_user, admin_token, test_app):
        """Méthode HTTP invalide → 400"""
        response = await client.post(
            "/api/discovery/proxy",
            headers=get_auth_header(admin_token),
            json={
                "app_slug": "test-app",
                "method": "INVALID",
                "path": "/api/test",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_proxy_with_query_params(self, client: AsyncClient, admin_user, admin_token, test_app):
        """Proxy avec query params"""
        mock_resp = _make_mock_response(status=200, body=[], text="[]", content=b"[]")
        httpx_patch, mock_client = _mock_httpx_client(mock_resp)

        with httpx_patch, _mock_discovery_token():
            response = await client.post(
                "/api/discovery/proxy",
                headers=get_auth_header(admin_token),
                json={
                    "app_slug": "test-app",
                    "method": "GET",
                    "path": "/api/items",
                    "query_params": {"limit": "10", "status": "active"},
                },
            )

        assert response.status_code == 200
        # Vérifier que httpx a été appelé avec les params
        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["params"] == {"limit": "10", "status": "active"}

    @pytest.mark.asyncio
    async def test_proxy_with_body(self, client: AsyncClient, admin_user, admin_token, test_app):
        """Proxy POST avec body JSON"""
        mock_resp = _make_mock_response(
            status=201, body={"id": 42}, text='{"id": 42}', content=b'{"id": 42}', reason="Created"
        )
        httpx_patch, mock_client = _mock_httpx_client(mock_resp)

        with httpx_patch, _mock_discovery_token():
            response = await client.post(
                "/api/discovery/proxy",
                headers=get_auth_header(admin_token),
                json={
                    "app_slug": "test-app",
                    "method": "POST",
                    "path": "/api/items",
                    "body": {"name": "test item", "value": 100},
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == 201
        # Vérifier que le body a été transmis
        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["json"] == {"name": "test item", "value": 100}

    @pytest.mark.asyncio
    async def test_proxy_connection_error(self, client: AsyncClient, admin_user, admin_token, test_app):
        """Erreur de connexion au service cible → retourne status 0"""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.routes.discovery._get_httpx_client", return_value=mock_client), _mock_discovery_token():
            response = await client.post(
                "/api/discovery/proxy",
                headers=get_auth_header(admin_token),
                json={
                    "app_slug": "test-app",
                    "method": "GET",
                    "path": "/api/test",
                },
            )

        assert response.status_code == 200  # Le proxy retourne 200 mais avec status=0
        data = response.json()
        assert data["status"] == 0
        assert data["error"] is not None
        assert "Connection refused" in data["error"]

    @pytest.mark.asyncio
    async def test_proxy_as_user_forbidden(self, client: AsyncClient, test_user, user_token, test_app):
        """Non-admin → 403"""
        response = await client.post(
            "/api/discovery/proxy",
            headers=get_auth_header(user_token),
            json={
                "app_slug": "test-app",
                "method": "GET",
                "path": "/api/test",
            },
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_proxy_unauthenticated(self, client: AsyncClient):
        """Sans token → 401"""
        response = await client.post(
            "/api/discovery/proxy",
            json={
                "app_slug": "test-app",
                "method": "GET",
                "path": "/api/test",
            },
        )
        assert response.status_code == 401


# ================================================================
# filter_openapi_spec — Filtrage des endpoints techniques
# ================================================================

class TestFilterOpenApiSpec:
    """Tests pour la fonction filter_openapi_spec"""

    def test_filter_health_endpoints(self):
        """Les endpoints health/docs/openapi sont filtrés"""
        from app.routes.apps import filter_openapi_spec

        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/health": {"get": {"summary": "Health check"}},
                "/api/health": {"get": {"summary": "API Health"}},
                "/openapi.json": {"get": {"summary": "OpenAPI spec"}},
                "/docs": {"get": {"summary": "Swagger UI"}},
                "/redoc": {"get": {"summary": "Redoc"}},
                "/api/employees": {"get": {"summary": "List employees"}},
                "/api/employees/{id}": {
                    "get": {"summary": "Get employee"},
                    "put": {"summary": "Update employee"},
                },
            },
        }

        filtered, total, excluded = filter_openapi_spec(spec)

        # Seuls les endpoints métier restent
        assert "/api/employees" in filtered["paths"]
        assert "/api/employees/{id}" in filtered["paths"]
        assert "/health" not in filtered["paths"]
        assert "/api/health" not in filtered["paths"]
        assert "/openapi.json" not in filtered["paths"]
        assert "/docs" not in filtered["paths"]
        assert "/redoc" not in filtered["paths"]

        assert excluded >= 5  # health, api/health, openapi.json, docs, redoc
        assert total == 8  # total all endpoints

    def test_filter_by_tags(self):
        """Les endpoints avec tags techniques sont filtrés"""
        from app.routes.apps import filter_openapi_spec

        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/api/data": {
                    "get": {"summary": "Get data", "tags": ["Data"]},
                },
                "/api/debug/info": {
                    "get": {"summary": "Debug info", "tags": ["debug"]},
                },
                "/api/internal/cache": {
                    "get": {"summary": "Clear cache", "tags": ["internal"]},
                },
            },
        }

        filtered, total, excluded = filter_openapi_spec(spec)

        assert "/api/data" in filtered["paths"]
        assert "/api/debug/info" not in filtered["paths"]
        assert "/api/internal/cache" not in filtered["paths"]

    def test_filter_empty_spec(self):
        """Une spec vide retourne vide"""
        from app.routes.apps import filter_openapi_spec

        result, total, excluded = filter_openapi_spec({})
        assert total == 0
        assert excluded == 0

    def test_filter_preserves_info(self):
        """Le filtrage conserve les métadonnées (info, servers, etc.)"""
        from app.routes.apps import filter_openapi_spec

        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Mon API", "version": "2.1"},
            "servers": [{"url": "http://localhost:8000"}],
            "paths": {
                "/health": {"get": {"summary": "Health"}},
                "/api/items": {"get": {"summary": "List items"}},
            },
        }

        filtered, _, _ = filter_openapi_spec(spec)

        assert filtered["info"]["title"] == "Mon API"
        assert filtered["info"]["version"] == "2.1"
        assert filtered["servers"] == [{"url": "http://localhost:8000"}]


# ================================================================
# GET /api/discovery/apps/{slug}/openapi — Spec stockée prioritaire
# ================================================================

class TestDiscoveryStoredSpec:
    """Tests pour la priorité spec stockée vs fetch live"""

    @pytest.mark.asyncio
    async def test_returns_stored_spec_first(self, client: AsyncClient, admin_user, admin_token, test_app, async_session):
        """Si l'app a une spec stockée, elle est retournée sans fetch live"""
        from app.models import Application
        from sqlalchemy import update

        # Stocker une spec dans l'app
        stored_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Stored API", "version": "1.0"},
            "paths": {
                "/api/items": {"get": {"summary": "List items", "tags": ["Items"]}},
            },
        }
        await async_session.execute(
            update(Application)
            .where(Application.slug == "test-app")
            .values(api_endpoints=stored_spec)
        )
        await async_session.commit()

        response = await client.get(
            "/api/discovery/apps/test-app/openapi",
            headers=get_auth_header(admin_token),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["spec"] is not None
        assert data["source"] == "stored"
        assert data["spec"]["info"]["title"] == "Stored API"
        assert "/api/items" in data["spec"]["paths"]

    @pytest.mark.asyncio
    async def test_falls_back_to_live_fetch(self, client: AsyncClient, admin_user, admin_token, test_app):
        """Sans spec stockée, fallback sur le fetch live"""
        mock_resp = _make_mock_response(status=404, text="Not Found", reason="Not Found")
        httpx_patch, _ = _mock_httpx_client(mock_resp)

        with httpx_patch, _mock_discovery_token():
            response = await client.get(
                "/api/discovery/apps/test-app/openapi",
                headers=get_auth_header(admin_token),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["spec"] is None  # Aucune spec trouvée ni en base ni en live


# ================================================================
# GET /api/discovery/apps/{slug}/m2m-setup — Bloc M2M Setup
# ================================================================

class TestM2MSetup:
    """Tests pour l'endpoint GET /api/discovery/apps/{slug}/m2m-setup"""

    @pytest.mark.asyncio
    async def test_m2m_setup_requires_admin(self, client: AsyncClient, user_token):
        """L'endpoint M2M Setup nécessite un admin"""
        response = await client.get(
            "/api/discovery/apps/test-app/m2m-setup",
            headers=get_auth_header(user_token),
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_m2m_setup_unknown_app(self, client: AsyncClient, admin_user, admin_token):
        """App inexistante retourne 404"""
        response = await client.get(
            "/api/discovery/apps/nonexistent-app/m2m-setup",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_m2m_setup_with_stored_spec(self, client: AsyncClient, admin_user, admin_token, test_app, async_session):
        """M2M Setup avec une spec OpenAPI stockée en base"""
        from app.models import Application
        from sqlalchemy import select, update

        # Stocker une spec OpenAPI en base
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0"},
            "paths": {
                "/api/items": {
                    "get": {
                        "summary": "Liste les items",
                        "tags": ["Items"],
                        "parameters": [
                            {"name": "limit", "in": "query", "schema": {"type": "integer"}, "required": False, "description": "Nombre max de résultats"}
                        ]
                    },
                    "post": {
                        "summary": "Créer un item",
                        "tags": ["Items"],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string", "description": "Nom de l'item"},
                                            "quantity": {"type": "integer", "description": "Quantité"}
                                        },
                                        "required": ["name"]
                                    }
                                }
                            }
                        }
                    }
                },
                "/api/items/{id}": {
                    "get": {
                        "summary": "Détail d'un item",
                        "tags": ["Items"],
                        "parameters": [
                            {"name": "id", "in": "path", "schema": {"type": "integer"}, "required": True, "description": "ID de l'item"}
                        ]
                    }
                }
            }
        }
        await async_session.execute(
            update(Application)
            .where(Application.slug == "test-app")
            .values(api_endpoints=spec)
        )
        await async_session.commit()

        response = await client.get(
            "/api/discovery/apps/test-app/m2m-setup",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200

        data = response.json()
        assert data["app_slug"] == "test-app"
        assert data["app_name"] == test_app.name
        assert data["api_url"] == test_app.api_url
        assert "token_url" in data
        assert "/api/auth/token" in data["token_url"]
        assert len(data["endpoints"]) == 3  # GET items, POST items, GET items/{id}

        # Vérifier le Markdown
        md = data["markdown"]
        assert "# M2M Setup" in md
        assert test_app.name in md
        assert "CLIENT_ID" in md
        assert "<votre_client_id>" in md
        assert "<votre_client_secret>" in md
        assert "grant_type" in md
        assert "/api/items" in md
        assert "Liste les items" in md
        assert "limit" in md
        assert "httpx" in md

    @pytest.mark.asyncio
    async def test_m2m_setup_without_spec(self, client: AsyncClient, admin_user, admin_token, test_app):
        """M2M Setup sans spec OpenAPI fournit quand même les infos de connexion"""
        mock_resp = _make_mock_response(status=404, text="Not Found", reason="Not Found")
        httpx_patch, _ = _mock_httpx_client(mock_resp)

        with httpx_patch, _mock_discovery_token():
            response = await client.get(
                "/api/discovery/apps/test-app/m2m-setup",
                headers=get_auth_header(admin_token),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["app_slug"] == "test-app"
        assert data["endpoints"] == []

        md = data["markdown"]
        assert "# M2M Setup" in md
        assert "CLIENT_ID" in md
        assert "Aucun endpoint documenté" in md


class TestMarkdownGeneration:
    """Tests unitaires pour les helpers de génération Markdown"""

    def test_extract_endpoints_from_empty_spec(self):
        from app.routes.discovery import _extract_endpoints_from_spec
        assert _extract_endpoints_from_spec(None) == []
        assert _extract_endpoints_from_spec({}) == []
        assert _extract_endpoints_from_spec({"paths": {}}) == []

    def test_extract_endpoints_from_spec(self):
        from app.routes.discovery import _extract_endpoints_from_spec

        spec = {
            "paths": {
                "/api/users": {
                    "get": {"summary": "List users", "tags": ["Users"], "parameters": []},
                    "post": {"summary": "Create user", "tags": ["Users"],
                             "requestBody": {"content": {"application/json": {"schema": {
                                 "type": "object",
                                 "properties": {"name": {"type": "string"}}
                             }}}}}
                }
            }
        }
        endpoints = _extract_endpoints_from_spec(spec)
        assert len(endpoints) == 2
        assert endpoints[0].method == "GET"
        assert endpoints[0].path == "/api/users"
        assert endpoints[1].method == "POST"
        assert endpoints[1].request_body_schema is not None

    def test_generate_markdown_with_endpoints(self):
        from app.routes.discovery import _generate_m2m_markdown, M2MEndpointDetail, M2MEndpointParam

        endpoints = [
            M2MEndpointDetail(
                method="GET",
                path="/api/items",
                summary="Liste les items",
                tags=["Items"],
                parameters=[M2MEndpointParam(name="limit", location="query", type="integer", required=False, description="Max résultats")]
            )
        ]
        md = _generate_m2m_markdown("test-app", "Test App", "Une app de test", "http://test:8000", "http://hub:8000", "http://hub:8000/api/auth/token", endpoints)

        assert "# M2M Setup — Test App (test-app)" in md
        assert "http://hub:8000/api/auth/token" in md
        assert "http://test:8000" in md
        assert "<votre_client_id>" in md
        assert "GET /api/items" in md
        assert "limit" in md
        assert "Endpoints disponibles (1)" in md

    def test_generate_markdown_without_endpoints(self):
        from app.routes.discovery import _generate_m2m_markdown

        md = _generate_m2m_markdown("my-app", "My App", "", "http://my:8000", "http://hub:8000", "http://hub:8000/api/auth/token", [])
        assert "Aucun endpoint documenté" in md
        assert "CLIENT_ID" in md
