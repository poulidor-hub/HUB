# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

import pytest
from httpx import AsyncClient


class TestDiscovery:

    @pytest.mark.asyncio
    async def test_discovery_document(self, client: AsyncClient, settings):
        settings.oidc_issuer = "https://hub.example.com"
        response = await client.get("/.well-known/openid-configuration")
        assert response.status_code == 200
        data = response.json()
        assert "issuer" in data
        assert data["issuer"].startswith("http")
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "userinfo_endpoint" in data
        assert "jwks_uri" in data
        assert "RS256" in data["id_token_signing_alg_values_supported"]
        assert "code" in data["response_types_supported"]
        assert "S256" in data["code_challenge_methods_supported"]
        assert data["backchannel_logout_supported"] is True

    @pytest.mark.asyncio
    async def test_discovery_is_public(self, client: AsyncClient, settings):
        settings.oidc_issuer = "https://hub.example.com"
        # Pas d'authentification requise
        response = await client.get("/.well-known/openid-configuration")
        assert response.status_code == 200
