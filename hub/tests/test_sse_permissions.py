# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests pour la notification SSE lors de la modification des permissions utilisateur.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from httpx import AsyncClient

from app.routes.events import notify_event, broadcast_event


class TestUserPermissionsSSE:
    """Tests pour l'émission SSE lors de la modification des permissions."""
    
    @pytest.mark.asyncio
    async def test_update_user_apps_emits_sse_event(
        self, client: AsyncClient, admin_user, test_user, test_app, admin_token
    ):
        """
        Vérifie que la modification des applications d'un utilisateur
        émet un événement SSE 'user_permissions_changed'.
        """
        # Mock de notify_event pour capturer l'appel
        with patch('app.routes.users.notify_event') as mock_notify:
            # Faire une requête pour modifier les apps de l'utilisateur
            response = await client.post(
                f"/api/users/{test_user.id}/apps",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "assignments": [
                        {"app_id": test_app.id, "role": "user"}
                    ]
                }
            )
            
            assert response.status_code == 200
            
            # Vérifier que notify_event a été appelé avec les bons paramètres
            mock_notify.assert_called()
            
            # Trouver l'appel avec 'user_permissions_changed'
            permission_calls = [
                call for call in mock_notify.call_args_list
                if call[0][0] == "user_permissions_changed"
            ]
            
            assert len(permission_calls) >= 1, "L'événement 'user_permissions_changed' devrait être émis"
            
            # Vérifier le contenu de l'événement
            event_data = permission_calls[0][0][1]
            assert event_data["user_id"] == test_user.id
            assert event_data["username"] == test_user.username
            assert "apps_count" in event_data
    
    @pytest.mark.asyncio
    async def test_update_user_apps_sse_contains_correct_apps_count(
        self, client: AsyncClient, admin_user, test_user, test_app, admin_token, async_session
    ):
        """
        Vérifie que l'événement SSE contient le bon nombre d'applications.
        """
        # Créer une deuxième application pour le test
        from app.models import Application
        app2 = Application(
            name="Test App 2",
            slug="test-app-2",
            api_url="http://test2:8000",
            is_active=True
        )
        async_session.add(app2)
        await async_session.commit()
        await async_session.refresh(app2)
        
        with patch('app.routes.users.notify_event') as mock_notify:
            # Assigner 2 applications
            response = await client.post(
                f"/api/users/{test_user.id}/apps",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "assignments": [
                        {"app_id": test_app.id, "role": "user"},
                        {"app_id": app2.id, "role": "viewer"}
                    ]
                }
            )
            
            assert response.status_code == 200
            
            # Vérifier le nombre d'apps dans l'événement
            permission_calls = [
                call for call in mock_notify.call_args_list
                if call[0][0] == "user_permissions_changed"
            ]
            
            assert len(permission_calls) >= 1
            event_data = permission_calls[0][0][1]
            assert event_data["apps_count"] == 2
    
    @pytest.mark.asyncio
    async def test_remove_all_user_apps_emits_sse_event(
        self, client: AsyncClient, admin_user, test_user, admin_token
    ):
        """
        Vérifie que la suppression de toutes les applications
        émet également un événement SSE avec apps_count=0.
        """
        with patch('app.routes.users.notify_event') as mock_notify:
            # Supprimer toutes les applications (assignments vide)
            response = await client.post(
                f"/api/users/{test_user.id}/apps",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"assignments": []}
            )
            
            assert response.status_code == 200
            
            # Vérifier l'événement
            permission_calls = [
                call for call in mock_notify.call_args_list
                if call[0][0] == "user_permissions_changed"
            ]
            
            assert len(permission_calls) >= 1
            event_data = permission_calls[0][0][1]
            assert event_data["apps_count"] == 0


class TestRefreshTokenPermissions:
    """Tests pour vérifier que le refresh token retourne les permissions mises à jour."""
    
    @pytest.mark.asyncio
    async def test_refresh_token_includes_updated_permissions(
        self, client: AsyncClient, test_user, test_app, async_session
    ):
        """
        Vérifie que le refresh token retourne un nouveau token
        avec les permissions mises à jour.
        """
        from app.models import UserAppRole
        from app.auth import create_refresh_token
        
        # Créer un refresh token pour l'utilisateur
        refresh_token = await create_refresh_token(
            async_session,
            user_id=test_user.id
        )
        
        # Ajouter une application à l'utilisateur directement en base
        user_app_role = UserAppRole(
            user_id=test_user.id,
            app_id=test_app.id,
            role="manager"
        )
        async_session.add(user_app_role)
        await async_session.commit()
        
        # Utiliser le refresh token pour obtenir un nouveau access token
        response = await client.post(
            "/api/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Décoder le nouveau token pour vérifier les apps
        import base64
        import json
        
        access_token = data["access_token"]
        payload_b64 = access_token.split('.')[1]
        # Ajouter padding si nécessaire
        payload_b64 += '=' * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        
        # Vérifier que l'app est dans le token avec le bon rôle
        assert "apps" in payload
        assert test_app.slug in payload["apps"]
        assert payload["apps"][test_app.slug] == "manager"


class TestSSEStreamAuth:
    """Le flux SSE /api/events/stream doit exiger une authentification."""

    @pytest.mark.asyncio
    async def test_event_stream_requires_auth(self, client: AsyncClient):
        """
        Sans token Bearer ni beacon_token+user_id, l'endpoint doit répondre 401
        proprement (régression : NameError 'status' non importé -> 500).
        """
        response = await client.get("/api/events/stream")
        assert response.status_code == 401
