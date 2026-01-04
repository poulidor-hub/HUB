"""
Tests pour le remplacement automatique d'applications et la suppression en cascade des M2M

Teste :
- Remplacement automatique d'une app existante lors de l'auto-registration (FORCE_REPLACE_APP=true)
- Suppression en cascade des Service Accounts M2M lors de la suppression d'une app
- Suppression des fichiers credentials lors de la suppression
- Comportement avec FORCE_REPLACE_APP=false
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import create_access_token, get_password_hash
from app.models import User, Application, ServiceAccount, service_account_app_access, AuditLog
from app.routes.service_accounts import save_service_account_credentials, delete_service_account_credentials, SECRETS_DIR
from tests.conftest import get_auth_header


class TestAppAutoReplace:
    """Tests pour le remplacement automatique d'applications lors de l'auto-registration"""
    
    @pytest.mark.asyncio
    async def test_register_new_app(self, client: AsyncClient, async_session: AsyncSession):
        """Enregistrement d'une nouvelle application fonctionne normalement"""
        # Simuler une IP Docker
        with patch('app.routes.apps.is_docker_network_ip', return_value=True):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "new-test-app",
                    "api_port": 8080,
                    "frontend_port": 3000
                }
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["is_new"] == True
        assert "client_id" in data["credentials"]
        assert "client_secret" in data["credentials"]
        assert data["application"]["slug"] == "new-test-app"
    
    @pytest.mark.asyncio
    async def test_register_existing_app_with_force_replace(self, client: AsyncClient, async_session: AsyncSession):
        """
        Avec FORCE_REPLACE_APP=true, une app existante est remplacée automatiquement
        """
        # Créer une app existante avec un SA
        existing_app = Application(
            name="Existing App",
            slug="existing-app",
            api_url="http://localhost:8000",
            is_active=True
        )
        async_session.add(existing_app)
        await async_session.flush()
        
        old_sa = ServiceAccount(
            name="SA - Existing App",
            client_id="old_client_id_12345",
            client_secret_hash=get_password_hash("old_secret"),
            is_active=True
        )
        async_session.add(old_sa)
        await async_session.flush()
        
        # Associer le SA à l'app
        await async_session.execute(
            service_account_app_access.insert().values(
                service_account_id=old_sa.id,
                app_id=existing_app.id
            )
        )
        await async_session.commit()
        
        old_app_id = existing_app.id
        old_sa_id = old_sa.id
        old_client_id = old_sa.client_id
        
        # Simuler une IP Docker et FORCE_REPLACE_APP=true
        with patch('app.routes.apps.is_docker_network_ip', return_value=True), \
             patch('app.routes.apps.settings.force_replace_app', True):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "existing-app",  # Même slug
                    "api_port": 9999,
                    "frontend_port": 4000
                }
            )
        
        assert response.status_code == 200
        data = response.json()
        
        # Doit être considéré comme nouveau (remplacement)
        assert data["is_new"] == True
        assert "remplacée" in data["message"].lower()
        assert "client_secret" in data["credentials"]  # Nouveau secret fourni
        
        # Le nouveau client_id doit être différent de l'ancien
        new_client_id = data["credentials"]["client_id"]
        assert new_client_id != old_client_id
        
        # Expirer le cache de la session pour obtenir des données fraîches
        async_session.expire_all()
        
        # Vérifier que l'ancien SA est supprimé (via client_id)
        result = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.client_id == old_client_id)
        )
        old_sa_check = result.scalar_one_or_none()
        assert old_sa_check is None
        
        # Vérifier qu'une nouvelle app existe avec le même slug
        result = await async_session.execute(
            select(Application).where(Application.slug == "existing-app")
        )
        new_app = result.scalar_one_or_none()
        assert new_app is not None
        
        # Vérifier qu'un nouveau SA existe avec le nouveau client_id
        result = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.client_id == new_client_id)
        )
        new_sa = result.scalar_one_or_none()
        assert new_sa is not None
    
    @pytest.mark.asyncio
    async def test_register_existing_app_without_force_replace(self, client: AsyncClient, async_session: AsyncSession):
        """
        Avec FORCE_REPLACE_APP=false, une app existante retourne is_new=False
        """
        # Créer une app existante
        existing_app = Application(
            name="Keep Me App",
            slug="keep-me-app",
            api_url="http://localhost:8000",
            is_active=True
        )
        async_session.add(existing_app)
        await async_session.commit()
        
        # Simuler une IP Docker et FORCE_REPLACE_APP=false
        with patch('app.routes.apps.is_docker_network_ip', return_value=True), \
             patch('app.routes.apps.settings.force_replace_app', False):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "keep-me-app",
                    "api_port": 8080
                }
            )
        
        assert response.status_code == 200
        data = response.json()
        
        # Ne doit PAS être remplacé
        assert data["is_new"] == False
        assert "déjà enregistrée" in data["message"].lower()
        # Pas de client_secret fourni (seulement client_id si SA existe)
        if data.get("credentials"):
            assert "client_secret" not in data["credentials"]
    
    @pytest.mark.asyncio
    async def test_replace_creates_audit_log(self, client: AsyncClient, async_session: AsyncSession):
        """
        Le remplacement automatique crée une entrée dans l'audit log
        """
        # Créer une app existante
        existing_app = Application(
            name="Audit Test App",
            slug="audit-test-app",
            api_url="http://localhost:8000",
            is_active=True
        )
        async_session.add(existing_app)
        await async_session.commit()
        
        # Simuler le remplacement
        with patch('app.routes.apps.is_docker_network_ip', return_value=True), \
             patch('app.routes.apps.settings.force_replace_app', True):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "audit-test-app",
                    "api_port": 8080
                }
            )
        
        assert response.status_code == 200
        
        # Vérifier l'audit log
        result = await async_session.execute(
            select(AuditLog).where(
                AuditLog.action == "replace_app",
                AuditLog.target == "audit-test-app"
            )
        )
        audit_entry = result.scalar_one_or_none()
        assert audit_entry is not None
        assert audit_entry.user_id is None  # Action système
        assert "remplacement automatique" in audit_entry.details.lower()


