"""
Tests de l'agrégation du catalogue M2M côté Hub.
"""
import pytest
from app.m2m_catalog_export import apply_heuristic, build_m2m_catalog_export


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
