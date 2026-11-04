# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

# HUB_base/hub/tests/test_oidc_backchannel_logout.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import jwt as pyjwt
from httpx import AsyncClient
from app.models import OAuthClient, OAuthRefreshToken
from app.auth import get_password_hash, hash_token, create_access_token
from tests.conftest import get_auth_header


@pytest.fixture
async def vikunja_client_with_logout(async_session):
    client_obj = OAuthClient(
        client_id="vikunja",
        client_secret_hash=get_password_hash("x"),
        name="Vikunja",
        redirect_uris=["https://vikunja.example.com/cb"],
        backchannel_logout_uri="https://vikunja.example.com/auth/openid/backchannel-logout",
    )
    async_session.add(client_obj)
    await async_session.commit()
    return client_obj


class TestBackchannelLogout:

    @pytest.mark.asyncio
    async def test_backchannel_logout_admin_only(
        self, client: AsyncClient, vikunja_client_with_logout, test_user
    ):
        # Non-admin → 403
        token, _, _ = create_access_token(
            username=test_user.username, apps={}
        )
        response = await client.post(
            "/api/oidc/backchannel-logout/vikunja",
            json={"sub": "alice"},
            headers=get_auth_header(token),
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_backchannel_logout_revokes_refresh_tokens_and_notifies(
        self, client: AsyncClient, vikunja_client_with_logout,
        admin_user, test_user, async_session,
    ):
        from datetime import timedelta
        from app.models import utcnow
        # Créer un refresh token pour test_user sur vikunja
        rt = OAuthRefreshToken(
            token_hash=hash_token("rt-1"),
            client_id="vikunja",
            user_id=test_user.id,
            scope="openid",
            expires_at=utcnow() + timedelta(days=30),
        )
        async_session.add(rt)
        await async_session.commit()

        token, _, _ = create_access_token(
            username=admin_user.username, apps={}
        )
        # Mock le client httpx créé par l'endpoint : on patche le constructeur
        # AsyncClient dans le module routes (et non la méthode .post sur la classe
        # réelle, ce qui intercepterait aussi le client de test httpx inbound).
        # Le client de test (instance réelle créée par la fixture avant le patch)
        # n'est pas affecté : seul le nouveau client créé dans l'endpoint l'est.
        mock_http = MagicMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.post = AsyncMock()
        mock_http.post.return_value.status_code = 200
        with patch("app.routes.oidc.httpx.AsyncClient", return_value=mock_http):
            response = await client.post(
                "/api/oidc/backchannel-logout/vikunja",
                json={"sub": test_user.username},
                headers=get_auth_header(token),
            )
        assert response.status_code == 200
        # Vérifier que le refresh token a été révoqué
        await async_session.refresh(rt)
        assert rt.revoked_at is not None
        # Vérifier que le logout_token a été envoyé
        mock_http.post.assert_awaited_once()
        call_kwargs = mock_http.post.call_args
        assert "logout_token" in call_kwargs.kwargs.get("data", {})

    @pytest.mark.asyncio
    async def test_backchannel_logout_no_uri_returns_400(
        self, client: AsyncClient, async_session, admin_user
    ):
        client_obj = OAuthClient(
            client_id="no-uri",
            client_secret_hash=get_password_hash("x"),
            name="No URI",
            redirect_uris=["https://example.com/cb"],
            backchannel_logout_uri=None,
        )
        async_session.add(client_obj)
        await async_session.commit()

        token, _, _ = create_access_token(
            username=admin_user.username, apps={}
        )
        response = await client.post(
            "/api/oidc/backchannel-logout/no-uri",
            json={"sub": "alice"},
            headers=get_auth_header(token),
        )
        assert response.status_code == 400
