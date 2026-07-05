"""
Tests d'intégration pour les endpoints API d'authentification
"""
import base64
import json

import pytest
from httpx import AsyncClient
from datetime import datetime, timedelta

from app.auth import create_access_token, get_password_hash
from app.models import User, Application, UserAppRole
from tests.conftest import get_auth_header


def decode_jwt_payload(token: str) -> dict:
    payload_b64 = token.split('.')[1]
    payload_b64 += '=' * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


class TestAuthEndpoints:
    """Tests pour les endpoints /api/auth/*"""
    
    @pytest.mark.asyncio
    async def test_login_success(self, client: AsyncClient, test_user: User):
        """Login réussi avec credentials valides"""
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "testuser",
                "password": "testpass123"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert "expires_at" in data
        assert data["token_type"] == "bearer"
    
    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client: AsyncClient, test_user: User):
        """Login échoue avec mauvais mot de passe"""
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "testuser",
                "password": "wrongpassword"
            }
        )
        
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_login_unknown_user(self, client: AsyncClient):
        """Login échoue avec utilisateur inconnu"""
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "unknownuser",
                "password": "password123"
            }
        )
        
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_login_inactive_user(self, client: AsyncClient, async_session):
        """Login échoue pour un utilisateur inactif"""
        # Créer un utilisateur inactif
        inactive_user = User(
            username="inactive",
            email="inactive@test.com",
            hashed_password=get_password_hash("password123"),
            is_active=False
        )
        async_session.add(inactive_user)
        await async_session.commit()
        
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "inactive",
                "password": "password123"
            }
        )
        
        assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_refresh_token_flow(self, client: AsyncClient, test_user: User):
        """Test du flow de refresh token"""
        # 1. Login pour obtenir les tokens
        login_response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "testuser",
                "password": "testpass123"
            }
        )
        
        assert login_response.status_code == 200
        tokens = login_response.json()
        refresh_token = tokens["refresh_token"]
        
        # 2. Utiliser le refresh token
        refresh_response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            }
        )
        
        assert refresh_response.status_code == 200
        new_tokens = refresh_response.json()
        assert "access_token" in new_tokens
        assert "refresh_token" in new_tokens
        # Le nouveau refresh token doit être différent (rotation)
        assert new_tokens["refresh_token"] != refresh_token
    
    @pytest.mark.asyncio
    async def test_refresh_token_invalid(self, client: AsyncClient):
        """Refresh token invalide échoue"""
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": "invalid_refresh_token"
            }
        )
        
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_get_public_key(self, client: AsyncClient):
        """Récupération de la clé publique"""
        response = await client.get("/api/auth/public-key")
        
        assert response.status_code == 200
        data = response.json()
        assert "public_key" in data
        assert "-----BEGIN PUBLIC KEY-----" in data["public_key"]
    
    @pytest.mark.asyncio
    async def test_me_endpoint(self, client: AsyncClient, test_user: User):
        """Endpoint /me retourne les infos utilisateur"""
        # Créer un token
        token, _, _ = create_access_token(
            username=test_user.username,
            apps={}
        )
        
        response = await client.get(
            "/api/auth/me",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "testuser"
    
    @pytest.mark.asyncio
    async def test_me_endpoint_without_token(self, client: AsyncClient):
        """Endpoint /me échoue sans token"""
        response = await client.get("/api/auth/me")
        
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_token_introspection(self, client: AsyncClient, test_user: User, monkeypatch):
        """Test de l'introspection de token (appel interne whitelisté)"""
        # Simule une app du parc appelant depuis un réseau whitelisté
        monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: True)
        token, _, _ = create_access_token(
            username=test_user.username,
            apps={"test-app": "user"}
        )
        
        response = await client.post(
            "/api/auth/token/introspect",
            data={"token": token}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert data["sub"] == "testuser"
        assert "apps" in data

    @pytest.mark.asyncio
    async def test_token_introspection_expired(self, client: AsyncClient, monkeypatch):
        """Introspection d'un token expiré (appel interne whitelisté)"""
        monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: True)
        token, _, _ = create_access_token(
            username="testuser",
            apps={},
            expires_delta=timedelta(seconds=-10)
        )
        
        response = await client.post(
            "/api/auth/token/introspect",
            data={"token": token}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is False

    @pytest.mark.asyncio
    async def test_token_introspection_external_blocked(self, client: AsyncClient, test_user: User, monkeypatch):
        """Un appel externe (non whitelisté) sans Authorization doit être rejeté (RFC 7662 §2.1)."""
        monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: False)
        token, _, _ = create_access_token(username=test_user.username, apps={"test-app": "user"})
        response = await client.post("/api/auth/token/introspect", data={"token": token})
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_logout(self, client: AsyncClient, test_user: User):
        """Test du logout"""
        # Login
        login_response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "testuser",
                "password": "testpass123"
            }
        )
        tokens = login_response.json()
        
        # Logout
        response = await client.post(
            "/api/auth/logout",
            headers=get_auth_header(tokens["access_token"])
        )
        
        assert response.status_code == 200


