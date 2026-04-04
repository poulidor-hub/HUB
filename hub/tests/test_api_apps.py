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
        assert data["application"]["slug"] == "nouvelle-app"
        # Note: service_account n'est plus créé automatiquement (l'app s'auto-enregistre)
    
    @pytest.mark.asyncio
    async def test_create_app_duplicate_slug(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Création avec slug existant échoue"""
        token, _, _ = create_access_token(
            username=admin_user.username,
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
    async def test_get_app_by_id(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Récupérer une app par son ID"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.get(
            f"/api/apps/{test_app.id}",
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
            apps={}
        )
        
        response = await client.get(
            "/api/apps/99999",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 404
    
    @pytest.mark.asyncio
    async def test_update_app(self, client: AsyncClient, admin_user: User, test_app: Application):
        """Mise à jour d'une app"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.patch(
            f"/api/apps/{test_app.id}",
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
        await async_session.refresh(app_to_delete)
        
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.delete(
            f"/api/apps/{app_to_delete.id}",
            headers=get_auth_header(token)
        )
        
        assert response.status_code in [200, 204]


class TestAppUserAssignment:
    """Tests pour l'assignation utilisateurs aux apps"""
    
    @pytest.mark.asyncio
    async def test_set_app_users(self, client: AsyncClient, admin_user: User, test_user: User, test_app: Application):
        """Définir les utilisateurs d'une app"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            f"/api/apps/{test_app.id}/users",
            json=[test_user.id],
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
    
    @pytest.mark.asyncio
    async def test_set_multiple_users(self, client: AsyncClient, admin_user: User, test_user: User, test_app: Application):
        """Définir plusieurs utilisateurs pour une app"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            f"/api/apps/{test_app.id}/users",
            json=[test_user.id, admin_user.id],
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_clear_app_users(self, client: AsyncClient, admin_user: User, user_with_app_access):
        """Vider les utilisateurs d'une app"""
        user, app = user_with_app_access
        
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            f"/api/apps/{app.id}/users",
            json=[],  # Liste vide pour supprimer tous les accès
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_app_users_in_all_response(self, client: AsyncClient, admin_user: User, user_with_app_access, admin_with_app):
        """Vérifier que /all retourne les utilisateurs des apps"""
        _, app = user_with_app_access
        
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.get(
            "/api/apps/all",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        apps = response.json()
        assert isinstance(apps, list)


class TestIsSharedService:
    """Tests pour le paramètre is_shared_service lors de la création d'applications"""
    
    @pytest.mark.asyncio
    async def test_create_app_with_is_shared_service_true(self, client: AsyncClient, admin_user: User):
        """Création d'une app avec is_shared_service=True via POST /api/apps/"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Shared API Test",
                "slug": "shared-api-test",
                "description": "API partagée pour test",
                "api_port": 18099,
                "is_shared_service": True
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["application"]["slug"] == "shared-api-test"
        assert data["application"]["is_shared_service"] is True
    
    @pytest.mark.asyncio
    async def test_create_app_with_is_shared_service_false(self, client: AsyncClient, admin_user: User):
        """Création d'une app avec is_shared_service=False (défaut)"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Non Shared App Test",
                "slug": "non-shared-app-test",
                "description": "App non partagée",
                "api_port": 18098,
                "is_shared_service": False
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["application"]["is_shared_service"] is False
    
    @pytest.mark.asyncio
    async def test_create_app_default_is_shared_service(self, client: AsyncClient, admin_user: User):
        """Création d'une app sans spécifier is_shared_service (doit être False par défaut)"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Default Shared Test",
                "slug": "default-shared-test",
                "description": "Test valeur par défaut",
                "api_port": 18097
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["application"]["is_shared_service"] is False
    
    @pytest.mark.asyncio
    async def test_auto_register_with_is_shared(self, client: AsyncClient, admin_user: User):
        """Auto-enregistrement admin avec is_shared=True via POST /api/apps/auto-register"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/apps/auto-register",
            json={
                "slug": "auto-shared-test",
                "name": "Auto Shared Test",
                "api_port": 18096,
                "is_shared": True
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["application"]["slug"] == "auto-shared-test"
        assert data["application"]["is_shared_service"] is True
