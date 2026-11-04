# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Exclusion des ateliers (`{slug}-ws`) du système de sauvegardes du Hub.

La convention `{slug}-ws` est réservée aux ateliers (hubctl + workshop_service) :
ces clones de travail sont éphémères et ne doivent jamais être sauvegardés, ni
apparaître dans les vues admin des backups (cf. page Admin → Sauvegardes).

Couvre :
- GET /api/admin/backups : les ateliers n'apparaissent plus (listing + totaux).
- GET /api/admin/backups/stats : les backups d'ateliers ne comptent plus.
- POST /api/apps/{slug}/backup/trigger : refusé pour un atelier.
- POST /api/apps/{slug}/backup (push M2M) : refusé pour un atelier.
- perform_app_backup : garde au choke-point (BackupExecutionError).
- run_app_backups (snapshots) : les ateliers ne sont plus collectés.
"""
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token
from app.models import AppBackup, Application, ServiceAccount, utcnow
from tests.conftest import get_auth_header, get_password_hash


@pytest.fixture
def backup_storage(tmp_path, monkeypatch):
    """STORAGE_BASE_DIR redirigé vers tmp_path (aucune écriture réelle)."""
    import app.routes.app_backups as app_backups_route
    import app.hub_self_backup as hub_backup_module

    storage = tmp_path / "backups"
    monkeypatch.setattr(app_backups_route, "STORAGE_BASE_DIR", storage)
    monkeypatch.setattr(hub_backup_module, "STORAGE_BASE_DIR", storage)
    return storage


async def make_workshop_app(async_session: AsyncSession, base: Application) -> Application:
    """Crée l'atelier {base.slug}-ws, comme le fait le workshop_service."""
    ws = Application(
        name=f"{base.name} Atelier",
        slug=f"{base.slug}-ws",
        description="Atelier de test",
        icon="bi-gear",
        color="#6c757d",
        api_url="http://localhost:8010",
        frontend_url="http://localhost:3010",
        callback_url="http://localhost:3010/callback",
        is_active=True,
        requires_auth=True,
        is_workshop=True,
        backup_mode="manual",
    )
    async_session.add(ws)
    await async_session.commit()
    await async_session.refresh(ws)
    return ws