class TestAppLaunch:
    """Tests pour le lancement d'applications depuis le dashboard."""

    @pytest.mark.asyncio
    async def test_app_launch_user_role_uses_callback_url(
        self, client: AsyncClient, user_with_app_access
    ):
        """Un utilisateur autorisé reçoit un token app et l'URL callback en priorité."""
        user, app = user_with_app_access
        token, _, _ = create_access_token(username=str(user.username), apps={})

        response = await client.post(
            f"/api/auth/apps/{app.slug}/launch",
            headers=get_auth_header(token)
        )

        assert response.status_code == 200
        data = response.json()
        assert data["launch_url"] == app.callback_url
        assert data["app_slug"] == app.slug
        assert data["role"] == "user"

        payload = decode_jwt_payload(data["access_token"])
        assert payload["sub"] == user.username
        assert payload["apps"][app.slug] == "user"

    @pytest.mark.asyncio
    async def test_app_launch_admin_without_explicit_role_gets_admin(
        self, client: AsyncClient, admin_user: User, test_app: Application
    ):
        """Un admin Hub sans rôle explicite obtient un rôle admin dans le token de lancement."""
        token, _, _ = create_access_token(username=str(admin_user.username), apps={})

        response = await client.post(
            f"/api/auth/apps/{test_app.slug}/launch",
            headers=get_auth_header(token)
        )

        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "admin"

        payload = decode_jwt_payload(data["access_token"])
        assert payload["apps"][test_app.slug] == "admin"

    @pytest.mark.asyncio
    async def test_app_launch_forbidden_without_role(
        self, client: AsyncClient, test_user: User, test_app: Application
    ):
        """Un utilisateur non admin sans rôle applicatif ne peut pas lancer l'app."""
        token, _, _ = create_access_token(username=str(test_user.username), apps={})

        response = await client.post(
            f"/api/auth/apps/{test_app.slug}/launch",
            headers=get_auth_header(token)
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_app_launch_forbidden_when_app_inactive(
        self, client: AsyncClient, user_with_app_access, async_session
    ):
        """Une application désactivée ne peut pas être lancée."""
        user, app = user_with_app_access
        app.is_active = False
        await async_session.commit()
        token, _, _ = create_access_token(username=str(user.username), apps={})

        response = await client.post(
            f"/api/auth/apps/{app.slug}/launch",
            headers=get_auth_header(token)
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_app_launch_requires_launch_url(
        self, client: AsyncClient, user_with_app_access, async_session
    ):
        """Une application sans callback_url ni frontend_url retourne une erreur explicite."""
        user, app = user_with_app_access
        app.callback_url = None
        app.frontend_url = None
        await async_session.commit()
        token, _, _ = create_access_token(username=str(user.username), apps={})

        response = await client.post(
            f"/api/auth/apps/{app.slug}/launch",
            headers=get_auth_header(token)
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_json_refresh_token_includes_apps_and_beacon_token(
        self, client: AsyncClient, user_with_app_access
    ):
        """Le refresh JSON conserve les rôles app et fournit un beacon_token utilisateur."""
        user, app = user_with_app_access
        login_response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": user.username,
                "password": "testpass123"
            }
        )
        assert login_response.status_code == 200
        refresh_token = login_response.json()["refresh_token"]

        response = await client.post(
            "/api/auth/token/refresh",
            json={"refresh_token": refresh_token}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["beacon_token"]

        payload = decode_jwt_payload(data["access_token"])
        assert payload["apps"][app.slug] == "user"


class TestClientCredentials:
    """Tests pour l'authentification M2M (service accounts)"""
    
    @pytest.mark.asyncio
    async def test_client_credentials_login(self, client: AsyncClient, service_account):
        """Login avec client credentials"""
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": service_account.client_id,
                "client_secret": "secret123"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
    
    @pytest.mark.asyncio
    async def test_client_credentials_invalid_secret(self, client: AsyncClient, service_account):
        """Client credentials avec mauvais secret"""
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": service_account.client_id,
                "client_secret": "wrong_secret"
            }
        )
        
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_client_credentials_unknown_client(self, client: AsyncClient):
        """Client credentials avec client inconnu"""
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "unknown_client",
                "client_secret": "secret123"
            }
        )
        
        assert response.status_code == 401


class TestSSOFlow:
    """Tests pour le flow SSO"""
    
    @pytest.mark.asyncio
    async def test_sso_redirect(self, client: AsyncClient, test_user: User, test_app: Application, user_with_app_access):
        """Test du flow SSO avec redirection"""
        response = await client.post(
            "/api/auth/sso",
            data={
                "username": "testuser",
                "password": "testpass123",
                "app_slug": "test-app"
            },
            follow_redirects=False
        )
        
        # Doit rediriger vers le callback de l'app
        assert response.status_code == 303
        assert "Location" in response.headers
        location = response.headers["Location"]
        assert "callback" in location
        assert "access_token=" in location
    
    @pytest.mark.asyncio
    async def test_sso_app_not_found(self, client: AsyncClient, test_user: User):
        """SSO avec app inexistante"""
        response = await client.post(
            "/api/auth/sso",
            data={
                "username": "testuser",
                "password": "testpass123",
                "app_slug": "unknown-app"
            }
        )
        
        assert response.status_code == 404
    
    @pytest.mark.asyncio
    async def test_sso_no_access(self, client: AsyncClient, test_user: User, test_app: Application):
        """SSO sans accès à l'app"""
        # L'utilisateur existe mais n'a pas accès à l'app (pas de UserAppRole)
        response = await client.post(
            "/api/auth/sso",
            data={
                "username": "testuser",
                "password": "testpass123",
                "app_slug": "test-app"
            }
        )
        
        assert response.status_code == 403
