"""
Tests ciblés pour les backups Vaultwarden complets stockés par le Hub.
"""
import hashlib
import io
import json
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token
from app.models import AppBackup, AuditLog, ServiceAccount
from tests.conftest import get_auth_header


def make_backup_zip(include_vaultwarden: bool = True, include_secrets: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "data.json",
            json.dumps(
                {
                    "metadata": {
                        "app_version": "1.2.3",
                        "format_version": "3.0",
                        "total_tables": 7,
                        "total_rows": 42,
                    },
                    "data": {
                        "items": [{"id": 1}],
                        "user_preferences": [{"id": 1}],
                        "uploaded_files": [{"path": "files/demo.txt"}],
                    },
                }
            ),
        )
        archive.writestr("files/demo.txt", "demo")
        if include_vaultwarden:
            archive.writestr(
                "vaultwarden/manifest.json",
                json.dumps({"files": [{"path": "db.sqlite3", "sha256": "abc"}]}),
            )
            archive.writestr("vaultwarden/db.sqlite3", "sqlite")
        if include_secrets:
            archive.writestr(
                "secrets/backup-secrets.json",
                json.dumps(
                    {
                        "encrypted_payload": "ciphertext",
                        "nonce": "nonce",
                        "key_id": "main",
                        "plaintext_sha256": "abc",
                        "secret_names": ["VAULTWARDEN_ADMIN_TOKEN"],
                    }
                ),
            )
    return buffer.getvalue()


def service_account_token(service_account: ServiceAccount) -> str:
    token, _, _ = create_access_token(
        username=service_account.client_id,
        apps={},
        is_service_account=True,
    )
    return token


@pytest.fixture
def backup_storage(tmp_path, monkeypatch):
    import app.routes.app_backups as app_backups_route
    import app.routes.backup as backup_route

    storage_path = tmp_path / "backups"
    monkeypatch.setattr(app_backups_route, "STORAGE_BASE_DIR", storage_path)
    monkeypatch.setattr(backup_route, "STORAGE_BASE_DIR", storage_path)
    return storage_path


async def create_stored_backup(
    async_session: AsyncSession,
    backup_storage,
    test_app,
    backup_id: str,
    include_vaultwarden: bool,
    include_secrets: bool,
) -> tuple[AppBackup, bytes]:
    content = make_backup_zip(include_vaultwarden=include_vaultwarden, include_secrets=include_secrets)
    app_dir = backup_storage / test_app.slug
    app_dir.mkdir(parents=True, exist_ok=True)
    filename = f"backup_{backup_id}.zip"
    (app_dir / filename).write_bytes(content)
    backup = AppBackup(
        backup_id=backup_id,
        app_id=test_app.id,
        app_slug=test_app.slug,
        app_version="1.2.3",
        format_version="3.0",
        total_items=1,
        total_preferences=1,
        total_files=1,
        total_tables=7,
        total_rows=42,
        total_size_bytes=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
        storage_path=f"{test_app.slug}/{filename}",
        includes_vaultwarden_snapshot=include_vaultwarden,
        includes_secret_bundle=include_secrets,
        restore_requires_offline=True,
        created_by="pytest",
        created_by_type="test",
    )
    async_session.add(backup)
    await async_session.commit()
    await async_session.refresh(backup)
    return backup, content


