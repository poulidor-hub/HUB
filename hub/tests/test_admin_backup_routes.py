# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests des routes d'export/import d'environnement du Hub (/api/admin/backup/*).

Contexte : les routes ont été supprimées du backend (commit e0708e0, remplacées
par les snapshots) mais restent appelées par les outils MCP (hub_backup_export_env,
hub_backup_import_env) et le module frontend backup-import.js. Tout upload > 10 Mo
renvoyait en outre un 413 trompeur (middleware anti-DoS court-circuitant avant le
404). Cette suite garantit la présence et le comportement des 5 routes :
  - GET  /api/admin/backup/export
  - GET  /api/admin/backup/export/download
  - POST /api/admin/backup/import
  - POST /api/admin/backup/import/upload
  - POST /api/admin/backup/import/preview
"""
import base64
import hashlib
import io
import json
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Application, ServiceAccount, UserAppRole, AppBackup, ApprovalStatus
from app.auth import get_password_hash


# =============================================================================
# Présence des routes (régression du bug initial : 404 / 413 trompeur)
# =============================================================================


class TestRoutesExist:
    async def test_export_route_exists(self, client: AsyncClient, admin_token: str):
        """GET /export ne renvoie PAS 404 (route réimplémentée)."""
        response = await client.get(
            "/api/admin/backup/export",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code != 404
        assert response.status_code == 200

    async def test_import_upload_route_exists(self, client: AsyncClient, admin_token: str):
        """POST /import/upload ne renvoie PAS 404 pour un petit fichier."""
        response = await client.post(
            "/api/admin/backup/import/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("backup.json", json.dumps({"users": []}).encode(), "application/json")},
        )
        assert response.status_code != 404
        assert response.status_code == 200

    async def test_preview_route_exists(self, client: AsyncClient, admin_token: str):
        """POST /import/preview ne renvoie PAS 404."""
        response = await client.post(
            "/api/admin/backup/import/preview",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("backup.json", json.dumps({"users": []}).encode(), "application/json")},
        )
        assert response.status_code == 200

    async def test_upload_over_10mb_not_rejected_by_middleware(
        self, client: AsyncClient, admin_token: str
    ):
        """Un upload > 10 Mo sur /import/upload n'est plus rejeté en 413 (limite élargie)."""
        big_json = json.dumps({"users": [], "padding": "x" * (11 * 1024 * 1024)}).encode()
        response = await client.post(
            "/api/admin/backup/import/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("backup.json", big_json, "application/json")},
        )
        assert response.status_code != 413
        assert response.status_code == 200

    async def test_preview_over_10mb_not_rejected_by_middleware(
        self, client: AsyncClient, admin_token: str
    ):
        """Un upload > 10 Mo sur /import/preview n'est plus rejeté en 413."""
        big_json = json.dumps({"users": [], "padding": "x" * (11 * 1024 * 1024)}).encode()
        response = await client.post(
            "/api/admin/backup/import/preview",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("backup.json", big_json, "application/json")},
        )
        assert response.status_code != 413
        assert response.status_code == 200


# =============================================================================
# Export
# =============================================================================


