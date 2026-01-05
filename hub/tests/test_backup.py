"""
Tests pour le système de backup/restore du Hub
Vérifie l'export et l'import complet des données
"""
import pytest
from datetime import datetime
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Application, ServiceAccount, UserAppRole, ApprovalStatus
from app.auth import get_password_hash


class TestBackupExport:
    """Tests pour l'export de backup"""
    
    @pytest.mark.asyncio
    async def test_export_requires_admin(self, client: AsyncClient, async_session: AsyncSession):
        """L'export nécessite un utilisateur admin"""
        # Créer un utilisateur non-admin
        user = User(
            username="user_normal",
            hashed_password=get_password_hash("password123"),
            is_admin=False
        )
        async_session.add(user)
        await async_session.commit()
        
        # Login
        login_response = await client.post("/api/auth/token", data={
            "grant_type": "password",
            "username": "user_normal",
            "password": "password123"
        })
        token = login_response.json()["access_token"]
        
        # Tenter l'export
        response = await client.get(
            "/api/admin/backup/export",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_export_full_data(self, client: AsyncClient, async_session: AsyncSession, admin_token: str):
        """L'export contient toutes les données nécessaires"""
        # Créer des données de test
        admin = User(
            username="admin_backup",
            email="admin@test.com",
            hashed_password=get_password_hash("admin123"),
            is_admin=True,
            must_change_password=True,
            can_change_password=False
        )
        async_session.add(admin)
        
        user = User(
            username="test_user",
            email="test@test.com",
            hashed_password=get_password_hash("test123"),
            is_admin=False,
            must_change_password=False,
            can_change_password=True
        )
        async_session.add(user)
        
        app = Application(
            name="Test App",
            slug="test-app",
            description="Application de test",
            icon="bi-gear",
            color="#ff0000",
            api_url="http://localhost:8080",
            frontend_url="http://localhost:3000",
            is_active=True,
            is_shared_service=True,
            approval_status=ApprovalStatus.approved
        )
        async_session.add(app)
        await async_session.flush()
        
        # Association user-app
        uar = UserAppRole(user_id=user.id, app_id=app.id, role="admin")
        async_session.add(uar)
        
        # Service Account
        sa = ServiceAccount(
            name="SA Test",
            client_id="sa_test_123",
            client_secret_hash=get_password_hash("secret123"),
            description="Service account de test"
        )
        async_session.add(sa)
        await async_session.commit()
        
        # Export
        response = await client.get(
            "/api/admin/backup/export",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code == 200
        
        data = response.json()
        
        # Vérifier la structure
        assert "version" in data
        assert "exported_at" in data
        assert "users" in data
        assert "applications" in data
        assert "service_accounts" in data
        assert "user_app_roles" in data
        
        # Vérifier les utilisateurs avec les nouveaux champs
        users_by_name = {u["username"]: u for u in data["users"]}
        assert "admin_backup" in users_by_name
        admin_export = users_by_name["admin_backup"]
        assert admin_export["must_change_password"] == True
        assert admin_export["can_change_password"] == False
        assert admin_export["hashed_password"].startswith("$2b$")
        
        test_user_export = users_by_name["test_user"]
        assert test_user_export["must_change_password"] == False
        assert test_user_export["can_change_password"] == True
        
        # Vérifier les applications avec les nouveaux champs
        apps_by_slug = {a["slug"]: a for a in data["applications"]}
        assert "test-app" in apps_by_slug
        app_export = apps_by_slug["test-app"]
        assert app_export["icon"] == "bi-gear"
        assert app_export["color"] == "#ff0000"
        assert app_export["is_shared_service"] == True
        assert app_export["approval_status"] == "approved"
    
    @pytest.mark.asyncio
    async def test_export_without_jwt_keys(self, client: AsyncClient, admin_token: str):
        """L'export peut exclure les clés JWT"""
        response = await client.get(
            "/api/admin/backup/export?include_jwt_keys=false",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("jwt_private_key_b64") is None
        assert data.get("jwt_public_key_b64") is None


class TestBackupImport:
    """Tests pour l'import de backup"""
    
    @pytest.mark.asyncio
    async def test_import_requires_admin(self, client: AsyncClient, async_session: AsyncSession):
        """L'import nécessite un utilisateur admin"""
        # Créer un utilisateur non-admin
        user = User(
            username="user_import",
            hashed_password=get_password_hash("password123"),
            is_admin=False
        )
        async_session.add(user)
        await async_session.commit()
        
        # Login
        login_response = await client.post("/api/auth/token", data={
            "grant_type": "password",
            "username": "user_import",
            "password": "password123"
        })
        token = login_response.json()["access_token"]
        
        # Tenter l'import
        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {token}"},
            json={"users": [], "applications": []}
        )
        assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_import_creates_users_with_new_fields(self, client: AsyncClient, async_session: AsyncSession, admin_token: str):
        """L'import crée des utilisateurs avec les nouveaux champs"""
        backup_data = {
            "import_mode": "skip",
            "users": [
                {
                    "username": "imported_user",
                    "email": "imported@test.com",
                    "hashed_password": get_password_hash("imported123"),
                    "is_active": True,
                    "is_admin": False,
                    "must_change_password": True,
                    "can_change_password": False
                }
            ],
            "applications": [],
            "service_accounts": [],
            "user_app_roles": []
        }
        
        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=backup_data
        )
        assert response.status_code == 200
        
        result = response.json()
        assert result["success"] == True
        assert result["users_created"] == 1
        
        # Vérifier les données en base
        user_result = await async_session.execute(
            select(User).where(User.username == "imported_user")
        )
        user = user_result.scalar_one()
        assert user.must_change_password == True
        assert user.can_change_password == False
    
    @pytest.mark.asyncio
    async def test_import_creates_apps_with_approval_status(self, client: AsyncClient, async_session: AsyncSession, admin_token: str):
        """L'import crée des applications avec le statut d'approbation"""
        backup_data = {
            "import_mode": "skip",
            "users": [],
            "applications": [
                {
                    "name": "App Importée",
                    "slug": "imported-app",
                    "description": "Description test",
                    "icon": "bi-star",
                    "color": "#00ff00",
                    "api_url": "http://localhost:9000",
                    "is_active": True,
                    "requires_auth": True,
                    "is_shared_service": False,
                    "approval_status": "pending",
                    "rejection_reason": None,
                    "available_roles": [
                        {"name": "admin", "description": "Administrateur"}
                    ]
                }
            ],
            "service_accounts": [],
            "user_app_roles": []
        }
        
        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=backup_data
        )
        assert response.status_code == 200
        
        result = response.json()
        assert result["success"] == True
        assert result["apps_created"] == 1
        
        # Vérifier les données en base
        app_result = await async_session.execute(
            select(Application).where(Application.slug == "imported-app")
        )
        app = app_result.scalar_one()
        assert app.icon == "bi-star"
        assert app.color == "#00ff00"
        assert app.approval_status == ApprovalStatus.pending
    
    @pytest.mark.asyncio
    async def test_import_mode_skip(self, client: AsyncClient, async_session: AsyncSession, admin_token: str):
        """Le mode skip ignore les entités existantes"""
        # Créer une app existante
        existing_app = Application(
            name="Existing App",
            slug="existing-app",
            api_url="http://localhost:1000",
            color="#111111"
        )
        async_session.add(existing_app)
        await async_session.commit()
        
        # Importer avec le même slug mais des données différentes
        backup_data = {
            "import_mode": "skip",
            "users": [],
            "applications": [
                {
                    "name": "Nouvelle App",
                    "slug": "existing-app",  # Même slug
                    "api_url": "http://localhost:2000",
                    "color": "#999999"
                }
            ],
            "service_accounts": [],
            "user_app_roles": []
        }
        
        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=backup_data
        )
        assert response.status_code == 200
        
        result = response.json()
        assert result["apps_skipped"] == 1
        assert result["apps_created"] == 0
        
        # Vérifier que l'app n'a pas changé
        await async_session.refresh(existing_app)
        assert existing_app.name == "Existing App"
        assert existing_app.color == "#111111"
    
    @pytest.mark.asyncio
    async def test_import_mode_update(self, client: AsyncClient, async_session: AsyncSession, admin_token: str):
        """Le mode update met à jour les entités existantes"""
        # Créer un utilisateur existant
        existing_user = User(
            username="update_user",
            hashed_password=get_password_hash("old_password"),
            email="old@test.com",
            must_change_password=False,
            can_change_password=True
        )
        async_session.add(existing_user)
        await async_session.commit()
        
        # Importer avec mise à jour
        backup_data = {
            "import_mode": "update",
            "users": [
                {
                    "username": "update_user",
                    "email": "new@test.com",
                    "hashed_password": get_password_hash("new_password"),
                    "is_active": True,
                    "is_admin": False,
                    "must_change_password": True,
                    "can_change_password": False
                }
            ],
            "applications": [],
            "service_accounts": [],
            "user_app_roles": []
        }
        
        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=backup_data
        )
        assert response.status_code == 200
        
        result = response.json()
        assert result["users_updated"] == 1
        
        # Vérifier les mises à jour
        await async_session.refresh(existing_user)
        assert existing_user.email == "new@test.com"
        assert existing_user.must_change_password == True
        assert existing_user.can_change_password == False


