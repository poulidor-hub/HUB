"""Tests pour la restauration self-backup du HUB (import environment.json)."""
import warnings
warnings.filterwarnings("ignore", message="'crypt' is deprecated", category=DeprecationWarning)

import pytest
import zipfile
import json
import io
from datetime import datetime

from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.main import app
from app.database import Base, get_db
from app.models import User, Application, ServiceAccount, UserAppRole
from app.auth import get_password_hash, create_access_token
from app.schemas import BackupImport, UserExport, ImportResult
from app.routes.hub_restore import import_environment, build_backup_import_from_zip_bytes


@pytest.fixture
async def client(async_engine, async_session):
    async def override_get_db():
        yield async_session
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _make_zip(environment_dict: dict) -> bytes:
    """Construit un ZIP contenant environment.json à partir d'un dict."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("environment.json", json.dumps(environment_dict, indent=2))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_users_create(async_session):
    """Nouveaux users (username inexistant) → créés."""
    import_data = BackupImport(
        users=[UserExport(
            username="newuser",
            email="new@test.com",
            hashed_password=get_password_hash("pass123"),
            is_active=True,
            is_admin=False,
        )],
    )
    result = await import_environment(async_session, import_data)
    assert result.users_created == 1
    assert result.users_updated == 0
    db_user = (await async_session.execute(
        select(User).where(User.username == "newuser")
    )).scalar_one()
    assert db_user.email == "new@test.com"
    assert db_user.is_active is True


@pytest.mark.asyncio
async def test_import_users_update(async_session, admin_user):
    """User existant → mis à jour (email, is_admin)."""
    import_data = BackupImport(
        users=[UserExport(
            username="admin",
            email="new-admin-email@test.com",
            hashed_password=get_password_hash("newpass123"),
            is_active=True,
            is_admin=False,  # changement : admin devient user
        )],
    )
    result = await import_environment(async_session, import_data)
    assert result.users_updated == 1
    assert result.users_created == 0
    await async_session.refresh(admin_user)
    assert admin_user.email == "new-admin-email@test.com"
    assert admin_user.is_admin is False


@pytest.mark.asyncio
async def test_import_apps_create(async_session):
    """Nouvelle app → créée."""
    from app.schemas import AppExport
    import_data = BackupImport(
        applications=[AppExport(
            name="Imported App",
            slug="imported-app",
            api_url="http://localhost:9999",
            frontend_url="http://localhost:4999",
        )],
    )
    result = await import_environment(async_session, import_data)
    assert result.apps_created == 1
    db_app = (await async_session.execute(
        select(Application).where(Application.slug == "imported-app")
    )).scalar_one()
    assert db_app.name == "Imported App"


@pytest.mark.asyncio
async def test_import_apps_update(async_session, test_app):
    """App existante → mise à jour."""
    from app.schemas import AppExport
    import_data = BackupImport(
        applications=[AppExport(
            name="Renamed App",
            slug="test-app",
            api_url="http://localhost:8888",
        )],
    )
    result = await import_environment(async_session, import_data)
    assert result.apps_updated == 1
    await async_session.refresh(test_app)
    assert test_app.name == "Renamed App"
    assert test_app.api_url == "http://localhost:8888"


@pytest.mark.asyncio
async def test_import_sa_with_secret(async_session, test_app, monkeypatch, tmp_path):
    """SA avec client_secret_plain → créé + fichier .env écrit."""
    from app.schemas import ServiceAccountExport
    import app.routes.hub_restore as mod
    monkeypatch.setattr(mod, "SECRETS_DIR", tmp_path)
    monkeypatch.setattr(
        "app.routes.service_accounts.SECRETS_DIR", tmp_path, raising=True
    )

    import_data = BackupImport(
        service_accounts=[ServiceAccountExport(
            name="SA - Imported",
            client_id="sa_imported123",
            client_secret_hash="dummy_hash",
            client_secret_plain="super-secret-123",
            is_active=True,
            app_slugs=["test-app"],
        )],
    )
    result = await import_environment(async_session, import_data)
    assert result.service_accounts_created == 1
    db_sa = (await async_session.execute(
        select(ServiceAccount).where(ServiceAccount.client_id == "sa_imported123")
    )).scalar_one()
    assert db_sa.name == "SA - Imported"
    env_file = tmp_path / "sa_test-app.env"
    assert env_file.exists()
    content = env_file.read_text()
    assert "CLIENT_ID=sa_imported123" in content
    assert "super-secret-123" in content or "ENC:" in content


@pytest.mark.asyncio
async def test_import_sa_without_secret(async_session, test_app):
    """SA sans client_secret_plain → skip + warning."""
    from app.schemas import ServiceAccountExport
    import_data = BackupImport(
        service_accounts=[ServiceAccountExport(
            name="SA - No Secret",
            client_id="sa_nosecret456",
            client_secret_hash="dummy_hash",
            client_secret_plain=None,
            is_active=True,
            app_slugs=["test-app"],
        )],
    )
    result = await import_environment(async_session, import_data)
    assert result.service_accounts_created == 0
    assert result.service_accounts_skipped == 1
    assert any("sa_nosecret456" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_import_roles_create(async_session, test_user, test_app):
    """Nouveau user-app-role → créé."""
    from app.schemas import UserAppRoleExport
    import_data = BackupImport(
        user_app_roles=[UserAppRoleExport(
            username="testuser",
            app_slug="test-app",
            role="admin",
            source="manual",
        )],
    )
    result = await import_environment(async_session, import_data)
    assert result.user_app_roles_created == 1
    uar = (await async_session.execute(
        select(UserAppRole).where(
            UserAppRole.user_id == test_user.id,
            UserAppRole.app_id == test_app.id,
        )
    )).scalar_one()
    assert uar.role == "admin"


@pytest.mark.asyncio
async def test_import_full_zip(async_session, monkeypatch, tmp_path):
    """End-to-end : ZIP complet avec users + apps + SA + rôles."""
    import app.routes.hub_restore as mod
    monkeypatch.setattr(mod, "SECRETS_DIR", tmp_path)
    monkeypatch.setattr(
        "app.routes.service_accounts.SECRETS_DIR", tmp_path, raising=True
    )

    env = {
        "version": "1.0",
        "exported_at": "2026-08-02T12:00:00",
        "exported_by": "admin",
        "users": [{
            "username": "fulluser",
            "email": "full@test.com",
            "hashed_password": get_password_hash("pass123"),
            "is_active": True,
            "is_admin": False,
        }],
        "applications": [{
            "name": "Full App",
            "slug": "full-app",
            "api_url": "http://localhost:7777",
        }],
        "service_accounts": [{
            "name": "SA - Full",
            "client_id": "sa_full123",
            "client_secret_hash": "dummy",
            "client_secret_plain": "full-secret",
            "is_active": True,
            "app_slugs": ["full-app"],
        }],
        "user_app_roles": [{
            "username": "fulluser",
            "app_slug": "full-app",
            "role": "user",
            "source": "manual",
        }],
    }
    zip_bytes = _make_zip(env)
    import_data = build_backup_import_from_zip_bytes(zip_bytes)
    result = await import_environment(async_session, import_data)
    assert result.success is True
    assert result.users_created == 1
    assert result.apps_created == 1
    assert result.service_accounts_created == 1
    assert result.user_app_roles_created == 1
    assert result.errors == []


@pytest.mark.asyncio
async def test_endpoint_restore_admin_auth(client, admin_token, monkeypatch, tmp_path):
    import app.routes.hub_restore as mod
    monkeypatch.setattr(mod, "SECRETS_DIR", tmp_path)
    monkeypatch.setattr(
        "app.routes.service_accounts.SECRETS_DIR", tmp_path, raising=True
    )
    env = {
        "version": "1.0", "exported_at": "2026-08-02T12:00:00", "exported_by": "admin",
        "users": [{
            "username": "epuser", "email": "ep@test.com",
            "hashed_password": get_password_hash("p"), "is_active": True, "is_admin": False,
        }],
    }
    zip_bytes = _make_zip(env)
    resp = await client.post(
        "/api/admin/restore",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("backup.zip", zip_bytes, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["users_created"] == 1


@pytest.mark.asyncio
async def test_endpoint_restore_m2m_auth(client, service_account):
    sa_token, _, _ = create_access_token(
        username=service_account.client_id,
        apps={"test-app": "admin"},
        is_service_account=True,
    )
    env = {"version": "1.0", "exported_at": "2026-08-02T12:00:00", "exported_by": "sa",
           "users": [], "applications": [], "service_accounts": [], "user_app_roles": []}
    zip_bytes = _make_zip(env)
    resp = await client.post(
        "/api/admin/restore",
        headers={"Authorization": f"Bearer {sa_token}"},
        files={"file": ("backup.zip", zip_bytes, "application/zip")},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_endpoint_restore_unauthorized(client, user_token):
    env = {"version": "1.0", "exported_at": "2026-08-02T12:00:00", "exported_by": "u"}
    zip_bytes = _make_zip(env)
    resp = await client.post(
        "/api/admin/restore",
        headers={"Authorization": f"Bearer {user_token}"},
        files={"file": ("backup.zip", zip_bytes, "application/zip")},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_endpoint_restore_corrupted_zip(client, admin_token):
    resp = await client.post(
        "/api/admin/restore",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("bad.zip", b"not a zip", "application/zip")},
    )
    assert resp.status_code == 400
    assert "corrompu" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_endpoint_restore_no_env_json(client, admin_token):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("other.txt", "hello")
    resp = await client.post(
        "/api/admin/restore",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("backup.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 400
    assert "environment.json" in resp.json()["detail"]
