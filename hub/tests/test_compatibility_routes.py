# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests de compatibilite pour les anciens clients HTTP du Hub.
"""
import pytest
from httpx import AsyncClient

from app.models import Application
from tests.conftest import get_auth_header


class TestCompatibilityRoutes:
    """Valide les alias conserves pour les clients historiques."""

    @pytest.mark.asyncio
    async def test_api_health_alias_returns_health_payload(
        self,
        client: AsyncClient,
    ):
        """L'alias /api/health doit repondre comme /health."""
        response = await client.get("/api/health")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "healthy"
        assert payload["service"] == "hub-auth"
        assert "version" in payload  # champ ajouté par /health (get_hub_version)

    @pytest.mark.asyncio
    async def test_backup_list_alias_matches_canonical_route(
        self,
        client: AsyncClient,
        admin_token: str,
        test_app: Application,
    ):
        """L'alias legacy /backup/list doit retourner la meme reponse."""
        app_slug = str(test_app.slug)
        headers = get_auth_header(admin_token)

        canonical_response = await client.get(
            f"/api/apps/{app_slug}/backup",
            headers=headers,
        )
        legacy_response = await client.get(
            f"/api/apps/{app_slug}/backup/list",
            headers=headers,
        )

        assert canonical_response.status_code == 200
        assert legacy_response.status_code == 200
        assert legacy_response.json() == canonical_response.json()
