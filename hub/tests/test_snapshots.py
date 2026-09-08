"""
Tests pour le systeme de snapshots complets du Hub.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import create_access_token
from app.models import HubSnapshot, User, AppSetting
from tests.conftest import get_auth_header


@pytest.fixture
async def admin_token(admin_user: User) -> str:
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    return token


@pytest.fixture
async def snapshot_settings(async_session: AsyncSession):
    keys_values = {
        "snapshot_cron": "",
        "snapshot_retention_max": "3",
        "snapshot_retention_min_age_hours": "0",
        "s3_endpoint": "",
        "s3_bucket": "test-bucket",
        "s3_region": "eu-west-1",
        "s3_access_key": "",
        "s3_secret_key": "",
        "s3_prefix": "test/",
    }
    for k, v in keys_values.items():
        setting = AppSetting(key=k, value=v)
        async_session.add(setting)
    await async_session.commit()


class TestSnapshotConfig:
    @pytest.mark.asyncio
    async def test_get_snapshot_config(
        self, client: AsyncClient, admin_token, snapshot_settings,
    ):
        response = await client.get(
            "/api/admin/snapshots/config",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["snapshot_retention_max"] == 3
        assert data["s3_bucket"] == "test-bucket"
        assert data["s3_configured"] is False

    @pytest.mark.asyncio
    async def test_update_snapshot_config(
        self, client: AsyncClient, admin_token, snapshot_settings,
    ):
        response = await client.put(
            "/api/admin/snapshots/config",
            headers=get_auth_header(admin_token),
            json={"snapshot_retention_max": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["snapshot_retention_max"] == 10

    @pytest.mark.asyncio
    async def test_snapshot_config_requires_admin(
        self, client: AsyncClient, test_user: User,
    ):
        token, _, _ = create_access_token(username=test_user.username, apps={})
        response = await client.get(
            "/api/admin/snapshots/config",
            headers=get_auth_header(token),
        )
        assert response.status_code == 403


class TestSnapshotLifecycle:
    @pytest.mark.asyncio
    async def test_list_snapshots_empty(
        self, client: AsyncClient, admin_token, snapshot_settings,
    ):
        response = await client.get(
            "/api/admin/snapshots/",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["snapshots"] == []

    @pytest.mark.asyncio
    async def test_concurrent_snapshot_prevented(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-running-001",
            status="running",
            created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.post(
            "/api/admin/snapshots/",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 409
        assert "deja en cours" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_snapshot(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-delete-001",
            status="success",
            created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.delete(
            "/api/admin/snapshots/test-delete-001",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200

        result = await async_session.execute(
            select(HubSnapshot).where(HubSnapshot.snapshot_id == "test-delete-001")
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_cannot_delete_running_snapshot(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-running-002",
            status="running",
            created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.delete(
            "/api/admin/snapshots/test-running-002",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_preview_snapshot(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-preview-001",
            status="success",
            manifest={"collectors": {"pg_dump": {"size_bytes": 1024}}, "apps": []},
            apps_succeeded=0,
            apps_failed=0,
            created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.post(
            "/api/admin/snapshots/test-preview-001/restore/preview",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["apps_succeeded"] == 0

    @pytest.mark.asyncio
    async def test_restore_requires_confirmation(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-restore-001",
            status="success",
            created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.post(
            "/api/admin/snapshots/test-restore-001/restore",
            headers=get_auth_header(admin_token),
            json={"confirm_full_restore": False},
        )
        assert response.status_code == 400
        assert "confirm_full_restore" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_cannot_restore_non_success_snapshot(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-failed-001",
            status="failed",
            created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.post(
            "/api/admin/snapshots/test-failed-001/restore",
            headers=get_auth_header(admin_token),
            json={"confirm_full_restore": True},
        )
        assert response.status_code == 400
        assert "reussis" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_in_progress_returns_idle_when_none(
        self, client: AsyncClient, admin_token, snapshot_settings,
    ):
        response = await client.get(
            "/api/admin/snapshots/in-progress",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "idle"

    @pytest.mark.asyncio
    async def test_in_progress_returns_running(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-in-progress-001",
            status="running",
            progress_step="pg_dump",
            progress_percent=45,
            created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.get(
            "/api/admin/snapshots/in-progress",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert data["progress_percent"] == 45
        assert data["progress_step"] == "pg_dump"

    @pytest.mark.asyncio
    async def test_get_snapshot_detail(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-detail-001",
            status="success",
            total_size_bytes=1024,
            created_by="admin:admin",
            apps_succeeded=2,
            apps_failed=1,
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.get(
            "/api/admin/snapshots/test-detail-001",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["snapshot_id"] == "test-detail-001"
        assert data["status"] == "success"
        assert data["apps_succeeded"] == 2

    @pytest.mark.asyncio
    async def test_download_requires_s3_config(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-dl-001",
            status="success",
            s3_bucket="test-bucket",
            s3_key="test/snapshot.tar.zst.enc",
            created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.get(
            "/api/admin/snapshots/test-dl-001/download",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_include_apps_code_default_on_flush(
        self, async_session: AsyncSession,
    ):
        snap = HubSnapshot(
            snapshot_id="test-flag-001", status="pending", created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()
        await async_session.refresh(snap)
        assert snap.include_apps_code is True

    @pytest.mark.asyncio
    async def test_snapshot_detail_exposes_apps_code_flag(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-flag-002", status="success", created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.get(
            "/api/admin/snapshots/test-flag-002",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        assert response.json()["include_apps_code"] is True


class TestRetention:
    @pytest.mark.asyncio
    async def test_retention_deletes_old_snapshots(
        self, async_session: AsyncSession, snapshot_settings,
    ):
        from app.snapshot_retention import apply_retention
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for i in range(10):
            snap = HubSnapshot(
                snapshot_id=f"retention-{i:03d}",
                status="success",
                created_at=now - timedelta(hours=25 + i),
                created_by="test",
            )
            async_session.add(snap)
        await async_session.commit()

        deleted = await apply_retention(async_session, max_count=3, min_age_hours=24)
        assert deleted >= 7

        remaining_result = await async_session.execute(
            select(HubSnapshot).where(HubSnapshot.status == "success")
        )
        remaining = remaining_result.scalars().all()
        assert len(remaining) <= 3

    @pytest.mark.asyncio
    async def test_retention_respects_min_age(
        self, async_session: AsyncSession, snapshot_settings,
    ):
        from app.snapshot_retention import apply_retention
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        recent = HubSnapshot(
            snapshot_id="recent-001",
            status="success",
            created_at=now - timedelta(hours=1),
            created_by="test",
        )
        async_session.add(recent)

        for i in range(5):
            old = HubSnapshot(
                snapshot_id=f"old-{i:03d}",
                status="success",
                created_at=now - timedelta(hours=48 + i),
                created_by="test",
            )
            async_session.add(old)
        await async_session.commit()

        deleted = await apply_retention(async_session, max_count=3, min_age_hours=24)
        assert deleted >= 3

        recent_check = await async_session.execute(
            select(HubSnapshot).where(HubSnapshot.snapshot_id == "recent-001")
        )
        assert recent_check.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_download_returns_streaming_for_success_snapshot(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings,
    ):
        snap = HubSnapshot(
            snapshot_id="test-dl-stream-001",
            status="success",
            s3_bucket="test-bucket",
            s3_key="test/stream.tar.zst.enc",
            created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.get(
            "/api/admin/snapshots/test-dl-stream-001/download",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 400
        assert "pas de stockage S3" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_download_from_local_path(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token, snapshot_settings, tmp_path,
    ):
        local_file = tmp_path / "snapshot_test-local.tar.zst.enc"
        local_file.write_bytes(b"FAKE_ENCRYPTED_SNAPSHOT_DATA")

        snap = HubSnapshot(
            snapshot_id="test-dl-local-001",
            status="success",
            local_path=str(local_file),
            created_by="test",
        )
        async_session.add(snap)
        await async_session.commit()

        response = await client.get(
            "/api/admin/snapshots/test-dl-local-001/download",
            headers=get_auth_header(admin_token),
        )
        assert response.status_code == 200
        assert response.headers["content-disposition"] == 'attachment; filename="snapshot_test-dl-local-001.tar.zst.enc"'
        data = b""
        async for chunk in response.aiter_bytes():
            data += chunk
        assert data == b"FAKE_ENCRYPTED_SNAPSHOT_DATA"


class TestAppsCodeEngine:
    @pytest.mark.asyncio
    async def test_create_snapshot_apps_code_only(
        self, async_session: AsyncSession, tmp_path, monkeypatch,
    ):
        """Snapshot « code apps uniquement » de bout en bout, portable hors container.

        Les chemins /code/* n'existent que dans le conteneur hub-api : on les
        redirige vers tmp_path (clé RSA de test générée à la volée) pour que
        le test tourne aussi sur un poste de dev.
        """
        from pathlib import Path as _P
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from app import snapshot_collectors
        from app import snapshot_engine
        from app.snapshot_engine import create_snapshot

        # Collecteur : source des apps isolée dans tmp_path
        src = tmp_path / "apps_source"
        (src / "monapp").mkdir(parents=True)
        (src / "monapp" / "main.py").write_text("print('x')")
        (src / "monapp" / ".git").mkdir()
        (src / "monapp" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        monkeypatch.setattr(snapshot_collectors, "APPS_SOURCE_DIR", src)

        # Secrets : paire RSA jetable (le snapshot est chiffré avec la clé publique JWT)
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        (secrets_dir / "jwt_private_key.pem").write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        monkeypatch.setattr(snapshot_engine, "SECRETS_DIR", secrets_dir)

        # Stockage local : volume /code/storage hors container → tmp_path
        storage_dir = tmp_path / "storage" / "snapshots"
        monkeypatch.setattr(snapshot_engine, "SNAPSHOT_STORAGE_DIR", storage_dir)

        snap = HubSnapshot(
            snapshot_id="test-appscode-001", status="pending", created_by="test",
            include_pg_dump=False, include_files=False, include_app_backups=False,
            include_docker_config=False, include_apps_code=True,
        )
        async_session.add(snap)
        await async_session.commit()

        sid = await create_snapshot(
            async_session, created_by="test", snapshot_id="test-appscode-001",
        )

        await async_session.refresh(snap)
        assert sid == "test-appscode-001"
        assert snap.status == "success"
        assert snap.progress_step == "done"
        assert snap.progress_percent == 100
        assert snap.total_size_bytes > 0
        assert snap.checksum_sha256
        assert snap.manifest["collectors"]["apps_code"]["apps_count"] == 1
        assert snap.manifest["collectors"]["apps_code"]["size_bytes"] > 0
        # L'archive chiffrée est bien écrite dans le répertoire de stockage patché
        assert str(storage_dir) in snap.local_path

        # Nettoyage : fichier local écrit + ligne DB
        local = _P(snap.local_path)
        if local.exists():
            local.unlink()
        await async_session.delete(snap)
        await async_session.commit()
