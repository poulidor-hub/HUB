"""
Tests pour le suivi de progression et l'exécution asynchrone des backups Hub.

Couvre :
- POST /api/apps/{slug}/backup/trigger renvoie 202 et crée la ligne en pending.
- 409 Conflict en cas de double déclenchement.
- Le job asynchrone passe à success + duration_seconds quand tout va bien.
- Les erreurs détaillées sont remontées dans error_message en cas d'échec app.
- Le retry post-timeout convertit un timeout du download en succès si l'app
  livre finalement le ZIP.
- GET /api/admin/backups/in-progress liste les jobs actifs.
- cleanup_stale_running_backups requalifie les backups bloqués au démarrage.
"""
import asyncio
import io
import json
import zipfile
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppBackup, Application, ServiceAccount, utcnow
from tests.conftest import get_auth_header


def _make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "data.json",
            json.dumps({"metadata": {"app_version": "1.0", "format_version": "3.0"}, "data": {}}),
        )
    return buf.getvalue()


@pytest.fixture
def backup_storage(tmp_path, monkeypatch):
    import app.routes.app_backups as app_backups_route
    import app.routes.backup as backup_route

    storage = tmp_path / "backups"
    monkeypatch.setattr(app_backups_route, "STORAGE_BASE_DIR", storage)
    monkeypatch.setattr(backup_route, "STORAGE_BASE_DIR", storage)
    return storage


@pytest.fixture
async def configured_app(async_session: AsyncSession, test_app, service_account):
    """test_app + service_account déjà associés, mode manual + api_url valide."""
    test_app.api_url = "http://fake-app:8000"
    test_app.backup_mode = "manual"
    await async_session.commit()
    await async_session.refresh(test_app)
    return test_app


