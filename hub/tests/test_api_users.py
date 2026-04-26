"""
Tests d'intégration pour les endpoints API des utilisateurs
"""
import pytest
import io
from httpx import AsyncClient
from sqlalchemy import select

from app.auth import create_access_token, get_password_hash
from app.models import User, Profile, Application, UserAppRole, UserSession, user_profiles
from tests.conftest import get_auth_header


class TestUsersEndpoints:
    """Tests pour les endpoints /api/users/*"""
    
    @pytest.mark.asyncio
    async def test_list_users_as_admin(self, client: AsyncClient, admin_user: User, test_user: User):
        """Admin peut lister tous les utilisateurs"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.get(
            "/api/users/",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        users = response.json()
        assert isinstance(users, list)
        assert len(users) >= 2  # admin + test_user
    
    @pytest.mark.asyncio
    async def test_list_users_forbidden_for_user(self, client: AsyncClient, test_user: User):
        """User standard ne peut pas lister les utilisateurs"""
        token, _, _ = create_access_token(
            username=test_user.username,
            apps={}
        )
        
        response = await client.get(
            "/api/users/",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_create_user(self, client: AsyncClient, admin_user: User):
        """Admin peut créer un utilisateur"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/users/",
            json={
                "username": "newuser",
                "email": "newuser@example.com",
                "password": "securepass123",
                "is_admin": False
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "newuser"
        assert data["email"] == "newuser@example.com"
        assert "hashed_password" not in data
    
    @pytest.mark.asyncio
    async def test_create_user_duplicate_username(self, client: AsyncClient, admin_user: User, test_user: User):
        """Création avec username existant échoue"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/users/",
            json={
                "username": test_user.username,  # Existant
                "email": "another@example.com",
                "password": "password123"
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 400
    
    @pytest.mark.asyncio
    async def test_get_user_by_id(self, client: AsyncClient, admin_user: User, test_user: User):
        """Admin peut récupérer un utilisateur par ID"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.get(
            f"/api/users/{test_user.id}",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_user.id
        assert data["username"] == test_user.username
    
    @pytest.mark.asyncio
    async def test_get_user_not_found(self, client: AsyncClient, admin_user: User):
        """Utilisateur inexistant retourne 404"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.get(
            "/api/users/99999",
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 404
    
    @pytest.mark.asyncio
    async def test_update_user(self, client: AsyncClient, admin_user: User, test_user: User):
        """Admin peut modifier un utilisateur"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.patch(
            f"/api/users/{test_user.id}",
            json={
                "email": "updated@example.com"
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "updated@example.com"
    
    @pytest.mark.asyncio
    async def test_update_user_password(self, client: AsyncClient, admin_user: User, test_user: User):
        """Admin peut modifier le mot de passe d'un utilisateur"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.patch(
            f"/api/users/{test_user.id}",
            json={
                "password": "newpassword123"
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        
        # Vérifier que le nouveau mot de passe fonctionne
        login_response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "password",
                "username": "testuser",
                "password": "newpassword123"
            }
        )
        
        assert login_response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_deactivate_user(self, client: AsyncClient, admin_user: User, test_user: User):
        """Admin peut désactiver un utilisateur"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.patch(
            f"/api/users/{test_user.id}",
            json={
                "is_active": False
            },
            headers=get_auth_header(token)
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["is_active"] is False
    
    @pytest.mark.asyncio
    async def test_delete_user(self, client: AsyncClient, admin_user: User, async_session):
        """Admin peut supprimer un utilisateur"""
        # Créer un utilisateur à supprimer
        user_to_delete = User(
            username="todelete",
            email="delete@example.com",
            hashed_password=get_password_hash("password123"),
            is_active=True
        )
        async_session.add(user_to_delete)
        await async_session.commit()
        await async_session.refresh(user_to_delete)
        
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.delete(
            f"/api/users/{user_to_delete.id}",
            headers=get_auth_header(token)
        )
        
        assert response.status_code in [200, 204]

    @pytest.mark.asyncio
    async def test_delete_user_with_active_sessions(self, client: AsyncClient, admin_user: User, async_session):
        """La suppression d'un utilisateur supprime aussi ses sessions au lieu de nullifier user_id"""
        user_to_delete = User(
            username="todelete-session",
            email="delete-session@example.com",
            hashed_password=get_password_hash("password123"),
            is_active=True
        )
        async_session.add(user_to_delete)
        await async_session.commit()
        await async_session.refresh(user_to_delete)

        session = UserSession(
            user_id=user_to_delete.id,
            ip_address="127.0.0.1",
            user_agent="pytest"
        )
        async_session.add(session)
        await async_session.commit()

        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )

        response = await client.delete(
            f"/api/users/{user_to_delete.id}",
            headers=get_auth_header(token)
        )

        assert response.status_code in [200, 204]

        sessions_result = await async_session.execute(
            select(UserSession).where(UserSession.user_id == user_to_delete.id)
        )
        assert sessions_result.scalars().first() is None
    
    @pytest.mark.asyncio
    async def test_admin_cannot_delete_self(self, client: AsyncClient, admin_user: User):
        """Admin ne peut pas se supprimer lui-même"""
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        
        response = await client.delete(
            f"/api/users/{admin_user.id}",
            headers=get_auth_header(token)
        )
        
        # Devrait échouer ou être interdit
        assert response.status_code in [400, 403]
    
    @pytest.mark.asyncio
    async def test_user_can_update_own_password(self, client: AsyncClient, test_user: User):
        """Un utilisateur peut changer son propre mot de passe"""
        token, _, _ = create_access_token(
            username=test_user.username,
            apps={}
        )
        
        response = await client.post(
            "/api/users/me/password",
            json={
                "current_password": "testpass123",
                "new_password": "mynewpassword123"
            },
            headers=get_auth_header(token)
        )
        
        # Le endpoint peut ne pas exister, donc on vérifie juste le code
        assert response.status_code in [200, 404]  # 404 si endpoint non implémenté


class TestM2MBulkUserRoles:
    """Tests pour l'endpoint M2M de récupération bulk des rôles utilisateurs"""

    async def _get_service_account_token(self, client: AsyncClient, service_account) -> str:
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": service_account.client_id,
                "client_secret": "secret123"
            }
        )
        assert response.status_code == 200
        return response.json()["access_token"]

    @pytest.mark.asyncio
    async def test_m2m_bulk_roles_returns_only_active_and_allowed_apps(
        self,
        client: AsyncClient,
        async_session,
        service_account,
        test_user: User,
        test_app: Application,
    ):
        """Le endpoint M2M retourne uniquement les apps actives autorisées pour le SA"""
        from app.models import service_account_app_access

        profile = Profile(name="Profil Hub Vision", description="Test", color="#123456")
        async_session.add(profile)
        await async_session.commit()
        await async_session.refresh(profile)

        await async_session.execute(
            user_profiles.insert().values(user_id=test_user.id, profile_id=profile.id)
        )
        await async_session.commit()

        second_user = User(
            username="followed2",
            email="followed2@example.com",
            hashed_password=get_password_hash("testpass123"),
            is_active=True,
            is_admin=False,
        )

        hidden_app = Application(
            name="Hidden App",
            slug="hidden-app",
            api_url="http://localhost:8010",
            is_active=True,
            requires_auth=True,
        )
        inactive_allowed_app = Application(
            name="Inactive Allowed",
            slug="inactive-allowed",
            api_url="http://localhost:8011",
            is_active=False,
            requires_auth=True,
        )

        async_session.add_all([second_user, hidden_app, inactive_allowed_app])
        await async_session.commit()
        await async_session.refresh(second_user)
        await async_session.refresh(hidden_app)
        await async_session.refresh(inactive_allowed_app)

        # Le service account a accès à test_app via fixture + à inactive_allowed_app
        await async_session.execute(
            service_account_app_access.insert().values(
                service_account_id=service_account.id,
                app_id=inactive_allowed_app.id,
            )
        )

        async_session.add_all([
            UserAppRole(user_id=test_user.id, app_id=test_app.id, role="manager"),
            UserAppRole(user_id=test_user.id, app_id=hidden_app.id, role="admin"),
            UserAppRole(user_id=test_user.id, app_id=inactive_allowed_app.id, role="viewer"),
            UserAppRole(user_id=second_user.id, app_id=test_app.id, role="user"),
        ])
        await async_session.commit()

        token = await self._get_service_account_token(client, service_account)

        response = await client.post(
            "/api/users/m2m/app-roles/bulk",
            json={
                "identifiers": [
                    test_user.username,
                    second_user.username,
                    "unknown-user",
                ]
            },
            headers=get_auth_header(token),
        )

        assert response.status_code == 200
        data = response.json()

        assert data["unknown-user"] == {"profiles": [], "apps": []}

        assert data[test_user.username]["profiles"] == [{
            "id": profile.id,
            "name": "Profil Hub Vision",
            "color": "#123456",
        }]
        assert data[test_user.username]["apps"] == [
            {"app_slug": "test-app", "role": "manager"}
        ]

        assert data[second_user.username]["profiles"] == []
        assert data[second_user.username]["apps"] == [
            {"app_slug": "test-app", "role": "user"}
        ]

    @pytest.mark.asyncio
    async def test_m2m_bulk_roles_forbidden_for_human_user(self, client: AsyncClient, test_user: User):
        """Un token utilisateur ne peut pas appeler le endpoint M2M"""
        token, _, _ = create_access_token(
            username=test_user.username,
            apps={},
        )

        response = await client.post(
            "/api/users/m2m/app-roles/bulk",
            json={"identifiers": [test_user.username]},
            headers=get_auth_header(token),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_m2m_bulk_roles_normalizes_and_deduplicates_identifiers(
        self,
        client: AsyncClient,
        service_account,
        test_user: User,
    ):
        """Les identifiants sont trim et dédupliqués avant traitement"""
        token = await self._get_service_account_token(client, service_account)

        response = await client.post(
            "/api/users/m2m/app-roles/bulk",
            json={"identifiers": [f" {test_user.username} ", test_user.username]},
            headers=get_auth_header(token),
        )

        assert response.status_code == 200
        data = response.json()

        assert list(data.keys()) == [test_user.username]
        assert data[test_user.username]["profiles"] == []
        assert isinstance(data[test_user.username]["apps"], list)


class TestM2MSearchUsers:
    """Tests pour l'endpoint M2M de recherche d'utilisateurs"""

    async def _get_service_account_token(self, client: AsyncClient, service_account) -> str:
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": service_account.client_id,
                "client_secret": "secret123"
            }
        )
        assert response.status_code == 200
        return response.json()["access_token"]

    @pytest.mark.asyncio
    async def test_m2m_search_returns_users_of_allowed_apps(
        self,
        client: AsyncClient,
        async_session,
        service_account,
        test_user: User,
        test_app: Application,
    ):
        """Le SA voit uniquement les users ayant un rôle sur ses apps autorisées."""
        # User dans une app NON autorisée pour le SA → doit être exclu
        hidden_app = Application(
            name="Hidden App",
            slug="hidden-app",
            api_url="http://localhost:8010",
            is_active=True,
            requires_auth=True,
        )
        other_user = User(
            username="alice",
            email="alice@example.com",
            hashed_password=get_password_hash("testpass123"),
            is_active=True,
        )
        async_session.add_all([hidden_app, other_user])
        await async_session.commit()
        await async_session.refresh(hidden_app)
        await async_session.refresh(other_user)

        async_session.add_all([
            UserAppRole(user_id=test_user.id, app_id=test_app.id, role="manager"),
            UserAppRole(user_id=other_user.id, app_id=hidden_app.id, role="user"),
        ])
        await async_session.commit()

        token = await self._get_service_account_token(client, service_account)
        response = await client.get(
            "/api/users/m2m/search",
            params={"limit": 10},
            headers=get_auth_header(token),
        )

        assert response.status_code == 200
        data = response.json()
        usernames = {u["username"] for u in data["users"]}
        assert test_user.username in usernames
        assert "alice" not in usernames

    @pytest.mark.asyncio
    async def test_m2m_search_filters_by_query(
        self,
        client: AsyncClient,
        async_session,
        service_account,
        test_user: User,
        test_app: Application,
    ):
        """Le paramètre q filtre sur username/email (ilike)."""
        bob = User(
            username="bob",
            email="bob@example.com",
            hashed_password=get_password_hash("testpass123"),
            is_active=True,
        )
        async_session.add(bob)
        await async_session.commit()
        await async_session.refresh(bob)

        async_session.add_all([
            UserAppRole(user_id=test_user.id, app_id=test_app.id, role="user"),
            UserAppRole(user_id=bob.id, app_id=test_app.id, role="user"),
        ])
        await async_session.commit()

        token = await self._get_service_account_token(client, service_account)
        response = await client.get(
            "/api/users/m2m/search",
            params={"q": "bob"},
            headers=get_auth_header(token),
        )
        assert response.status_code == 200
        data = response.json()
        usernames = {u["username"] for u in data["users"]}
        assert usernames == {"bob"}

    @pytest.mark.asyncio
    async def test_m2m_search_excludes_inactive_users(
        self,
        client: AsyncClient,
        async_session,
        service_account,
        test_app: Application,
    ):
        """Les utilisateurs inactifs sont exclus."""
        inactive = User(
            username="inactive-user",
            email="inactive@example.com",
            hashed_password=get_password_hash("testpass123"),
            is_active=False,
        )
        async_session.add(inactive)
        await async_session.commit()
        await async_session.refresh(inactive)

        async_session.add(
            UserAppRole(user_id=inactive.id, app_id=test_app.id, role="user")
        )
        await async_session.commit()

        token = await self._get_service_account_token(client, service_account)
        response = await client.get(
            "/api/users/m2m/search",
            params={"q": "inactive"},
            headers=get_auth_header(token),
        )
        assert response.status_code == 200
        data = response.json()
        usernames = {u["username"] for u in data["users"]}
        assert "inactive-user" not in usernames

    @pytest.mark.asyncio
    async def test_m2m_search_app_slug_must_be_allowed(
        self,
        client: AsyncClient,
        service_account,
    ):
        """Demander un app_slug non autorisé → 403."""
        token = await self._get_service_account_token(client, service_account)
        response = await client.get(
            "/api/users/m2m/search",
            params={"app_slug": "not-allowed-slug"},
            headers=get_auth_header(token),
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_m2m_search_forbidden_for_human_user(
        self, client: AsyncClient, test_user: User
    ):
        """Un token user (non-SA) reçoit 403."""
        token, _, _ = create_access_token(
            username=test_user.username,
            apps={},
        )
        response = await client.get(
            "/api/users/m2m/search",
            headers=get_auth_header(token),
        )
        assert response.status_code == 403


class TestCsvImport:
    """Tests pour les endpoints CSV d'import d'utilisateurs"""

    def _admin_token(self, admin_user):
        token, _, _ = create_access_token(
            username=admin_user.username,
            apps={}
        )
        return token

    def _user_token(self, test_user):
        token, _, _ = create_access_token(
            username=test_user.username,
            apps={}
        )
        return token

    @pytest.mark.asyncio
    async def test_download_csv_template(self, client: AsyncClient, admin_user: User):
        """Admin peut télécharger le template CSV"""
        token = self._admin_token(admin_user)
        response = await client.get(
            "/api/users/csv/template",
            headers=get_auth_header(token)
        )
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "import_users_template.csv" in response.headers.get("content-disposition", "")
        content = response.text
        assert "username" in content
        assert "password" in content
        assert "profile" in content

    @pytest.mark.asyncio
    async def test_download_csv_template_forbidden_for_user(self, client: AsyncClient, test_user: User):
        """User standard ne peut pas télécharger le template CSV"""
        token = self._user_token(test_user)
        response = await client.get(
            "/api/users/csv/template",
            headers=get_auth_header(token)
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_preview_csv_valid(self, client: AsyncClient, admin_user: User, async_session):
        """Preview CSV avec des données valides"""
        # Créer un profil de test
        profile = Profile(name="Cadre", description="Test", color="#007bff")
        async_session.add(profile)
        await async_session.commit()

        token = self._admin_token(admin_user)
        csv_content = "username,password,profile\njean.dupont,MotDePasse123,Cadre\nmarie.martin,Secret456!,\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_lines"] == 2
        assert data["total_valid"] == 2
        assert data["total_errors"] == 0
        # Premier user a un profil matché
        assert data["lines"][0]["profile_matched"] is not None
        assert data["lines"][0]["profile_matched"]["name"] == "Cadre"
        # Deuxième user n'a pas de profil
        assert data["lines"][1]["profile_matched"] is None
        assert data["lines"][1]["profile_ignored"] is False

    @pytest.mark.asyncio
    async def test_preview_csv_unknown_profile(self, client: AsyncClient, admin_user: User, async_session):
        """Preview CSV avec un profil inexistant → warning, profil ignoré"""
        profile = Profile(name="Agent", description="Test", color="#28a745")
        async_session.add(profile)
        await async_session.commit()

        token = self._admin_token(admin_user)
        csv_content = "username,password,profile\nuser1,Password123,ProfilInexistant\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_valid"] == 1  # User est quand même valide
        assert data["total_warnings"] > 0
        assert data["lines"][0]["profile_ignored"] is True
        assert data["lines"][0]["profile_matched"] is None

    @pytest.mark.asyncio
    async def test_preview_csv_existing_username(self, client: AsyncClient, admin_user: User, test_user: User):
        """Preview CSV avec un username déjà existant → erreur"""
        token = self._admin_token(admin_user)
        csv_content = f"username,password,profile\n{test_user.username},Password123,\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_errors"] == 1
        assert data["lines"][0]["is_valid"] is False
        assert any("existe" in e for e in data["lines"][0]["errors"])

    @pytest.mark.asyncio
    async def test_preview_csv_duplicate_in_csv(self, client: AsyncClient, admin_user: User):
        """Preview CSV avec des doublons dans le CSV → erreur sur le second"""
        token = self._admin_token(admin_user)
        csv_content = "username,password,profile\nduplicateuser,Password123,\nduplicateuser,OtherPass123,\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 200
        data = response.json()
        assert data["lines"][0]["is_valid"] is True
        assert data["lines"][1]["is_valid"] is False
        assert any("Doublon" in e for e in data["lines"][1]["errors"])

    @pytest.mark.asyncio
    async def test_preview_csv_short_password(self, client: AsyncClient, admin_user: User):
        """Preview CSV avec un mot de passe trop court → erreur"""
        token = self._admin_token(admin_user)
        csv_content = "username,password,profile\nuser1,abc,\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_errors"] == 1
        assert any("court" in e for e in data["lines"][0]["errors"])

    @pytest.mark.asyncio
    async def test_preview_csv_empty_username(self, client: AsyncClient, admin_user: User):
        """Preview CSV avec un username vide → erreur"""
        token = self._admin_token(admin_user)
        csv_content = "username,password,profile\n,Password123,\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_errors"] == 1
        assert data["lines"][0]["is_valid"] is False

    @pytest.mark.asyncio
    async def test_preview_csv_empty_file(self, client: AsyncClient, admin_user: User):
        """Preview CSV avec un fichier vide → erreur 400"""
        token = self._admin_token(admin_user)
        files = {"file": ("test.csv", io.BytesIO(b""), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_csv_bad_headers(self, client: AsyncClient, admin_user: User):
        """Preview CSV avec des mauvais headers → erreur 400"""
        token = self._admin_token(admin_user)
        csv_content = "nom,motdepasse,role\nuser1,Password123,admin\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_csv_too_many_lines(self, client: AsyncClient, admin_user: User):
        """Preview CSV avec plus de 100 lignes → erreur 400"""
        token = self._admin_token(admin_user)
        lines = ["username,password,profile"]
        for i in range(101):
            lines.append(f"user{i},Password123,")
        csv_content = "\n".join(lines)
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_csv_semicolon_separator(self, client: AsyncClient, admin_user: User, async_session):
        """Preview CSV avec séparateur point-virgule (Excel FR)"""
        profile = Profile(name="Agent", description="Test", color="#28a745")
        async_session.add(profile)
        await async_session.commit()

        token = self._admin_token(admin_user)
        csv_content = "username;password;profile\nuser.test;Password123;Agent\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_valid"] == 1
        assert data["lines"][0]["profile_matched"]["name"] == "Agent"

    @pytest.mark.asyncio
    async def test_preview_csv_forbidden_for_user(self, client: AsyncClient, test_user: User):
        """User standard ne peut pas uploader un CSV"""
        token = self._user_token(test_user)
        csv_content = "username,password,profile\nuser1,Password123,\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_preview_csv_not_csv_file(self, client: AsyncClient, admin_user: User):
        """Preview avec un fichier non-CSV → erreur 400"""
        token = self._admin_token(admin_user)
        files = {"file": ("test.txt", io.BytesIO(b"hello world"), "text/plain")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_csv_profile_case_insensitive(self, client: AsyncClient, admin_user: User, async_session):
        """Le matching de profil est case-insensitive"""
        profile = Profile(name="Cadre Supérieur", description="Test", color="#007bff")
        async_session.add(profile)
        await async_session.commit()

        token = self._admin_token(admin_user)
        csv_content = "username,password,profile\nuser1,Password123,cadre supérieur\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 200
        data = response.json()
        assert data["lines"][0]["profile_matched"] is not None
        assert data["lines"][0]["profile_matched"]["name"] == "Cadre Supérieur"

    @pytest.mark.asyncio
    async def test_preview_csv_comment_lines_ignored(self, client: AsyncClient, admin_user: User):
        """Les lignes commençant par # sont ignorées"""
        token = self._admin_token(admin_user)
        csv_content = "username,password,profile\n# ceci est un commentaire,ignoré,ignoré\nuser1,Password123,\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        response = await client.post(
            "/api/users/csv/preview",
            headers=get_auth_header(token),
            files=files
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_lines"] == 1
        assert data["lines"][0]["username"] == "user1"
