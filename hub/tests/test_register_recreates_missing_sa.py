# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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
    async def test_register_shared_app_without_owner_sa_does_not_touch_foreign_sa(
        self, client: AsyncClient, async_session: AsyncSession, tmp_path: Path
    ):
        """App partagée SANS SA propriétaire MAIS avec des SA étrangers liés
        (accès shared) : le register doit recréer un SA dédié et NE JAMAIS
        rotationner le secret d'un SA étranger (bug Philia Proxy 2026-08-22)."""
        from app.auth import get_password_hash

        shared_app = Application(
            name="Shared Orphan",
            slug="shared-orphan",
            api_url="http://192.0.2.10:9100",
            is_active=True,
            approval_status="approved",
            is_shared_service=True,
        )
        async_session.add(shared_app)

        foreign_sa = ServiceAccount(
            name="SA - Philia Proxy",
            client_id="sa_philia_foreign",
            client_secret_hash=get_password_hash("philia_old_secret"),
            is_active=True,
        )
        async_session.add(foreign_sa)
        await async_session.flush()

        # Lien shared : le SA étranger a accès à l'app partagée
        await async_session.execute(
            service_account_app_access.insert().values(
                service_account_id=foreign_sa.id,
                app_id=shared_app.id,
            )
        )
        await async_session.commit()
        foreign_hash_before = foreign_sa.client_secret_hash

        with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
             patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
            response = await client.post(
                "/api/apps/register",
                json={
                    "slug": "shared-orphan",
                    "name": "Shared Orphan",
                    "api_port": 9100,
                    "frontend_port": 4300,
                    "is_shared": True,
                },
            )

        assert response.status_code == 200, f"{response.status_code}: {response.text}"
        creds = response.json()["credentials"]
        assert creds["client_id"] != "sa_philia_foreign", "Le SA d'une autre app a été livré !"

        # Le SA étranger n'a PAS été rotationné
        await async_session.refresh(foreign_sa)
        assert foreign_sa.client_secret_hash == foreign_hash_before

        # Un SA dédié a été recréé et lié
        result = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.client_id == creds["client_id"])
        )
        sa = result.scalar_one()
        assert sa.name == "SA - Shared Orphan"

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
