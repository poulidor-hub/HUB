"""
Tests d'intégration pour les endpoints API des applications
"""
import pytest
from httpx import AsyncClient

from app.auth import create_access_token, get_password_hash
from app.models import User, Application, UserAppRole
from tests.conftest import get_auth_header


class TestAppsEndpoints:
    """Tests pour les endpoints /api/apps/*"""
    
    @pytest.mark.asyncio
    async def test_list_apps_as_admin(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Admin voit toutes les apps"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.get(
            "/api/apps/",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        apps = response.json()
        assert isinstance(apps, list)
        assert len(apps) >= 1
    
    @pytest.mark.asyncio
    async def test_list_apps_as_user(self, client: AsyncClient, user_with_app_access):
        """User voit seulement ses apps"""
        user, app = user_with_app_access
        token, _, _ = create_access_token(
            username=user.username,
            roles=["user"],
            apps={app.slug: "user"}
        )
        
        response = await client.get(
            "/api/apps/",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_list_apps_unauthorized(self, client: AsyncClient):
        """Liste des apps sans authentification échoue"""
        response = await client.get("/api/apps/")
        
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_list_all_apps_admin_only(self, client: AsyncClient, admin_user: User, test_app: Application, admin_with_app):
        """Seul admin peut voir /all"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.get(
            "/api/apps/all",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_list_all_apps_forbidden_for_user(self, client: AsyncClient, test_user: User):
        """User ne peut pas voir /all"""
        token, _, _ = create_access_token(
            username=test_user.username,
            roles=["user"],
            apps={}
        )
        
        response = await client.get(
            "/api/apps/all",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_create_app(self, client: AsyncClient, admin_user: User):
        """Création d'une application par admin"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Nouvelle App",
                "slug": "nouvelle-app",
                "description": "Description de la nouvelle app",
                "api_port": 8080,
                "frontend_port": 3000
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["app"]["slug"] == "nouvelle-app"
        assert "service_account" in data
    
    @pytest.mark.asyncio
    async def test_create_app_duplicate_slug(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Création avec slug existant échoue"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "App Duplicate",
                "slug": test_app.slug,  # Slug existant
                "description": "Test",
                "api_port": 8080
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 400
    
    @pytest.mark.asyncio
    async def test_create_app_forbidden_for_user(self, client: AsyncClient, test_user: User):
        """User ne peut pas créer d'app"""
        token, _, _ = create_access_token(
            username=test_user.username,
            roles=["user"],
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Test App",
                "slug": "test-app-new",
                "description": "Test",
                "api_port": 8080
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_get_app_by_slug(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Récupérer une app par son slug"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.get(
            f"/api/apps/{test_app.slug}",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["slug"] == test_app.slug
    
    @pytest.mark.asyncio
    async def test_get_app_not_found(self, client: AsyncClient, admin_user: User):
        """App inexistante retourne 404"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.get(
            "/api/apps/unknown-app",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 404
    
    @pytest.mark.asyncio
    async def test_update_app(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Mise à jour d'une app"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.patch(
            f"/api/apps/{test_app.slug}",
            json={
                "description": "Nouvelle description"
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Nouvelle description"
    
    @pytest.mark.asyncio
    async def test_delete_app(self, client: AsyncClient, admin_user: User, async_session):
        """Suppression d'une app"""
        # Créer une app à supprimer
        app_to_delete = Application(
            name="App to Delete",
            slug="app-to-delete",
            api_url="http://localhost:9999",
            is_active=True
        )
        async_session.add(app_to_delete)
        await async_session.commit()
        
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.delete(
            "/api/apps/app-to-delete",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200


class TestAppUserAssignment:
    """Tests pour l'assignation utilisateurs aux apps"""
    
    @pytest.mark.asyncio
    async def test_assign_user_to_app(self, client: AsyncClient, admin_user: User, test_user: User, test_app: Application):
        """Assigner un utilisateur à une app"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.post(
            f"/api/apps/{test_app.slug}/users/{test_user.id}",
            json={"role": "user"},
            headers=get_auth_header(token)
        )
        
        assert response.status_code in [200, 201]
    
    @pytest.mark.asyncio
    async def test_assign_user_with_role(self, client: AsyncClient, admin_user: User, test_user: User, test_app: Application):
        """Assigner un utilisateur avec un rôle spécifique"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.post(
            f"/api/apps/{test_app.slug}/users/{test_user.id}",
            json={"role": "manager"},
            headers=get_auth_header(token)
        )
        
        assert response.status_code in [200, 201]
    
    @pytest.mark.asyncio
    async def test_remove_user_from_app(self, client: AsyncClient, admin_user: User, user_with_app_access):
        """Retirer un utilisateur d'une app"""
        user, app = user_with_app_access
        
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.delete(
            f"/api/apps/{app.slug}/users/{user.id}",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_list_app_users(self, client: AsyncClient, admin_user: User, user_with_app_access, admin_with_app):
        """Lister les utilisateurs d'une app"""
        _, app = user_with_app_access
        
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.get(
            f"/api/apps/{app.slug}/users",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        users = response.json()
        assert isinstance(users, list)