class TestExport:
    async def test_export_requires_admin(self, client: AsyncClient, async_session: AsyncSession):
        user = User(
            username="user_backup_nonadmin",
            hashed_password=get_password_hash("password123"),
            is_admin=False,
        )
        async_session.add(user)
        await async_session.commit()

        login = await client.post("/api/auth/token", data={
            "grant_type": "password",
            "username": "user_backup_nonadmin",
            "password": "password123",
        })
        token = login.json()["access_token"]

        response = await client.get(
            "/api/admin/backup/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    async def test_export_full_data(self, client: AsyncClient, async_session: AsyncSession, admin_token: str):
        admin = User(
            username="admin_backup_exp",
            email="admin_backup_exp@test.com",
            hashed_password=get_password_hash("admin123"),
            is_admin=True,
            must_change_password=True,
            can_change_password=False,
        )
        async_session.add(admin)

        user = User(
            username="test_user_exp",
            email="test_user_exp@test.com",
            hashed_password=get_password_hash("test123"),
            is_admin=False,
            prenom="Jean",
            nom="Dupont",
        )
        async_session.add(user)
        await async_session.flush()

        app = Application(
            name="Test App Export",
            slug="test-app-export",
            description="Application de test",
            icon="bi-gear",
            color="#ff0000",
            api_url="http://localhost:8080",
            frontend_url="http://localhost:3000",
            is_active=True,
            is_shared_service=True,
            approval_status=ApprovalStatus.approved,
        )
        async_session.add(app)
        await async_session.flush()

        async_session.add(UserAppRole(user_id=user.id, app_id=app.id, role="admin"))

        sa = ServiceAccount(
            name="SA Test Export",
            client_id="sa_test_export_123",
            client_secret_hash=get_password_hash("secret123"),
            description="Service account de test",
        )
        async_session.add(sa)
        await async_session.commit()

        response = await client.get(
            "/api/admin/backup/export",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

        data = response.json()
        assert "version" in data
        assert "exported_at" in data
        assert "users" in data
        assert "applications" in data
        assert "service_accounts" in data
        assert "user_app_roles" in data

        users_by_name = {u["username"]: u for u in data["users"]}
        admin_export = users_by_name["admin_backup_exp"]
        assert admin_export["must_change_password"] is True
        assert admin_export["can_change_password"] is False
        assert admin_export["hashed_password"].startswith("$2b$")
        # Champs modernes (uuid/identité civile) exportés
        assert "uuid" in admin_export and "oidc_sub" in admin_export
        assert users_by_name["test_user_exp"]["prenom"] == "Jean"

        apps_by_slug = {a["slug"]: a for a in data["applications"]}
        app_export = apps_by_slug["test-app-export"]
        assert app_export["icon"] == "bi-gear"
        assert app_export["is_shared_service"] is True
        assert app_export["approval_status"] == "approved"

    async def test_export_without_jwt_keys_by_default(self, client: AsyncClient, admin_token: str):
        response = await client.get(
            "/api/admin/backup/export",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = response.json()
        assert data.get("jwt_private_key_b64") is None
        assert data.get("backup_private_key_b64") is None

    async def test_export_download_json(self, client: AsyncClient, admin_token: str):
        response = await client.get(
            "/api/admin/backup/export/download?include_jwt_keys=false",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        assert "attachment" in response.headers.get("content-disposition", "")
        data = response.json()
        assert "users" in data

    async def test_export_download_zip_skips_unfinished_app_backups(
        self, client: AsyncClient, async_session: AsyncSession, admin_token: str, test_app: Application
    ):
        """L'export ZIP ignore les backups sans fichier physique au lieu de planter."""
        pending_backup = AppBackup(
            backup_id="pending-without-file",
            app_id=test_app.id,
            app_slug=test_app.slug,
            status="pending",
            storage_path=None,
            total_size_bytes=0,
            created_by="test",
            created_by_type="hub",
        )
        async_session.add(pending_backup)
        await async_session.commit()

        response = await client.get(
            "/api/admin/backup/export/download?include_jwt_keys=false&include_sa_secrets=true&include_app_backups=true",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "environment.json" in archive.namelist()
            assert all("pending-without-file" not in name for name in archive.namelist())


# =============================================================================
# Import (JSON direct)
# =============================================================================


class TestImport:
    async def test_import_requires_admin(self, client: AsyncClient, async_session: AsyncSession):
        user = User(
            username="user_backup_import",
            hashed_password=get_password_hash("password123"),
            is_admin=False,
        )
        async_session.add(user)
        await async_session.commit()

        login = await client.post("/api/auth/token", data={
            "grant_type": "password",
            "username": "user_backup_import",
            "password": "password123",
        })
        token = login.json()["access_token"]

        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {token}"},
            json={"users": [], "applications": []},
        )
        assert response.status_code == 403

    async def test_import_creates_users_with_fields(self, client: AsyncClient, async_session: AsyncSession, admin_token: str):
        backup_data = {
            "import_mode": "skip",
            "users": [
                {
                    "username": "imported_user",
                    "email": "imported@test.com",
                    "hashed_password": get_password_hash("imported123"),
                    "is_active": True,
                    "is_admin": False,
                    "must_change_password": True,
                    "can_change_password": False,
                    "prenom": "Alice",
                    "nom": "Martin",
                }
            ],
            "applications": [],
            "service_accounts": [],
            "user_app_roles": [],
        }
        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=backup_data,
        )
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert result["users_created"] == 1

        user_result = await async_session.execute(
            select(User).where(User.username == "imported_user")
        )
        user = user_result.scalar_one()
        assert user.must_change_password is True
        assert user.can_change_password is False
        assert user.prenom == "Alice"

    async def test_import_creates_apps_with_roles(self, client: AsyncClient, async_session: AsyncSession, admin_token: str):
        backup_data = {
            "import_mode": "skip",
            "users": [],
            "applications": [
                {
                    "name": "App Importée",
                    "slug": "imported-app",
                    "description": "Description test",
                    "icon": "bi-star",
                    "color": "#00ff00",
                    "api_url": "http://localhost:9000",
                    "is_active": True,
                    "requires_auth": True,
                    "is_shared_service": False,
                    "approval_status": "pending",
                    "rejection_reason": None,
                    "available_roles": [{"name": "admin", "description": "Administrateur"}],
                }
            ],
            "service_accounts": [],
            "user_app_roles": [],
        }
        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=backup_data,
        )
        assert response.status_code == 200
        result = response.json()
        assert result["apps_created"] == 1

        app_result = await async_session.execute(
            select(Application).where(Application.slug == "imported-app")
        )
        app = app_result.scalar_one()
        assert app.approval_status == ApprovalStatus.pending
        await async_session.refresh(app, ["available_roles"])
        assert any(r.name == "admin" for r in app.available_roles)

    async def test_import_mode_error_conflicts(self, client: AsyncClient, async_session: AsyncSession, admin_token: str, test_app: Application):
        backup_data = {
            "import_mode": "error",
            "users": [],
            "applications": [
                {
                    "name": test_app.name,
                    "slug": test_app.slug,
                    "api_url": test_app.api_url,
                    "is_active": True,
                    "requires_auth": True,
                    "is_shared_service": False,
                    "approval_status": "approved",
                    "available_roles": [],
                }
            ],
            "service_accounts": [],
            "user_app_roles": [],
        }
        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=backup_data,
        )
        assert response.status_code == 409

    async def test_import_mode_update_updates_app(self, client: AsyncClient, async_session: AsyncSession, admin_token: str, test_app: Application):
        backup_data = {
            "import_mode": "update",
            "users": [],
            "applications": [
                {
                    "name": "Nom Mis À Jour",
                    "slug": test_app.slug,
                    "description": "Description mise à jour",
                    "api_url": test_app.api_url,
                    "is_active": True,
                    "requires_auth": True,
                    "is_shared_service": False,
                    "approval_status": "approved",
                    "available_roles": [],
                }
            ],
            "service_accounts": [],
            "user_app_roles": [],
        }
        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=backup_data,
        )
        assert response.status_code == 200
        result = response.json()
        assert result["apps_updated"] == 1

        app_result = await async_session.execute(
            select(Application).where(Application.slug == test_app.slug)
        )
        app = app_result.scalar_one()
        assert app.name == "Nom Mis À Jour"

    async def test_import_creates_service_account_with_secret_file(
        self, client: AsyncClient, async_session: AsyncSession, admin_token: str, monkeypatch, tmp_path
    ):
        """L'import crée le SA, l'associe aux apps et écrit son fichier de credentials."""
        sa_secrets_dir = tmp_path / "service_accounts"
        monkeypatch.setattr("app.routes.service_accounts.SECRETS_DIR", sa_secrets_dir)

        backup_data = {
            "import_mode": "skip",
            "users": [],
            "applications": [
                {
                    "name": "App SA",
                    "slug": "app-sa",
                    "api_url": "http://localhost:9100",
                    "is_active": True,
                    "requires_auth": True,
                    "is_shared_service": False,
                    "approval_status": "approved",
                    "available_roles": [],
                }
            ],
            "service_accounts": [
                {
                    "name": "SA Importé",
                    "client_id": "sa_imported_123",
                    "client_secret_hash": get_password_hash("secret123"),
                    "client_secret_plain": "plaintext-secret",
                    "description": "SA importé",
                    "is_active": True,
                    "app_slugs": ["app-sa"],
                }
            ],
            "user_app_roles": [],
        }
        response = await client.post(
            "/api/admin/backup/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=backup_data,
        )
        assert response.status_code == 200
        result = response.json()
        assert result["service_accounts_created"] == 1

        env_files = list(sa_secrets_dir.glob("sa_*.env"))
        assert len(env_files) == 1
        content = env_files[0].read_text()
        assert "CLIENT_ID=sa_imported_123" in content
        assert "plaintext-secret" in content


