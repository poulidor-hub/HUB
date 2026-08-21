"""
T1 (self-healing M2M) : le ré-enregistrement trusted d'une app existante
dont le service account a disparu (suppression en cascade) doit recréer un
SA actif, lié à l'app, et retourner des credentials complets.

Non-régression : une IP non-Docker reste refusée (403).
"""
import pytest
from pathlib import Path
from unittest.mock import patch
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Application,
    ServiceAccount,
    service_account_app_access,
    AuditLog,
)


class TestRegisterRecreatesMissingSA:
    @pytest.mark.asyncio
    async def test_register_existing_app_without_sa_recreates_sa(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        # App existante SANS service account (SA supprimé en cascade)
        orphan_app = Application(
            name="Orphan App",
            slug="orphan-app",
            api_url="http://192.0.2.10:9003",
            is_active=True,
            approval_status="approved",
        )
        async_session.add(orphan_app)
        await async_session.commit()

        # SECRETS_DIR → tmp_path : le test ne doit pas écrire dans /code/secrets
        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "orphan-app",
                    "name": "Orphan App",
                    "api_port": 9003,
                    "frontend_port": 4206,
                },
            )

        assert response.status_code == 200, f"{response.status_code}: {response.text}"
        data = response.json()
        assert data["is_new"] is False
        creds = data["credentials"]
        assert creds["client_id"], "client_id manquant dans la réponse"
        assert creds["client_secret"], "client_secret manquant dans la réponse (T1 non corrigé)"

        # Le SA est recréé, actif, lié à l'app
        result = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.client_id == creds["client_id"])
        )
        sa = result.scalar_one()
        assert sa.is_active is True
        links = await async_session.execute(
            select(service_account_app_access).where(
                service_account_app_access.c.service_account_id == sa.id
            )
        )
        app_ids = {row.app_id for row in links}
        assert orphan_app.id in app_ids

        # Recréation tracée dans l'audit
        audits = await async_session.execute(
            select(AuditLog).where(AuditLog.action == "m2m_sa_recreated")
        )
        assert audits.scalars().first() is not None

    @pytest.mark.asyncio
    async def test_register_from_non_docker_ip_still_rejected(
        self, client: AsyncClient
    ):
        """Non-régression sécurité : IP hors réseau Docker → 403."""
        with patch("app.routes.apps.is_docker_network_ip", return_value=False):
            response = await client.post(
                "/api/apps/register",
                json={"slug": "whatever", "api_port": 1234},
            )
        assert response.status_code == 403