class TestAppDeleteCascade:
    """Tests pour la suppression en cascade des M2M lors de la suppression d'une app"""
    
    @pytest.mark.asyncio
    async def test_delete_app_removes_owner_sa(self, client: AsyncClient, admin_user: User, async_session: AsyncSession):
        """
        La suppression d'une app supprime aussi son Service Account propriétaire
        """
        # Créer une app avec son SA
        app = Application(
            name="App With SA",
            slug="app-with-sa",
            api_url="http://localhost:8000",
            is_active=True
        )
        async_session.add(app)
        await async_session.flush()
        
        owner_sa = ServiceAccount(
            name="SA - App With SA",  # Pattern qui identifie le SA comme propriétaire
            client_id="owner_sa_12345",
            client_secret_hash=get_password_hash("secret"),
            is_active=True
        )
        async_session.add(owner_sa)
        await async_session.flush()
        
        # Associer le SA à l'app
        await async_session.execute(
            service_account_app_access.insert().values(
                service_account_id=owner_sa.id,
                app_id=app.id
            )
        )
        await async_session.commit()
        
        app_id = app.id
        sa_id = owner_sa.id
        
        # Supprimer l'app via l'API admin
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.delete(
            f"/api/apps/{app_id}",
            headers=get_auth_header(token)
        )
        
        assert response.status_code in [200, 204]
        
        # Vérifier que le SA est supprimé
        result = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.id == sa_id)
        )
        deleted_sa = result.scalar_one_or_none()
        assert deleted_sa is None
    
    @pytest.mark.asyncio
    async def test_delete_app_keeps_non_owner_sa(self, client: AsyncClient, admin_user: User, async_session: AsyncSession):
        """
        La suppression d'une app ne supprime PAS les SA qui ont juste accès (pas propriétaires)
        """
        # Créer une app
        app = Application(
            name="Shared App",
            slug="shared-app",
            api_url="http://localhost:8000",
            is_active=True
        )
        async_session.add(app)
        await async_session.flush()
        
        # Créer un SA qui a accès mais n'est pas propriétaire
        other_sa = ServiceAccount(
            name="SA - Other App",  # Nom différent, pas propriétaire
            client_id="other_sa_12345",
            client_secret_hash=get_password_hash("secret"),
            is_active=True
        )
        async_session.add(other_sa)
        await async_session.flush()
        
        # Donner accès à l'app
        await async_session.execute(
            service_account_app_access.insert().values(
                service_account_id=other_sa.id,
                app_id=app.id
            )
        )
        await async_session.commit()
        
        app_id = app.id
        sa_id = other_sa.id
        
        # Supprimer l'app
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.delete(
            f"/api/apps/{app_id}",
            headers=get_auth_header(token)
        )
        
        assert response.status_code in [200, 204]
        
        # Le SA doit toujours exister
        result = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.id == sa_id)
        )
        kept_sa = result.scalar_one_or_none()
        assert kept_sa is not None
    
    @pytest.mark.asyncio
    async def test_delete_app_creates_audit_with_sa_details(self, client: AsyncClient, admin_user: User, async_session: AsyncSession):
        """
        L'audit log de suppression inclut les détails des SA supprimés
        """
        # Créer une app avec son SA
        app = Application(
            name="Audit Delete App",
            slug="audit-delete-app",
            api_url="http://localhost:8000",
            is_active=True
        )
        async_session.add(app)
        await async_session.flush()
        
        sa = ServiceAccount(
            name="SA - Audit Delete App",
            client_id="audit_sa_12345",
            client_secret_hash=get_password_hash("secret"),
            is_active=True
        )
        async_session.add(sa)
        await async_session.flush()
        
        await async_session.execute(
            service_account_app_access.insert().values(
                service_account_id=sa.id,
                app_id=app.id
            )
        )
        await async_session.commit()
        
        app_id = app.id
        
        # Supprimer l'app
        token, _, _ = create_access_token(
            username=admin_user.username,
            roles=["admin"],
            apps={}
        )
        
        response = await client.delete(
            f"/api/apps/{app_id}",
            headers=get_auth_header(token)
        )
        
        assert response.status_code in [200, 204]
        
        # Vérifier l'audit log
        result = await async_session.execute(
            select(AuditLog).where(
                AuditLog.action == "delete_app",
                AuditLog.target == "audit-delete-app"
            )
        )
        audit_entry = result.scalar_one_or_none()
        assert audit_entry is not None
        assert "SA - Audit Delete App" in audit_entry.details


