# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

import pytest
from httpx import AsyncClient
from app.config import get_settings
from app.oidc_helpers import create_access_token_jwt


class TestUserInfo:

    @pytest.mark.asyncio
    async def test_userinfo_returns_claims(
        self, client: AsyncClient, test_user
    ):
        settings = get_settings()
        access_token = create_access_token_jwt(
            sub=test_user.username,
            client_id="vikunja",
            scope="openid profile email",
            settings=settings,
        )
        response = await client.get(
            "/api/oidc/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["sub"] == test_user.username
        assert data["preferred_username"] == test_user.username
        assert data["name"] == test_user.username
        assert data["email"] == f"{test_user.username}@hub.internal"
        assert data["email_verified"] is True

    @pytest.mark.asyncio
    async def test_userinfo_without_token_returns_401(self, client: AsyncClient):
        response = await client.get("/api/oidc/userinfo")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_userinfo_invalid_token_returns_401(self, client: AsyncClient):
        response = await client.get(
            "/api/oidc/userinfo",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_userinfo_filtered_by_scope(
        self, client: AsyncClient, test_user
    ):
        settings = get_settings()
        # Scope sans "email" : claim email ne doit pas être retourné
        access_token = create_access_token_jwt(
            sub=test_user.username,
            client_id="vikunja",
            scope="openid profile",
            settings=settings,
        )
        response = await client.get(
            "/api/oidc/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "email" not in data
        assert data["sub"] == test_user.username
        assert "preferred_username" in data
