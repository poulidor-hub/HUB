"""
Tests de l'agrégation du catalogue M2M côté Hub.
"""
import pytest
from unittest.mock import AsyncMock, patch
from app.m2m_catalog_export import apply_heuristic, build_m2m_catalog_export
from app.auth import create_access_token
from tests.conftest import get_auth_header


class TestApplyHeuristic:
    """apply_heuristic tagge les endpoints à partir d'un OpenAPI brut."""

    def test_m2m_path_tagged_service(self):
        spec = {
            "paths": {
                "/api/m2m/sync": {
                    "post": {
                        "summary": "Sync",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            }
        }
        catalog = apply_heuristic(spec, slug="demo")
        ep = catalog["endpoints"][0]
        assert ep["path"] == "/api/m2m/sync"
        assert ep["auth_level"] == "service"
        assert catalog["introspection_source"] == "openapi-heuristic"

    def test_secured_endpoint_tagged_user(self):
        spec = {
            "paths": {
                "/api/items": {
                    "get": {
                        "security": [{"OAuth2": []}],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            }
        }
        catalog = apply_heuristic(spec, slug="demo")
        # security présent -> l'heuristique lève le niveau à "user"
        assert catalog["endpoints"][0]["auth_level"] == "user"

    def test_unsecured_endpoint_tagged_public(self):
        spec = {
            "paths": {
                "/api/health": {
                    "get": {
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            }
        }
        catalog = apply_heuristic(spec, slug="demo")
        assert catalog["endpoints"][0]["auth_level"] == "public"

    def test_excludes_technical_paths(self):
        spec = {
            "paths": {
                "/docs": {"get": {"responses": {"200": {"description": "x"}}}},
                "/api/items": {"get": {"responses": {"200": {"description": "x"}}}},
            }
        }
        catalog = apply_heuristic(spec, slug="demo")
        paths = [e["path"] for e in catalog["endpoints"]]
        assert "/api/items" in paths
        assert "/docs" not in paths


class TestExportEndpoint:
    """GET /api/apps/m2m-catalog/export est admin-only et retourne un JSON téléchargeable."""

    @pytest.mark.asyncio
    async def test_admin_can_export(self, client, admin_user, test_app):
        # test_app crée une app partagée ; on mocke l'agrégation
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        with patch(
            "app.routes.apps.build_m2m_catalog_export",
            new_callable=AsyncMock,
            return_value={
                "$schema": "m2m-catalog/v1",
                "export_metadata": {"apps_count": 0, "endpoints_count": 0, "m2m_reachable_count": 0},
                "m2m_usage_notes": {},
                "apps": [],
            },
        ):
            response = await client.get(
                "/api/apps/m2m-catalog/export",
                headers=get_auth_header(token),
            )
        assert response.status_code == 200
        data = response.json()
        assert data["$schema"] == "m2m-catalog/v1"
        assert "apps" in data
        # Content-Disposition présent (téléchargement)
        assert "attachment" in response.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_user_forbidden(self, client, test_user):
        token, _, _ = create_access_token(username=test_user.username, apps={})
        response = await client.get(
            "/api/apps/m2m-catalog/export",
            headers=get_auth_header(token),
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_anon_unauthorized(self, client):
        response = await client.get("/api/apps/m2m-catalog/export")
        assert response.status_code == 401