class TestCredentialsFileManagement:
    """Tests pour la gestion des fichiers credentials (.env)"""
    
    def test_delete_service_account_credentials_existing_file(self, tmp_path):
        """
        delete_service_account_credentials supprime le fichier s'il existe
        """
        # Créer un fichier temporaire
        with patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'unlink') as mock_unlink:
            # Patch SECRETS_DIR pour le test
            with patch('app.routes.service_accounts.SECRETS_DIR', tmp_path):
                # Créer le fichier
                test_file = tmp_path / "sa_test-app.env"
                test_file.write_text("CLIENT_ID=test\nCLIENT_SECRET=secret")
                
                result = delete_service_account_credentials("test-app")
                
                # Le fichier doit être supprimé
                assert not test_file.exists() or result == True
    
    def test_delete_service_account_credentials_nonexistent_file(self):
        """
        delete_service_account_credentials retourne False si le fichier n'existe pas
        """
        with patch('app.routes.service_accounts.SECRETS_DIR', Path("/nonexistent")):
            result = delete_service_account_credentials("nonexistent-app")
            assert result == False
    
    def test_save_and_delete_credentials_cycle(self, tmp_path):
        """
        Test du cycle complet: save puis delete
        """
        with patch('app.routes.service_accounts.SECRETS_DIR', tmp_path):
            # Sauvegarder
            save_service_account_credentials(
                name="cycle-test",
                client_id="test_client_id",
                client_secret="test_client_secret",
                app_slugs=["cycle-test"]
            )
            
            # Vérifier que le fichier existe
            expected_file = tmp_path / "sa_cycle-test.env"
            assert expected_file.exists()
            
            # Supprimer
            result = delete_service_account_credentials("cycle-test")
            assert result == True
            assert not expected_file.exists()


class TestAutoRegistrationFromNonDockerIP:
    """Tests pour l'auto-registration depuis une IP non-Docker"""
    
    @pytest.mark.asyncio
    async def test_register_from_non_docker_ip_forbidden(self, client: AsyncClient):
        """
        L'auto-registration depuis une IP non-Docker est refusée
        """
        # Sans mock, l'IP sera celle du test (127.0.0.1 ou localhost)
        with patch('app.routes.apps.is_docker_network_ip', return_value=False):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "forbidden-app",
                    "api_port": 8080
                }
            )
        
        assert response.status_code == 403
        assert "docker" in response.json()["detail"].lower()
