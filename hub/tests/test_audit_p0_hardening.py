"""Durcissements P0 issus de l'audit de sécurité d'octobre 2026.

- B-1 : POST /api/admin/restore — plus d'acceptation de tout service account ;
  seuls un utilisateur admin et le SA système interne du hub
  (settings.hub_internal_sa_client_id) sont autorisés.
- B-2 : POST /api/apps/{slug}/backup — un backup_id contenant un chemin
  (traversal `../`, chemin absolu, séparateurs) est rejeté avant toute écriture.
- B-3 : POST /api/auth/login — redirect_to doit être un chemin relatif sûr ;
  URL absolue, protocole-relatif (//) et backslash sont rejetés (open redirect).
- #6  : PUT /api/apps/me/shared — is_shared=true n'auto-approuve plus une
  application pending (statut, is_active et SA restent inchangés).
"""
import io
import json
import uuid
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, get_password_hash
from app.config import get_settings
from app.models import ApprovalStatus, AppBackup, ServiceAccount, User
from tests.conftest import get_auth_header


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sa_token(service_account: ServiceAccount) -> str:
    token, _, _ = create_access_token(
        username=service_account.client_id,
        apps={},
        is_service_account=True,
    )
    return token


def _user_token(user: User) -> str:
    token, _, _ = create_access_token(username=user.username, apps={})
    return token


def _minimal_backup_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "data.json",
            json.dumps({"metadata": {}, "data": {"items": []}}),
        )
    return buffer.getvalue()


@pytest.fixture
def backup_storage(tmp_path, monkeypatch):
    """Stockage backups isolé (même pattern que test_app_backups_vaultwarden)."""
    import app.hub_self_backup as hub_backup_module
    import app.routes.app_backups as app_backups_route

    storage_path = tmp_path / "backups"
    monkeypatch.setattr(app_backups_route, "STORAGE_BASE_DIR", storage_path)
    monkeypatch.setattr(hub_backup_module, "STORAGE_BASE_DIR", storage_path)
    return storage_path


