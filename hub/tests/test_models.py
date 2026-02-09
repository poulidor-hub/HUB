"""
Tests des modèles de données
"""
import pytest
from datetime import datetime, timedelta, timezone

from app.models import (
    User, Application, UserAppRole, ServiceAccount,
    RefreshToken, RevokedToken, AuditLog, DEFAULT_ROLES
)
from app.auth import get_password_hash


class TestUserModel:
    """Tests pour le modèle User"""
    
    @pytest.mark.asyncio
    async def test_user_creation(self, async_session):
        """Test création d'un utilisateur"""
        user = User(
            username="newuser",
            email="new@example.com",
            hashed_password=get_password_hash("password123"),
            is_active=True,
            is_admin=False
        )
        async_session.add(user)
        await async_session.commit()
        await async_session.refresh(user)
        
        assert user.id is not None
        assert user.username == "newuser"
        assert user.is_active is True
        assert user.created_at is not None
    
    @pytest.mark.asyncio
    async def test_user_repr(self, test_user):
        """Test représentation string"""
        assert "testuser" in repr(test_user)
    
    @pytest.mark.asyncio
    async def test_user_get_apps_with_roles(self, async_session, test_user, test_app):
        """Test récupération des apps avec rôles"""
        # Ajouter un rôle
        role = UserAppRole(
            user_id=test_user.id,
            app_id=test_app.id,
            role="manager"
        )
        async_session.add(role)
        await async_session.commit()
        
        # Recharger l'utilisateur avec les relations
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        result = await async_session.execute(
            select(User)
            .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
            .where(User.id == test_user.id)
        )
        user = result.scalar_one()
        
        apps_roles = user.get_apps_with_roles()
        assert test_app.slug in apps_roles
        assert apps_roles[test_app.slug] == "manager"
    
    @pytest.mark.asyncio
    async def test_user_get_role_for_app(self, async_session, test_user, test_app):
        """Test récupération du rôle pour une app spécifique"""
        role = UserAppRole(
            user_id=test_user.id,
            app_id=test_app.id,
            role="admin"
        )
        async_session.add(role)
        await async_session.commit()
        
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        result = await async_session.execute(
            select(User)
            .options(selectinload(User.app_roles).selectinload(UserAppRole.application))
            .where(User.id == test_user.id)
        )
        user = result.scalar_one()
        
        assert user.get_role_for_app(test_app.slug) == "admin"
        assert user.get_role_for_app("unknown-app") is None


class TestApplicationModel:
    """Tests pour le modèle Application"""
    
    @pytest.mark.asyncio
    async def test_app_creation(self, async_session):
        """Test création d'une application"""
        app = Application(
            name="New App",
            slug="new-app",
            description="Description",
            api_url="http://localhost:9000",
            is_active=True
        )
        async_session.add(app)
        await async_session.commit()
        await async_session.refresh(app)
        
        assert app.id is not None
        assert app.slug == "new-app"
        assert app.is_active is True
    
    @pytest.mark.asyncio
    async def test_app_defaults(self, async_session):
        """Test valeurs par défaut"""
        app = Application(
            name="Test",
            slug="test",
            api_url="http://localhost:8000"
        )
        async_session.add(app)
        await async_session.commit()
        await async_session.refresh(app)
        
        assert app.icon == "bi-app"
        assert app.color == "#0d6efd"
        assert app.requires_auth is True
    
    @pytest.mark.asyncio
    async def test_app_repr(self, test_app):
        """Test représentation string"""
        assert "Test App" in repr(test_app)


class TestServiceAccountModel:
    """Tests pour le modèle ServiceAccount"""
    
    def test_generate_client_id(self):
        """Test génération client_id"""
        client_id = ServiceAccount.generate_client_id()
        assert client_id.startswith("sa_")
        assert len(client_id) >= 35  # sa_ + 32 chars hex
    
    def test_generate_client_secret(self):
        """Test génération client_secret"""
        secret = ServiceAccount.generate_client_secret()
        assert len(secret) > 40  # URL-safe base64
    
    def test_client_id_uniqueness(self):
        """Test unicité des client_id"""
        ids = [ServiceAccount.generate_client_id() for _ in range(100)]
        assert len(set(ids)) == 100  # Tous uniques
    
    @pytest.mark.asyncio
    async def test_service_account_creation(self, async_session):
        """Test création service account"""
        sa = ServiceAccount(
            name="Test SA",
            client_id=ServiceAccount.generate_client_id(),
            client_secret_hash=get_password_hash("secret"),
            description="Test service account"
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)
        
        assert sa.id is not None
        assert sa.is_active is True


class TestRefreshTokenModel:
    """Tests pour le modèle RefreshToken"""
    
    @pytest.mark.asyncio
    async def test_refresh_token_is_valid(self, async_session, test_user):
        """Test propriété is_valid"""
        # Token valide
        valid_token = RefreshToken(
            token_hash="valid_hash",
            user_id=test_user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7)
        )
        async_session.add(valid_token)
        await async_session.commit()
        await async_session.refresh(valid_token)
        
        assert valid_token.is_valid is True
    
    @pytest.mark.asyncio
    async def test_refresh_token_expired(self, async_session, test_user):
        """Test token expiré"""
        expired_token = RefreshToken(
            token_hash="expired_hash",
            user_id=test_user.id,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1)
        )
        async_session.add(expired_token)
        await async_session.commit()
        await async_session.refresh(expired_token)
        
        assert expired_token.is_valid is False
    
    @pytest.mark.asyncio
    async def test_refresh_token_revoked(self, async_session, test_user):
        """Test token révoqué"""
        revoked_token = RefreshToken(
            token_hash="revoked_hash",
            user_id=test_user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            revoked_at=datetime.now(timezone.utc)
        )
        async_session.add(revoked_token)
        await async_session.commit()
        await async_session.refresh(revoked_token)
        
        assert revoked_token.is_valid is False


class TestUserAppRoleModel:
    """Tests pour le modèle UserAppRole"""
    
    @pytest.mark.asyncio
    async def test_user_app_role_creation(self, async_session, test_user, test_app):
        """Test création d'un rôle user-app"""
        role = UserAppRole(
            user_id=test_user.id,
            app_id=test_app.id,
            role="viewer"
        )
        async_session.add(role)
        await async_session.commit()
        await async_session.refresh(role)
        
        assert role.id is not None
        assert role.role == "viewer"
    
    @pytest.mark.asyncio
    async def test_user_app_role_repr(self, async_session, test_user, test_app):
        """Test représentation string"""
        role = UserAppRole(
            user_id=test_user.id,
            app_id=test_app.id,
            role="admin"
        )
        async_session.add(role)
        await async_session.commit()
        
        repr_str = repr(role)
        assert str(test_user.id) in repr_str
        assert str(test_app.id) in repr_str


class TestDefaultRoles:
    """Tests pour les rôles par défaut"""
    
    def test_default_roles_exist(self):
        """Vérifie que les rôles par défaut sont définis"""
        assert "admin" in DEFAULT_ROLES
        assert "manager" in DEFAULT_ROLES
        assert "user" in DEFAULT_ROLES
        assert "viewer" in DEFAULT_ROLES
    
    def test_default_roles_count(self):
        """Vérifie le nombre de rôles par défaut"""
        assert len(DEFAULT_ROLES) == 4