# =============================================================================
# Import depuis fichier uploadé (JSON / ZIP) + preview
# =============================================================================


def _make_env_json() -> dict:
    return {
        "version": "1.0",
        "exported_at": "2026-10-06T12:00:00",
        "exported_by": "admin",
        "users": [
            {
                "username": "upload_user",
                "email": "upload@test.com",
                "hashed_password": get_password_hash("upload123"),
                "is_active": True,
                "is_admin": False,
            }
        ],
        "applications": [],
        "service_accounts": [],
        "user_app_roles": [],
        "app_backups": [],
    }


class TestImportUpload:
    async def test_upload_json_imports(self, client: AsyncClient, admin_token: str):
        response = await client.post(
            "/api/admin/backup/import/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("backup.json", json.dumps(_make_env_json()).encode(), "application/json")},
            data={"import_mode": "skip"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert result["users_created"] == 1

    async def test_upload_zip_with_app_backup(
        self, client: AsyncClient, async_session: AsyncSession, admin_token: str, monkeypatch, tmp_path
    ):
        """Un ZIP complet (environment.json + backups/) restaure les backups d'apps."""
        storage_dir = tmp_path / "backups"
        monkeypatch.setattr("app.routes.admin_backup.STORAGE_BASE_DIR", storage_dir)

        backup_content = b"fake-zip-content-of-app-backup"
        checksum = hashlib.sha256(backup_content).hexdigest()

        env = _make_env_json()
        env["applications"] = [
            {
                "name": "App ZIP",
                "slug": "app-zip",
                "api_url": "http://localhost:9200",
                "is_active": True,
                "requires_auth": True,
                "is_shared_service": False,
                "approval_status": "approved",
                "available_roles": [],
            }
        ]
        env["app_backups"] = [
            {
                "backup_id": "bk-zip-123",
                "app_slug": "app-zip",
                "app_version": "1.0.0",
                "total_items": 3,
                "total_size_bytes": len(backup_content),
                "checksum": checksum,
                "storage_filename": "backups/app-zip/backup_bk-zip-123.zip",
                "created_by": "admin",
                "created_by_type": "hub",
            }
        ]

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("environment.json", json.dumps(env))
            zf.writestr("backups/app-zip/backup_bk-zip-123.zip", backup_content)
        buffer.seek(0)

        response = await client.post(
            "/api/admin/backup/import/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("backup.zip", buffer.getvalue(), "application/zip")},
            data={"import_mode": "skip", "include_app_backups": "true"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert result["app_backups_created"] == 1

        restored = storage_dir / "app-zip" / "backup_bk-zip-123.zip"
        assert restored.exists()
        assert restored.read_bytes() == backup_content

        db_backup = await async_session.execute(
            select(AppBackup).where(AppBackup.backup_id == "bk-zip-123")
        )
        backup_row = db_backup.scalar_one()
        assert backup_row.storage_path == "app-zip/backup_bk-zip-123.zip"
        assert backup_row.checksum == checksum

    async def test_upload_invalid_file_rejected(self, client: AsyncClient, admin_token: str):
        response = await client.post(
            "/api/admin/backup/import/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("backup.txt", b"ceci n'est ni json ni zip", "text/plain")},
        )
        assert response.status_code == 400

    async def test_upload_empty_file_rejected(self, client: AsyncClient, admin_token: str):
        response = await client.post(
            "/api/admin/backup/import/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("backup.json", b"", "application/json")},
        )
        assert response.status_code == 400


class TestPreview:
    async def test_preview_json(self, client: AsyncClient, admin_token: str):
        response = await client.post(
            "/api/admin/backup/import/preview",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("backup.json", json.dumps(_make_env_json()).encode(), "application/json")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["format"] == "json"
        assert data["users_count"] == 1
        assert data["has_jwt_keys"] is False

    async def test_preview_zip(self, client: AsyncClient, admin_token: str):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("environment.json", json.dumps(_make_env_json()))
        buffer.seek(0)

        response = await client.post(
            "/api/admin/backup/import/preview",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("backup.zip", buffer.getvalue(), "application/zip")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["format"] == "zip"
        assert data["users_count"] == 1