async def make_sa_for_app(async_session: AsyncSession, app: Application) -> ServiceAccount:
    """Service account lié à `app` (pattern conftest.service_account)."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models import service_account_app_access

    sa = ServiceAccount(
        name=f"SA {app.slug}",
        client_id=f"sa_{app.slug.replace('-', '')}",
        client_secret_hash=get_password_hash("secret123"),
        description="SA de test",
        is_active=True,
    )
    async_session.add(sa)
    await async_session.commit()
    await async_session.refresh(sa)
    await async_session.execute(
        service_account_app_access.insert().values(
            service_account_id=sa.id,
            app_id=app.id,
        )
    )
    await async_session.commit()
    result = await async_session.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.id == sa.id)
    )
    return result.scalar_one()


def _success_backup(app: Application, backup_id: str, size: int) -> AppBackup:
    return AppBackup(
        backup_id=backup_id,
        app_id=app.id,
        app_slug=app.slug,
        status="success",
        started_at=utcnow(),
        completed_at=utcnow(),
        progress_step="done",
        progress_percent=100,
        total_size_bytes=size,
        created_by="admin",
        created_by_type="hub",
    )


class TestAdminOverview:
    @pytest.mark.asyncio
    async def test_overview_exclut_les_ateliers(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token,
        test_app,
        backup_storage,
    ):
        from app.routes.app_backups import ensure_hub_system_application

        ws = await make_workshop_app(async_session, test_app)
        async_session.add(_success_backup(test_app, "bk-prod-1", 100))
        async_session.add(_success_backup(ws, "bk-ws-1", 999))
        await async_session.commit()
        # L'overview crée l'app système « hub » si absente — acceptable,
        # on vérifie l'exclusion des ateliers, pas la liste exhaustive.
        await ensure_hub_system_application(async_session)

        response = await client.get(
            "/api/admin/backups",
            headers=get_auth_header(admin_token),
        )

        assert response.status_code == 200, response.text
        data = response.json()
        slugs = [a["app_slug"] for a in data["apps"]]
        assert "test-app-ws" not in slugs
        assert "test-app" in slugs
        assert data["total_backups"] == 1
        assert data["total_size_bytes"] == 100


class TestAdminStats:
    @pytest.mark.asyncio
    async def test_stats_excluent_les_ateliers(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token,
        test_app,
        backup_storage,
    ):
        ws = await make_workshop_app(async_session, test_app)
        async_session.add(_success_backup(test_app, "bk-prod-2", 100))
        async_session.add(_success_backup(ws, "bk-ws-2", 999))
        await async_session.commit()

        response = await client.get(
            "/api/admin/backups/stats",
            headers=get_auth_header(admin_token),
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total_backups"] == 1
        assert data["total_size_bytes"] == 100
        assert data["apps_with_backups"] == 1


class TestTriggerBackup:
    @pytest.mark.asyncio
    async def test_trigger_refuse_un_atelier(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token,
        test_app,
        backup_storage,
    ):
        ws = await make_workshop_app(async_session, test_app)

        response = await client.post(
            f"/api/apps/{ws.slug}/backup/trigger",
            headers=get_auth_header(admin_token),
        )

        assert response.status_code == 400, response.text
        assert "atelier" in response.json()["detail"].lower()


class TestReceiveBackup:
    @pytest.mark.asyncio
    async def test_push_m2m_refuse_pour_un_atelier(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        test_app,
        backup_storage,
    ):
        ws = await make_workshop_app(async_session, test_app)
        sa = await make_sa_for_app(async_session, ws)

        token, _, _ = create_access_token(
            username=sa.client_id,
            apps={},
            is_service_account=True,
        )
        response = await client.post(
            f"/api/apps/{ws.slug}/backup",
            headers=get_auth_header(token),
            files={"file": ("backup.zip", b"not-a-real-zip", "application/zip")},
            data={"backup_id": "bk-ws-push"},
        )

        assert response.status_code == 400, response.text
        assert "atelier" in response.json()["detail"].lower()
        # Aucune ligne de backup ne doit être créée
        count = len((await async_session.execute(
            select_from_appbackup_ws()
        )).scalars().all())
        assert count == 0


def select_from_appbackup_ws():
    from sqlalchemy import select
    from app.models import AppBackup

    return select(AppBackup).where(AppBackup.app_slug == "test-app-ws")


class TestPerformAppBackup:
    @pytest.mark.asyncio
    async def test_perform_app_backup_refuse_un_atelier(
        self,
        async_session: AsyncSession,
        test_app,
        backup_storage,
    ):
        from app.routes.app_backups import BackupExecutionError, perform_app_backup

        ws = await make_workshop_app(async_session, test_app)
        # SA lié à l'atelier : sans la garde, le backup réussirait via le mock
        # HTTP ci-dessous et le test échouerait pour la bonne raison.
        await make_sa_for_app(async_session, ws)

        # Client HTTP mocké « qui répond » : sans la garde, le backup réussirait
        # et le test échouerait pour la bonne raison (aucune erreur levée).
        zip_bytes = b"PK\x03\x04fake"
        create_resp = MagicMock(status_code=200)
        create_resp.json = MagicMock(return_value={"backup_id": "remote-ws"})
        download_resp = MagicMock(status_code=200)
        download_resp.content = zip_bytes
        download_resp.text = ""
        client = MagicMock()
        client.post = _async_return(create_resp)
        client.get = _async_return(download_resp)
        cm = MagicMock()
        cm.__aenter__ = _async_return(client)
        cm.__aexit__ = _async_return(False)

        with patch("httpx.AsyncClient", return_value=cm):
            with pytest.raises(BackupExecutionError) as exc_info:
                await perform_app_backup(
                    async_session,
                    ws,
                    created_by="admin",
                    created_by_type="hub",
                )

        assert "atelier" in str(exc_info.value).lower()


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner


class TestSnapshotCollectors:
    @pytest.mark.asyncio
    async def test_run_app_backups_ignore_les_ateliers(
        self,
        async_session: AsyncSession,
        service_account: ServiceAccount,
        monkeypatch,
        tmp_path,
        test_app,
        backup_storage,
    ):
        import app.snapshot_collectors as snapshot_collectors
        from app.snapshot_collectors import run_app_backups

        ws = await make_workshop_app(async_session, test_app)
        # L'atelier doit être éligible en apparence (actif, backup non désactivé)
        # pour prouver que c'est bien le filtre -ws qui l'exclut. Sans filtre,
        # il échouerait (aucun SA) → failed=1 : le test est rouge pour la
        # bonne raison avant le fix.
        assert ws.is_active is True
        assert ws.backup_mode != "disabled"

        # Storage pointé vers tmp pour les deux modules concernés
        monkeypatch.setattr(snapshot_collectors, "STORAGE_BASE_DIR", backup_storage)

        # Mock HTTP : l'app prod répond normalement au create/download
        zip_bytes = b"PK\x03\x04fake"
        create_resp = MagicMock(status_code=200)
        create_resp.json = MagicMock(return_value={"backup_id": "remote-snap"})
        download_resp = MagicMock(status_code=200)
        download_resp.content = zip_bytes
        download_resp.text = ""
        client = MagicMock()
        client.post = _async_return(create_resp)
        client.get = _async_return(download_resp)
        cm = MagicMock()
        cm.__aenter__ = _async_return(client)
        cm.__aexit__ = _async_return(False)

        snapshot = MagicMock()
        snapshot.snapshot_id = "snap-test-1"

        with patch("httpx.AsyncClient", return_value=cm):
            result = await run_app_backups(async_session, tmp_path, snapshot)

        assert result["failed"] == 0, result
        assert result["succeeded"] == 1, result
        assert [e["slug"] for e in result["app_entries"]] == [test_app.slug]
