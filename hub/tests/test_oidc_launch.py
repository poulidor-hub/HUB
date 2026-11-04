# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests pour /api/oidc/launch/{client_id} (redirecteur vers page login app externe).

Le launch shortcut valide le JWT HUB du dashboard, vérifie l'accès user,
puis redirige vers la page /logout de l'app externe (frontend_url) avec
?provider=hub : la session éventuellement résiduelle de l'app est purgée
AVANT que l'utilisateur ne lance un flux OIDC frais (cf. commit ff8d2a4 :
le cookie de session Vikunja persistait entre deux users HUB et exposait
les données de l'utilisateur précédent). SANS access_token dans l'URL
finale (sinon l'app externe l'interprète comme un retour de flow OIDC).
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from app.models import Application, AuditLog, OAuthClient, User, UserSession
from app.auth import create_access_token, get_password_hash
from tests.conftest import get_auth_header


@pytest.fixture
async def vikunja_app_and_client(async_session, test_user):
    """Crée une app HUB 'vikunja' (frontend_url = racine de l'app, comme la
    config réelle de Vikunja au Hub) + un client OIDC lié."""
    app = Application(
        name="Vikunja",
        slug="vikunja",
        api_url="http://192.0.2.10:3457",
        frontend_url="http://192.0.2.10:3457",
        callback_url="http://192.0.2.10:8000/api/oidc/launch/vikunja",
        is_active=True,
        requires_auth=True,
    )
    async_session.add(app)
    await async_session.flush()

    client = OAuthClient(
        client_id="vikunja",
        client_secret_hash=get_password_hash("vike-secret"),
        name="Vikunja",
        redirect_uris=["http://192.0.2.10:3457/auth/openid/hub/callback"],
        scopes=["openid", "profile", "email"],
        app_id=app.id,
    )
    async_session.add(client)
    await async_session.commit()
    return app, client


class TestLaunchRedirector:

    @pytest.mark.asyncio
    async def test_launch_redirects_to_frontend_url(
        self, client: AsyncClient, vikunja_app_and_client, test_user
    ):
        """JWT valide + client lié à app → 302 vers {frontend_url}/logout?provider=hub.

        La purge de session (logout) précède le login OIDC frais (ff8d2a4),
        et l'access_token ne doit PAS apparaître dans l'URL finale.
        """
        token, _, _ = create_access_token(
            username=test_user.username, apps={"vikunja": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers["location"]
        assert location == "http://192.0.2.10:3457/logout?provider=hub"
        # L'access_token ne doit PAS être dans l'URL finale
        assert "access_token" not in location

    @pytest.mark.asyncio
    async def test_launch_rejects_invalid_jwt(self, client: AsyncClient, vikunja_app_and_client):
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={"access_token": "invalid-token"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_launch_rejects_unknown_client(self, client: AsyncClient, test_user):
        token, _, _ = create_access_token(
            username=test_user.username, apps={"unknown-app": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/unknown-client",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_launch_rejects_inactive_client(
        self, client: AsyncClient, async_session, test_user
    ):
        # App + client inactif
        app = Application(
            name="Inactive App", slug="inactive-app",
            api_url="http://example.com", frontend_url="http://example.com/login",
            is_active=True, requires_auth=True,
        )
        async_session.add(app)
        await async_session.flush()
        client_obj = OAuthClient(
            client_id="inactive", client_secret_hash=get_password_hash("x"),
            name="Inactive", redirect_uris=["http://example.com/cb"],
            is_active=False, app_id=app.id,
        )
        async_session.add(client_obj)
        await async_session.commit()

        token, _, _ = create_access_token(
            username=test_user.username, apps={"inactive-app": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/inactive",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_launch_rejects_client_without_app_id(
        self, client: AsyncClient, async_session, test_user
    ):
        """Client OIDC sans app_id → 400 (impossible de déterminer frontend_url)."""
        client_obj = OAuthClient(
            client_id="orphan", client_secret_hash=get_password_hash("x"),
            name="Orphan", redirect_uris=["http://example.com/cb"],
            app_id=None,
        )
        async_session.add(client_obj)
        await async_session.commit()

        token, _, _ = create_access_token(
            username=test_user.username, apps={"orphan": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/orphan",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_launch_rejects_user_without_app_role(
        self, client: AsyncClient, vikunja_app_and_client, test_user
    ):
        """User dont le JWT ne contient pas 'vikunja' dans apps → 403."""
        token, _, _ = create_access_token(
            username=test_user.username, apps={"other-app": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_launch_recycled_username_resolves_by_pure_username(
        self, client: AsyncClient, async_session, vikunja_app_and_client
    ):
        """Username recyclé : le lancement attribue audit + présence à B, pas A.

        Le sub du JWT applicatif EST le username (jamais renommé côté
        applicatif) : le lookup ici est par username PUR, PAS par oidc_sub.
        Scénario : A renommé jdupont→jmartin (garde oidc_sub=jdupont) ;
        B créé avec username=jdupont (oidc_sub=uuid). Le fallback oidc_sub
        du helper résoudrait vers A (mauvais user pour audit/présence).
        """
        user_a = User(
            username="jmartin", email="a@example.com",
            hashed_password=get_password_hash("x"),
            oidc_sub="jdupont", is_active=True,
        )
        user_b = User(
            username="jdupont", email="b@example.com",
            hashed_password=get_password_hash("x"),
            oidc_sub=str(uuid.uuid4()), is_active=True,
        )
        async_session.add_all([user_a, user_b])
        await async_session.commit()
        await async_session.refresh(user_a)
        await async_session.refresh(user_b)

        token, _, _ = create_access_token(
            username=user_b.username, apps={"vikunja": "user"}
        )
        response = await client.get(
            "/api/oidc/launch/vikunja",
            params={"access_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 302

        # Audit attribué à B (porteur actuel du username), pas à A (renommé)
        result = await async_session.execute(
            select(AuditLog).where(
                AuditLog.action == "oidc_launch", AuditLog.target == "vikunja"
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.user_id == user_b.id
        assert audit.user_id != user_a.id

        # Présence de session créée pour B, aucune pour A
        result = await async_session.execute(
            select(UserSession).where(UserSession.user_id == user_b.id)
        )
        assert result.scalar_one_or_none() is not None
        result = await async_session.execute(
            select(UserSession).where(UserSession.user_id == user_a.id)
        )
        assert result.scalar_one_or_none() is None
