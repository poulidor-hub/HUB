# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

# HUB_base/hub/tests/test_oidc_clients_admin.py
import pytest
from httpx import AsyncClient
from app.models import OAuthClient
from app.auth import create_access_token, get_password_hash
from tests.conftest import get_auth_header


class TestOidcClientsCrud:

    @pytest.mark.asyncio
    async def test_list_clients_as_admin(
        self, client: AsyncClient, admin_user, async_session
    ):
        c = OAuthClient(
            client_id="vikunja",
            client_secret_hash=get_password_hash("x"),
            name="Vikunja",
        )
        async_session.add(c)
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        response = await client.get(
            "/api/oidc/clients", headers=get_auth_header(token)
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(c["client_id"] == "vikunja" for c in data)
        # Le secret ne doit jamais être exposé
        assert "client_secret" not in data[0]
        assert "client_secret_hash" not in data[0]

    @pytest.mark.asyncio
    async def test_list_clients_forbidden_for_user(
        self, client: AsyncClient, test_user
    ):
        token, _, _ = create_access_token(username=test_user.username, apps={})
        response = await client.get(
            "/api/oidc/clients", headers=get_auth_header(token)
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_create_client_returns_secret_once(
        self, client: AsyncClient, admin_user
    ):
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        response = await client.post(
            "/api/oidc/clients",
            json={
                "name": "Vikunja",
                "redirect_uris": ["https://vikunja.example.com/cb"],
                "scopes": ["openid", "profile", "email"],
            },
            headers=get_auth_header(token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Vikunja"
        assert data["client_id"].startswith("oc_")
        # Le secret est renvoyé UNE SEULE FOIS à la création
        assert "client_secret" in data
        assert len(data["client_secret"]) >= 30

    @pytest.mark.asyncio
    async def test_regenerate_secret(
        self, client: AsyncClient, admin_user, async_session
    ):
        c = OAuthClient(
            client_id="oc_test",
            client_secret_hash=get_password_hash("old-secret"),
            name="Test",
        )
        async_session.add(c)
        await async_session.commit()

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        response = await client.post(
            f"/api/oidc/clients/{c.id}/regenerate-secret",
            headers=get_auth_header(token),
        )
        assert response.status_code == 200
        data = response.json()
        assert "client_secret" in data
        assert data["client_secret"] != "old-secret"

    @pytest.mark.asyncio
    async def test_delete_client(
        self, client: AsyncClient, admin_user, async_session
    ):
        c = OAuthClient(
            client_id="oc_del",
            client_secret_hash=get_password_hash("x"),
            name="Del",
        )
        async_session.add(c)
        await async_session.commit()
        cid = c.id

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        response = await client.delete(
            f"/api/oidc/clients/{cid}", headers=get_auth_header(token)
        )
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_toggle_active(
        self, client: AsyncClient, admin_user, async_session
    ):
        c = OAuthClient(
            client_id="oc_toggle",
            client_secret_hash=get_password_hash("x"),
            name="Toggle",
            is_active=True,
        )
        async_session.add(c)
        await async_session.commit()
        cid = c.id

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        response = await client.patch(
            f"/api/oidc/clients/{cid}/toggle-active",
            headers=get_auth_header(token),
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is False