def _restore_zip_with_user(username: str = "pirate") -> bytes:
    """ZIP de restore contenant un compte admin — pour prouver l'absence d'effet."""
    environment = {
        "users": [
            {
                "username": username,
                "email": f"{username}@test.com",
                "hashed_password": get_password_hash("injected123"),
                "is_active": True,
                "is_admin": True,
            }
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("environment.json", json.dumps(environment))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# B-1 — /api/admin/restore : contrôle d'accès
# ---------------------------------------------------------------------------


class TestRestoreAccessControl:
    async def test_restore_rejette_service_account_classique(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        test_app,
        service_account: ServiceAccount,
    ):
        """B-1 : le SA d'une application ne doit pas pouvoir restaurer
        l'environnement du hub (escalade SA → admin)."""
        response = await client.post(
            "/api/admin/restore",
            files={"file": ("hub.zip", _restore_zip_with_user(), "application/zip")},
            headers=get_auth_header(_sa_token(service_account)),
        )
        assert response.status_code == 403, response.text

        # Aucun effet de bord : le compte injecté ne doit pas exister
        injected = (
            await async_session.execute(select(User).where(User.username == "pirate"))
        ).scalar_one_or_none()
        assert injected is None

    async def test_restore_rejette_utilisateur_non_admin(
        self, client: AsyncClient, test_user: User
    ):
        response = await client.post(
            "/api/admin/restore",
            files={"file": ("hub.zip", _restore_zip_with_user(), "application/zip")},
            headers=get_auth_header(_user_token(test_user)),
        )
        assert response.status_code == 403, response.text

    async def test_restore_accepte_admin(
        self, client: AsyncClient, admin_user: User
    ):
        """Comportement historique conservé : un admin peut restaurer."""
        response = await client.post(
            "/api/admin/restore",
            files={"file": ("hub.zip", _restore_zip_with_user("imported_admin"), "application/zip")},
            headers=get_auth_header(_user_token(admin_user)),
        )
        assert response.status_code == 200, response.text

    async def test_restore_accepte_sa_systeme_interne(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        monkeypatch,
    ):
        """Le SA système interne du hub reste autorisé (sauvegarde/restauration auto)."""
        monkeypatch.setattr(
            get_settings(),
            "hub_internal_sa_client_id",
            "sa_hub_internal_test",
            raising=False,
        )
        sa = ServiceAccount(
            name="Hub Internal SA",
            client_id="sa_hub_internal_test",
            client_secret_hash=get_password_hash("secret123"),
            description="SA système interne de test",
            is_active=True,
        )
        async_session.add(sa)
        await async_session.commit()

        response = await client.post(
            "/api/admin/restore",
            files={"file": ("hub.zip", _restore_zip_with_user("restored_by_internal"), "application/zip")},
            headers=get_auth_header(_sa_token(sa)),
        )
        assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# B-2 — backup_id : rejet des chemins (path traversal)
# ---------------------------------------------------------------------------


class TestBackupIdValidation:
    async def test_reception_rejette_backup_id_traversal(
        self,
        client: AsyncClient,
        test_app,
        service_account: ServiceAccount,
        backup_storage,
    ):
        """B-2 : `../` dans backup_id ne doit pas sortir du dossier de stockage."""
        response = await client.post(
            f"/api/apps/{test_app.slug}/backup",
            headers=get_auth_header(_sa_token(service_account)),
            files={"file": ("backup.zip", _minimal_backup_zip(), "application/zip")},
            data={"backup_id": "../../evil"},
        )
        assert response.status_code == 400, response.text
        assert not (backup_storage.parent / "evil.zip").exists()
        assert not (backup_storage / "evil.zip").exists()

    async def test_reception_rejette_backup_id_chemin_absolu(
        self,
        client: AsyncClient,
        test_app,
        service_account: ServiceAccount,
    ):
        response = await client.post(
            f"/api/apps/{test_app.slug}/backup",
            headers=get_auth_header(_sa_token(service_account)),
            files={"file": ("backup.zip", _minimal_backup_zip(), "application/zip")},
            data={"backup_id": "/etc/evil"},
        )
        assert response.status_code == 400, response.text

    async def test_reception_accepte_backup_id_valide(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        test_app,
        service_account: ServiceAccount,
        backup_storage,
    ):
        """Comportement historique conservé : un UUID passe et est stocké."""
        backup_id = str(uuid.uuid4())
        response = await client.post(
            f"/api/apps/{test_app.slug}/backup",
            headers=get_auth_header(_sa_token(service_account)),
            files={"file": ("backup.zip", _minimal_backup_zip(), "application/zip")},
            data={"backup_id": backup_id},
        )
        assert response.status_code == 200, response.text

        row = (
            await async_session.execute(
                select(AppBackup).where(AppBackup.backup_id == backup_id)
            )
        ).scalar_one()
        assert row.storage_path == f"{test_app.slug}/backup_{backup_id}.zip"
        assert (backup_storage / row.storage_path).exists()

    def test_safe_backup_filename_rejette_les_chemins(self):
        """Helper unitaire : tout backup_id non atomique est refusé."""
        from fastapi import HTTPException

        from app.routes.app_backups import safe_backup_filename

        valid_id = str(uuid.uuid4())
        assert safe_backup_filename(valid_id) == f"backup_{valid_id}.zip"
        assert safe_backup_filename("vw-full-001") == "backup_vw-full-001.zip"

        for bad in [
            "../../evil",
            "a/b",
            "a\\b",
            "..",
            ".",
            "",
            "/etc/evil",
            "backup_\n.zip",
            "x" * 300,
        ]:
            with pytest.raises(HTTPException):
                safe_backup_filename(bad)


# ---------------------------------------------------------------------------
# B-3 — /api/auth/login : redirect_to doit être un chemin relatif sûr
# ---------------------------------------------------------------------------


class TestLoginRedirectSafety:
    async def _login(self, client: AsyncClient, test_user: User, redirect_to: str):
        return await client.post(
            "/api/auth/login",
            json={
                "username": test_user.username,
                "password": "testpass123",
                "redirect_to": redirect_to,
            },
        )

    async def test_login_rejette_redirect_absolu(self, client: AsyncClient, test_user: User):
        """B-3 : pas d'open redirect vers un domaine externe avec le token en query."""
        response = await self._login(client, test_user, "https://evil.example/catch")
        assert response.status_code == 400, response.text

    async def test_login_rejette_redirect_protocol_relative(
        self, client: AsyncClient, test_user: User
    ):
        response = await self._login(client, test_user, "//evil.example/catch")
        assert response.status_code == 400, response.text

    async def test_login_rejette_redirect_backslash(
        self, client: AsyncClient, test_user: User
    ):
        response = await self._login(client, test_user, "/\\evil.example")
        assert response.status_code == 400, response.text

    async def test_login_accepte_chemin_relatif(self, client: AsyncClient, test_user: User):
        """Comportement historique conservé : chemin relatif de la même origine."""
        response = await self._login(client, test_user, "/admin/index.html?tab=general")
        assert response.status_code == 303, response.text
        location = response.headers["location"]
        assert location.startswith("/admin/index.html?tab=general&")
        assert "access_token=" in location

    async def test_login_sans_redirect_reponse_json(
        self, client: AsyncClient, test_user: User
    ):
        response = await client.post(
            "/api/auth/login",
            json={"username": test_user.username, "password": "testpass123"},
        )
        assert response.status_code == 200, response.text
        assert "access_token" in response.json()


# ---------------------------------------------------------------------------
# #6 — /api/apps/me/shared : plus d'auto-approbation
# ---------------------------------------------------------------------------


class TestSharedFlagNoAutoApproval:
    async def test_is_shared_n_auto_approuve_plus_une_app_pending(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        test_app,
        service_account: ServiceAccount,
    ):
        """#6 : is_shared=true sur une app pending ne doit plus l'approuver."""
        test_app.approval_status = ApprovalStatus.pending
        test_app.is_active = False
        async_session.add(test_app)
        await async_session.commit()

        response = await client.put(
            "/api/apps/me/shared",
            params={"is_shared": "true"},
            headers={
                "Authorization": f"Bearer {_sa_token(service_account)}",
                "X-Slug": test_app.slug,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["auto_approved"] is False

        await async_session.refresh(test_app)
        assert test_app.is_shared_service is True
        assert test_app.approval_status == ApprovalStatus.pending
        assert test_app.is_active is False

    async def test_is_shared_sur_app_approuvee_propage_toujours(
        self,
        client: AsyncClient,
        async_session: AsyncSession,
        test_app,
        service_account: ServiceAccount,
        other_app,
    ):
        """Comportement historique conservé : une app déjà approuvée devient
        service partagé et se propage aux autres SA, sans changer son statut."""
        test_app.approval_status = ApprovalStatus.approved
        test_app.is_active = True
        async_session.add(test_app)
        await async_session.commit()

        # Un second SA (autre app) qui n'a pas encore accès à test_app
        from sqlalchemy.orm import selectinload

        from app.models import service_account_app_access

        other_sa = ServiceAccount(
            name="Other SA",
            client_id="sa_other_123456789",
            client_secret_hash=get_password_hash("secret123"),
            is_active=True,
        )
        async_session.add(other_sa)
        await async_session.commit()
        await async_session.refresh(other_sa)
        await async_session.execute(
            service_account_app_access.insert().values(
                service_account_id=other_sa.id, app_id=other_app.id
            )
        )
        await async_session.commit()

        response = await client.put(
            "/api/apps/me/shared",
            params={"is_shared": "true"},
            headers={
                "Authorization": f"Bearer {_sa_token(service_account)}",
                "X-Slug": test_app.slug,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["shared_added_to_sa_count"] >= 1

        await async_session.refresh(test_app)
        assert test_app.is_shared_service is True
        assert test_app.approval_status == ApprovalStatus.approved

        result = await async_session.execute(
            select(ServiceAccount)
            .options(selectinload(ServiceAccount.applications))
            .where(ServiceAccount.client_id == "sa_other_123456789")
        )
        other_sa_after = result.scalar_one()
        assert test_app in other_sa_after.applications