class TestVaultwardenBackupReception:
    @pytest.mark.asyncio
    async def test_receive_vaultwarden_backup_metadata_and_preserves_zip(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        service_account: ServiceAccount,
        test_app,
        backup_storage,
    ):
        content = make_backup_zip()
        checksum = hashlib.sha256(content).hexdigest()
        response = await client.post(
            f"/api/apps/{test_app.slug}/backup",
            headers=get_auth_header(service_account_token(service_account)),
            files={"file": ("backup.zip", content, "application/zip")},
            data={
                "backup_id": "vw-full-001",
                "app_version": "9.9.9",
                "format_version": "3.0",
                "total_tables": "7",
                "total_rows": "42",
                "total_files": "3",
                "checksum": checksum,
                "includes_vaultwarden_snapshot": "true",
                "includes_secret_bundle": "true",
                "restore_requires_offline": "true",
                "description": "Backup Vaultwarden complet",
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["includes_vaultwarden_snapshot"] is True
        assert data["includes_secret_bundle"] is True
        assert data["restore_requires_offline"] is True
        assert data["format_version"] == "3.0"
        assert data["total_tables"] == 7
        assert data["total_rows"] == 42

        result = await async_session.execute(
            select(AppBackup).where(AppBackup.backup_id == "vw-full-001")
        )
        backup = result.scalar_one()
        assert backup.includes_vaultwarden_snapshot is True
        assert backup.includes_secret_bundle is True

        stored_content = (backup_storage / backup.storage_path).read_bytes()
        assert stored_content == content
        assert hashlib.sha256(stored_content).hexdigest() == checksum

    @pytest.mark.asyncio
    async def test_old_backup_without_vaultwarden_flags_remains_compatible(
        self,
        client: AsyncClient,
        service_account: ServiceAccount,
        test_app,
        backup_storage,
    ):
        content = make_backup_zip(include_vaultwarden=False, include_secrets=False)
        response = await client.post(
            f"/api/apps/{test_app.slug}/backup",
            headers=get_auth_header(service_account_token(service_account)),
            files={"file": ("backup.zip", content, "application/zip")},
            data={
                "backup_id": "generic-001",
                "checksum": hashlib.sha256(content).hexdigest(),
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["includes_vaultwarden_snapshot"] is False
        assert data["includes_secret_bundle"] is False

    @pytest.mark.asyncio
    async def test_receive_vaultwarden_backup_infers_flags_from_zip(
        self,
        client: AsyncClient,
        service_account: ServiceAccount,
        test_app,
        backup_storage,
    ):
        content = make_backup_zip(include_vaultwarden=True, include_secrets=True)
        response = await client.post(
            f"/api/apps/{test_app.slug}/backup",
            headers=get_auth_header(service_account_token(service_account)),
            files={"file": ("backup.zip", content, "application/zip")},
            data={
                "backup_id": "vw-inferred-001",
                "checksum": hashlib.sha256(content).hexdigest(),
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["includes_vaultwarden_snapshot"] is True
        assert data["includes_secret_bundle"] is True
        assert data["restore_requires_offline"] is True
        assert data["format_version"] == "3.0"
        assert data["total_tables"] == 7
        assert data["total_rows"] == 42

    @pytest.mark.asyncio
    async def test_latest_backup_returns_vaultwarden_flags(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token: str,
        test_app,
        backup_storage,
    ):
        backup, _ = await create_stored_backup(
            async_session,
            backup_storage,
            test_app,
            backup_id="latest-vw-001",
            include_vaultwarden=True,
            include_secrets=True,
        )

        response = await client.get(
            f"/api/apps/{test_app.slug}/backup/latest",
            headers=get_auth_header(admin_token),
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["backup_id"] == backup.backup_id
        assert data["includes_vaultwarden_snapshot"] is True
        assert data["includes_secret_bundle"] is True
        assert data["checksum"] == backup.checksum


class TestVaultwardenBackupTrigger:
    @pytest.mark.asyncio
    async def test_triggered_backup_infers_vaultwarden_metadata_from_downloaded_zip(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        monkeypatch,
        admin_token: str,
        service_account: ServiceAccount,
        test_app,
        backup_storage,
    ):
        content = make_backup_zip(include_vaultwarden=True, include_secrets=True)
        captured = {}

        class FakeResponse:
            def __init__(self, status_code=200, json_data=None, content_data=b""):
                self.status_code = status_code
                self._json_data = json_data or {}
                self.content = content_data
                self.text = ""

            def json(self):
                return self._json_data

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, headers=None, **kwargs):
                captured["create_url"] = url
                captured["create_headers"] = headers
                return FakeResponse(
                    json_data={
                        "backup_id": "trigger-vw-001",
                        "metadata": {"app_version": "9.9.9"},
                    }
                )

            async def get(self, url, headers=None, **kwargs):
                captured["download_url"] = url
                captured["download_headers"] = headers
                return FakeResponse(content_data=content)

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

        # Le trigger lance une tâche de fond qui ouvre sa propre session via
        # async_session_maker. Pour que la tâche utilise la même DB de test,
        # on patche async_session_maker pour réutiliser la session de test.
        class _SessionCtx:
            async def __aenter__(self):
                return async_session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        def _fake_session_maker():
            return _SessionCtx()

        import app.database as _db_module
        monkeypatch.setattr(_db_module, "async_session_maker", _fake_session_maker)

        response = await client.post(
            f"/api/apps/{test_app.slug}/backup/trigger",
            headers=get_auth_header(admin_token),
        )

        # Le trigger est asynchrone : il renvoie 202 et crée la ligne en pending,
        # puis lance une tâche de fond qui finalise le backup.
        assert response.status_code == 202, response.text
        data = response.json()
        assert data["status"] == "pending"
        assert data["backup_id"]
        initial_backup_id = data["backup_id"]

        # Attendre la fin de la tâche de fond (elle remplace backup_id par celui de l'app)
        import asyncio as _asyncio
        for _ in range(50):
            await _asyncio.sleep(0.05)
            existing = await async_session.execute(
                select(AppBackup).where(AppBackup.backup_id == "trigger-vw-001")
            )
            if existing.scalar_one_or_none() is not None:
                break

        assert captured["create_url"] == f"{test_app.api_url}/api/admin/backup"
        assert captured["download_url"] == f"{test_app.api_url}/api/admin/backup/download?backup_id=trigger-vw-001"

        result = await async_session.execute(
            select(AppBackup).where(AppBackup.backup_id == "trigger-vw-001")
        )
        backup = result.scalar_one()
        assert backup.created_by_type == "hub"
        assert backup.status == "success"
        assert backup.duration_seconds is not None
        assert backup.app_version == "9.9.9"
        assert backup.format_version == "3.0"
        assert backup.total_tables == 7
        assert backup.total_rows == 42
        assert backup.includes_vaultwarden_snapshot is True
        assert backup.includes_secret_bundle is True
        assert backup.restore_requires_offline is True
        assert (backup_storage / backup.storage_path).read_bytes() == content

        list_response = await client.get(
            f"/api/apps/{test_app.slug}/backup",
            headers=get_auth_header(admin_token),
        )
        assert list_response.status_code == 200, list_response.text
        backups_listed = list_response.json()["backups"]
        # Filtrer le backup d'intérêt (la liste peut contenir aussi la ligne initiale
        # si jamais initial_backup_id != "trigger-vw-001")
        listed_backup = next(b for b in backups_listed if b["backup_id"] == "trigger-vw-001")
        assert listed_backup["includes_vaultwarden_snapshot"] is True
        assert listed_backup["includes_secret_bundle"] is True


class TestVaultwardenRestoreValidation:
    @pytest.mark.asyncio
    async def test_restore_vaultwarden_requires_confirmation(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token: str,
        test_app,
        backup_storage,
    ):
        backup, _ = await create_stored_backup(
            async_session,
            backup_storage,
            test_app,
            backup_id="needs-confirm",
            include_vaultwarden=True,
            include_secrets=True,
        )

        response = await client.post(
            f"/api/apps/{test_app.slug}/backup/{backup.backup_id}/restore",
            headers=get_auth_header(admin_token),
            json={"restore_vaultwarden": True},
        )

        assert response.status_code == 400
        assert "confirm_full_restore" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_restore_vaultwarden_rejects_backup_without_snapshot(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token: str,
        test_app,
        backup_storage,
    ):
        backup, _ = await create_stored_backup(
            async_session,
            backup_storage,
            test_app,
            backup_id="generic-no-snapshot",
            include_vaultwarden=False,
            include_secrets=False,
        )

        response = await client.post(
            f"/api/apps/{test_app.slug}/backup/{backup.backup_id}/restore",
            headers=get_auth_header(admin_token),
            json={
                "restore_vaultwarden": True,
                "confirm_full_restore": True,
            },
        )

        assert response.status_code == 400
        assert "snapshot Vaultwarden" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_restore_rejects_missing_secret_bundle_flag(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        admin_token: str,
        test_app,
        backup_storage,
    ):
        backup, _ = await create_stored_backup(
            async_session,
            backup_storage,
            test_app,
            backup_id="snapshot-no-secrets",
            include_vaultwarden=True,
            include_secrets=False,
        )

        response = await client.post(
            f"/api/apps/{test_app.slug}/backup/{backup.backup_id}/restore",
            headers=get_auth_header(admin_token),
            json={
                "restore_secret_bundle": True,
            },
        )

        assert response.status_code == 400
        assert "bundle de secrets" in response.json()["detail"]


class TestVaultwardenRestoreRelay:
    @pytest.mark.asyncio
    async def test_restore_vaultwarden_flags_relayed_and_audited(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        monkeypatch,
        admin_token: str,
        service_account: ServiceAccount,
        test_app,
        backup_storage,
    ):
        backup, content = await create_stored_backup(
            async_session,
            backup_storage,
            test_app,
            backup_id="relay-vw-001",
            include_vaultwarden=True,
            include_secrets=True,
        )
        captured = {}

        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "success": True,
                    "message": "ok",
                    "items_restored": 1,
                    "vaultwarden_snapshot_restored": True,
                    "vaultwarden_restore": {"files_restored": 2, "bytes_restored": 10},
                    "secret_bundle_restored": True,
                    "secret_bundle_secret_names": ["VAULTWARDEN_ADMIN_TOKEN"],
                    "accounts_reencrypted": 3,
                    "requires_vaultwarden_restart": True,
                    "warnings": [],
                    "errors": [],
                }

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, headers=None, files=None, data=None):
                captured["url"] = url
                captured["headers"] = headers
                captured["files"] = files
                captured["data"] = data
                return FakeResponse()

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

        response = await client.post(
            f"/api/apps/{test_app.slug}/backup/{backup.backup_id}/restore",
            headers=get_auth_header(admin_token),
            json={
                "clear_existing": True,
                "restore_vaultwarden": True,
                "restore_secret_bundle": True,
                "require_vaultwarden_offline": True,
                "confirm_full_restore": True,
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["success"] is True
        assert data["vaultwarden_snapshot_restored"] is True
        assert data["secret_bundle_restored"] is True
        assert data["accounts_reencrypted"] == 3
        assert data["requires_vaultwarden_restart"] is True
        assert captured["url"] == f"{test_app.api_url}/api/admin/restore"
        assert captured["data"]["restore_vaultwarden"] == "true"
        assert captured["data"]["restore_secret_bundle"] == "true"
        assert captured["data"]["require_vaultwarden_offline"] == "true"
        assert captured["data"]["confirm_full_restore"] == "true"
        assert captured["files"]["file"][1] == content

        audit_result = await async_session.execute(
            select(AuditLog).where(AuditLog.action == "restore_vaultwarden_complete")
        )
        audit = audit_result.scalar_one()
        assert audit.target == test_app.slug
        assert "backup-secrets.json" not in (audit.details or "")
        assert "ciphertext" not in (audit.details or "")
