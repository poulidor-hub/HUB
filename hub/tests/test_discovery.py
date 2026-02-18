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