class TestBackupRoundTrip:
    """Tests de bout en bout : export puis import"""
    
    @pytest.mark.asyncio
    async def test_export_import_roundtrip(self, client: AsyncClient, async_session: AsyncSession, admin_token: str):
        """L'export puis import préserve toutes les données"""
        # Créer des données complètes
        user = User(
            username="roundtrip_user",
            email="roundtrip@test.com",
            hashed_password=get_password_hash("roundtrip123"),
            is_admin=False,
            must_change_password=True,
            can_change_password=False
        )
        async_session.add(user)
        
        app = Application(
            name="Roundtrip App",
            slug="roundtrip-app",
            description="Test roundtrip",
            icon="bi-arrow-repeat",
            color="#abcdef",
            api_url="http://localhost:7777",
            is_shared_service=True,
            approval_status=ApprovalStatus.approved
        )
        async_session.add(app)
        await async_session.commit()
        
        # Export
        export_response = await client.get(
            "/api/admin/backup/export?include_jwt_keys=false",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert export_response.status_code == 200
        exported_data = export_response.json()
        
        # Supprimer les données
        await async_session.delete(app)
        await async_session.delete(user)
        await async_session.commit()
        
        # Préparer pour l'import
        import_data = {
            "import_mode": "skip",
            "users": exported_data["users"],
            "applications": exported_data["applications"],
            "service_accounts": exported_data.get("service_accounts", []),
            "user_app_roles": exported_data.get("user_app_roles", [])
        }
        
        # Import
        import_response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=import_data
        )
        assert import_response.status_code == 200
        
        result = import_response.json()
        assert result["success"] == True
        
        # Vérifier que les données sont restaurées
        user_result = await async_session.execute(
            select(User).where(User.username == "roundtrip_user")
        )
        restored_user = user_result.scalar_one_or_none()
        assert restored_user is not None
        assert restored_user.email == "roundtrip@test.com"
        assert restored_user.must_change_password == True
        assert restored_user.can_change_password == False
        
        app_result = await async_session.execute(
            select(Application).where(Application.slug == "roundtrip-app")
        )
        restored_app = app_result.scalar_one_or_none()
        assert restored_app is not None
        assert restored_app.icon == "bi-arrow-repeat"
        assert restored_app.color == "#abcdef"
        assert restored_app.is_shared_service == True


# Fixture pour le token admin
@pytest.fixture
async def admin_token(client: AsyncClient, async_session: AsyncSession) -> str:
    """Crée un admin et retourne son token"""
    admin = User(
        username="test_admin",
        hashed_password=get_password_hash("admin_password"),
        is_admin=True
    )
    async_session.add(admin)
    await async_session.commit()
    
    response = await client.post("/api/auth/token", data={
        "grant_type": "password",
        "username": "test_admin",
        "password": "admin_password"
    })
    return response.json()["access_token"]