def _mock_async_client(create_response=None, download_response=None,
                       create_exc=None, download_exc=None):
    """Construit un mock d'httpx.AsyncClient utilisé en context manager.

    Le 1er appel POST est `create`, le 1er GET est `download`.
    """
    client = MagicMock()

    async def _post(*args, **kwargs):
        if create_exc is not None:
            raise create_exc
        return create_response

    async def _get(*args, **kwargs):
        if download_exc is not None:
            raise download_exc
        return download_response

    client.post = AsyncMock(side_effect=_post)
    client.get = AsyncMock(side_effect=_get)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _http_response(status_code=200, body=b"", json_payload=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = body
    if json_payload is not None:
        resp.json = MagicMock(return_value=json_payload)
        resp.text = json.dumps(json_payload)
    else:
        resp.text = body.decode("utf-8", errors="ignore") if isinstance(body, bytes) else str(body)
    return resp


class TestTriggerAsync:
    @pytest.mark.asyncio
    async def test_trigger_returns_202_and_creates_pending_row(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token,
        configured_app,
        backup_storage,
    ):
        # Empêcher la tâche de fond d'aboutir : on patch perform_app_backup
        # pour qu'elle soit un no-op (le test vérifie juste le 202 + pending).
        with patch(
            "app.routes.app_backups.perform_app_backup",
            new=AsyncMock(return_value=None),
        ):
            response = await client.post(
                f"/api/apps/{configured_app.slug}/backup/trigger",
                headers=get_auth_header(admin_token),
            )

        assert response.status_code == 202, response.text
        data = response.json()
        assert data["success"] is True
        assert data["status"] == "pending"
        assert data["backup_id"]

        # Laisser tourner brièvement la tâche de fond (qui a été patchée -> noop)
        await asyncio.sleep(0.05)

        result = await async_session.execute(
            select(AppBackup).where(AppBackup.backup_id == data["backup_id"])
        )
        backup = result.scalar_one()
        assert backup.status == "pending"
        assert backup.progress_step == "queued"
        assert backup.app_slug == configured_app.slug

    @pytest.mark.asyncio
    async def test_double_trigger_returns_409(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token,
        configured_app,
        backup_storage,
    ):
        # Créer manuellement un backup en pending pour simuler un job en cours
        in_progress = AppBackup(
            backup_id="inflight-001",
            app_id=configured_app.id,
            app_slug=configured_app.slug,
            status="running",
            started_at=utcnow(),
            progress_step="downloading",
            progress_percent=30,
            created_by="admin",
            created_by_type="hub",
        )
        async_session.add(in_progress)
        await async_session.commit()

        response = await client.post(
            f"/api/apps/{configured_app.slug}/backup/trigger",
            headers=get_auth_header(admin_token),
        )

        assert response.status_code == 409
        assert "déjà en cours" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_trigger_disabled_returns_400(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token,
        configured_app,
        backup_storage,
    ):
        configured_app.backup_mode = "disabled"
        await async_session.commit()

        response = await client.post(
            f"/api/apps/{configured_app.slug}/backup/trigger",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 400


class TestPerformAppBackup:
    @pytest.mark.asyncio
    async def test_hub_system_app_backup_creates_local_export(
        self,
        async_session: AsyncSession,
        backup_storage,
    ):
        from app.routes.app_backups import ensure_hub_system_application, perform_app_backup

        hub_app = await ensure_hub_system_application(async_session)

        backup = await perform_app_backup(
            async_session,
            hub_app,
            created_by="admin",
            created_by_type="hub",
        )

        assert backup.status == "success"
        assert backup.app_slug == "hub"
        assert backup.app_version == "hub"
        assert backup.storage_path.startswith("hub/backup_")
        archive_path = backup_storage / backup.storage_path
        assert archive_path.exists()
        with zipfile.ZipFile(archive_path) as archive:
            assert "environment.json" in archive.namelist()

    @pytest.mark.asyncio
    async def test_success_path_sets_duration(
        self,
        async_session: AsyncSession,
        configured_app,
        backup_storage,
    ):
        from app.routes.app_backups import perform_app_backup

        zip_bytes = _make_zip()
        create_resp = _http_response(200, json_payload={"backup_id": "remote-1"})
        download_resp = _http_response(200, body=zip_bytes)
        cm = _mock_async_client(create_response=create_resp, download_response=download_resp)

        with patch("httpx.AsyncClient", return_value=cm):
            backup = await perform_app_backup(
                async_session,
                configured_app,
                created_by="admin",
                created_by_type="hub",
            )

        assert backup.status == "success"
        assert backup.completed_at is not None
        assert backup.duration_seconds is not None and backup.duration_seconds >= 0
        assert backup.progress_percent == 100
        assert backup.progress_step == "done"
        assert backup.error_message is None
        assert backup.storage_path

    @pytest.mark.asyncio
    async def test_app_returns_500_records_detailed_error(
        self,
        async_session: AsyncSession,
        configured_app,
        backup_storage,
    ):
        from app.routes.app_backups import perform_app_backup, BackupExecutionError

        create_resp = _http_response(500, body=b"InternalServerError details ici")
        cm = _mock_async_client(create_response=create_resp)

        with patch("httpx.AsyncClient", return_value=cm):
            with pytest.raises(BackupExecutionError) as exc:
                await perform_app_backup(
                    async_session,
                    configured_app,
                    created_by="admin",
                    created_by_type="hub",
                )

        assert "HTTP 500" in exc.value.message
        assert "InternalServerError details ici" in exc.value.message

        # La ligne AppBackup doit refléter l'échec
        result = await async_session.execute(
            select(AppBackup).where(AppBackup.app_slug == configured_app.slug)
        )
        backup = result.scalars().first()
        assert backup is not None
        assert backup.status == "failed"
        assert backup.error_message is not None
        assert "HTTP 500" in backup.error_message

    @pytest.mark.asyncio
    async def test_download_timeout_then_retry_succeeds(
        self,
        async_session: AsyncSession,
        configured_app,
        backup_storage,
    ):
        """1er download timeout → retry étendu réussit → status=success."""
        from app.routes.app_backups import perform_app_backup

        zip_bytes = _make_zip()
        create_resp = _http_response(200, json_payload={"backup_id": "remote-2"})
        download_resp_ok = _http_response(200, body=zip_bytes)

        # Le 1er client (timeout=120) timeout au download.
        cm_first = _mock_async_client(
            create_response=create_resp,
            download_exc=httpx.TimeoutException("read timeout"),
        )
        # Le 2nd client (timeout=300, retry) renvoie le ZIP.
        cm_retry = _mock_async_client(download_response=download_resp_ok)

        clients_iter = iter([cm_first, cm_retry])

        def _client_factory(*args, **kwargs):
            return next(clients_iter)

        with patch("httpx.AsyncClient", side_effect=_client_factory):
            backup = await perform_app_backup(
                async_session,
                configured_app,
                created_by="admin",
                created_by_type="hub",
            )

        assert backup.status == "success"
        assert backup.duration_seconds is not None

    @pytest.mark.asyncio
    async def test_download_double_timeout_results_in_status_timeout(
        self,
        async_session: AsyncSession,
        configured_app,
        backup_storage,
    ):
        from app.routes.app_backups import perform_app_backup, BackupExecutionError

        create_resp = _http_response(200, json_payload={"backup_id": "remote-3"})
        cm_first = _mock_async_client(
            create_response=create_resp,
            download_exc=httpx.TimeoutException("read timeout 1"),
        )
        cm_retry = _mock_async_client(
            download_exc=httpx.TimeoutException("read timeout 2"),
        )

        clients_iter = iter([cm_first, cm_retry])

        def _client_factory(*args, **kwargs):
            return next(clients_iter)

        with patch("httpx.AsyncClient", side_effect=_client_factory):
            with pytest.raises(BackupExecutionError):
                await perform_app_backup(
                    async_session,
                    configured_app,
                    created_by="admin",
                    created_by_type="hub",
                )

        result = await async_session.execute(
            select(AppBackup).where(AppBackup.app_slug == configured_app.slug)
        )
        backup = result.scalars().first()
        assert backup is not None
        assert backup.status == "timeout"
        assert "Timeout" in (backup.error_message or "")


class TestInProgressEndpoint:
    @pytest.mark.asyncio
    async def test_lists_only_pending_running(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token,
        configured_app,
    ):
        # Crée 1 backup pending, 1 running, 1 success
        rows = [
            AppBackup(
                backup_id="pending-1",
                app_id=configured_app.id,
                app_slug=configured_app.slug,
                status="pending",
                started_at=utcnow(),
                progress_step="queued",
                progress_percent=0,
                created_by="a",
                created_by_type="hub",
            ),
            AppBackup(
                backup_id="running-1",
                app_id=configured_app.id,
                app_slug=configured_app.slug,
                status="running",
                started_at=utcnow(),
                progress_step="downloading",
                progress_percent=30,
                created_by="a",
                created_by_type="hub",
            ),
            AppBackup(
                backup_id="success-1",
                app_id=configured_app.id,
                app_slug=configured_app.slug,
                status="success",
                started_at=utcnow() - timedelta(seconds=10),
                completed_at=utcnow(),
                duration_seconds=10,
                progress_step="done",
                progress_percent=100,
                storage_path="x/y.zip",
                created_by="a",
                created_by_type="hub",
            ),
        ]
        for r in rows:
            async_session.add(r)
        await async_session.commit()

        response = await client.get(
            "/api/admin/backups/in-progress",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        ids = {item["backup_id"] for item in data["items"]}
        assert ids == {"pending-1", "running-1"}


class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_cleanup_marks_old_running_as_timeout(
        self,
        async_session: AsyncSession,
        configured_app,
        monkeypatch,
    ):
        from app.routes import app_backups as ab_module
        from app.database import async_session_maker

        # Monkey-patcher async_session_maker pour réutiliser la session de test
        class _FakeSessionCtx:
            async def __aenter__(self):
                return async_session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        def _fake_maker():
            return _FakeSessionCtx()

        monkeypatch.setattr(ab_module, "async_session_maker", _fake_maker, raising=False)

        # Patch via l'import local dans la fonction
        import app.database as db_module
        monkeypatch.setattr(db_module, "async_session_maker", _fake_maker)

        # Vieille ligne running (1h) -> doit être requalifiée
        old_running = AppBackup(
            backup_id="old-running",
            app_id=configured_app.id,
            app_slug=configured_app.slug,
            status="running",
            started_at=utcnow() - timedelta(hours=1),
            progress_step="downloading",
            progress_percent=50,
            created_by="a",
            created_by_type="hub",
        )
        # Pending récent -> doit aussi être requalifié au démarrage
        recent_pending = AppBackup(
            backup_id="recent-pending",
            app_id=configured_app.id,
            app_slug=configured_app.slug,
            status="pending",
            started_at=utcnow(),
            progress_step="queued",
            progress_percent=0,
            created_by="a",
            created_by_type="hub",
        )
        async_session.add_all([old_running, recent_pending])
        await async_session.commit()

        count = await ab_module.cleanup_stale_running_backups()
        assert count == 2

        await async_session.refresh(old_running)
        await async_session.refresh(recent_pending)
        assert old_running.status == "timeout"
        assert recent_pending.status == "timeout"
        assert old_running.error_message
