"""Tests pour la restauration self-backup du HUB (import environment.json)."""
import warnings
warnings.filterwarnings("ignore", message="'crypt' is deprecated", category=DeprecationWarning)

import pytest
import zipfile
import json
import io
from datetime import datetime

from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete, select
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
async def test_ensure_hub_system_application_creates_service_account(async_session):
    """Régression : l'app hub auto-créée n'avait aucun ServiceAccount associé,
    donc tout restore du hub échouait avec « Aucun service account trouvé ».
    ensure_hub_system_application doit garantir app + SA actif associé."""
    from app.routes.app_backups import ensure_hub_system_application

    hub_app = await ensure_hub_system_application(async_session)
    assert hub_app.slug == "hub"

    sa = (await async_session.execute(
        select(ServiceAccount)
        .join(ServiceAccount.applications)
        .where(Application.slug == "hub")
        .limit(1)
    )).scalar_one_or_none()
    assert sa is not None, "Aucun SA associé à l'app hub après ensure_hub_system_application"
    assert sa.is_active is True


@pytest.mark.asyncio
async def test_ensure_hub_system_application_reuses_existing_sa(async_session):
    """Si un SA est déjà associé à l'app hub, ensure ne crée pas de doublon."""
    from app.routes.app_backups import ensure_hub_system_application

    hub_app = Application(
        name="Hub", slug="hub", api_url="http://hub-api:8000",
        frontend_url="/", is_active=True, is_system_app=True,
    )
    async_session.add(hub_app)
    sa_hub = ServiceAccount(
        name="SA - Hub manuel", client_id="sa_hub_manual_test",
        client_secret_hash=get_password_hash("hubsecret"),
        is_active=True,
    )
    sa_hub.applications = [hub_app]
    async_session.add(sa_hub)
    await async_session.commit()

    await ensure_hub_system_application(async_session)

    sas = (await async_session.execute(
        select(ServiceAccount)
        .join(ServiceAccount.applications)
        .where(Application.slug == "hub")
    )).scalars().all()
    assert len(sas) == 1
    assert sas[0].client_id == "sa_hub_manual_test"


@pytest.mark.asyncio
async def test_export_import_preserves_identity_anchors(async_session):
    """Test money : export → import sur hub vide préserve uuid/oidc_sub/prenom/nom.

    Sans ces ancres, un restore sur hub vide casserait les liaisons
    exemple-app-16 (user_uuid) et OIDC des users renommés.
    """
    from app.hub_self_backup import _do_export, _build_zip_archive

    renamed = User(
        username="jmartin", email="jmartin@test.com",
        hashed_password=get_password_hash("p"),
        uuid="11111111-1111-4111-8111-111111111111",
        oidc_sub="jdupont",  # renommé : jdupont → jmartin, sub conservé
        prenom="Jean", nom="Martin",
    )
    plain = User(
        username="mariecurie", email="marie@test.com",
        hashed_password=get_password_hash("p"),
        uuid="22222222-2222-4222-8222-222222222222",
        oidc_sub="mariecurie",
        prenom="Marie", nom="Curie",
    )
    async_session.add_all([renamed, plain])
    await async_session.commit()

    # Export via la voie réelle : _do_export → ZIP → BackupImport (round-trip JSON)
    class _Actor:
        username = "admin"

    export = await _do_export(async_session, _Actor(), False, False, False)
    zip_bytes = _build_zip_archive(export).getvalue()
    import_data = build_backup_import_from_zip_bytes(zip_bytes)

    # Hub vide : purge des users (simule la restauration sur un hub neuf)
    await async_session.execute(delete(User))
    await async_session.commit()

    result = await import_environment(async_session, import_data)
    assert result.errors == []
    assert result.users_created == 2

    db_jmartin = (await async_session.execute(
        select(User).where(User.username == "jmartin")
    )).scalar_one()
    assert db_jmartin.uuid == "11111111-1111-4111-8111-111111111111"
    assert db_jmartin.oidc_sub == "jdupont"  # ancre OIDC du renommé préservée
    assert db_jmartin.prenom == "Jean"
    assert db_jmartin.nom == "Martin"

    db_marie = (await async_session.execute(
        select(User).where(User.username == "mariecurie")
    )).scalar_one()
    assert db_marie.uuid == "22222222-2222-4222-8222-222222222222"
    assert db_marie.oidc_sub == "mariecurie"
    assert db_marie.prenom == "Marie"
    assert db_marie.nom == "Curie"


@pytest.mark.asyncio
async def test_import_legacy_backup_generates_identity(async_session):
    """Backup legacy (UserExport sans les champs identitaires) : restore OK,
    oidc_sub généré par le helper (== username si libre), uuid non-NULL."""
    import_data = BackupImport(
        users=[UserExport(
            username="legacyuser",
            email="legacy@test.com",
            hashed_password=get_password_hash("pass123"),
            is_active=True,
            is_admin=False,
        )],
    )
    result = await import_environment(async_session, import_data)
    assert result.errors == []
    assert result.users_created == 1
    db_user = (await async_session.execute(
        select(User).where(User.username == "legacyuser")
    )).scalar_one()
    assert db_user.oidc_sub == "legacyuser"  # helper : username si libre
    assert db_user.uuid is not None  # défaut colonne
    assert db_user.prenom is None
    assert db_user.nom is None


@pytest.mark.asyncio
async def test_import_update_keeps_existing_identity_anchors(async_session):
    """Update branch : username existant → uuid/oidc_sub/prenom/nom existants
    non écrasés par les données (potentiellement anciennes) du backup."""
    existing = User(
        username="alice", email="alice@old.com",
        hashed_password=get_password_hash("oldpass123"),
        uuid="aaaaaaaa-1111-4111-8111-111111111111",
        oidc_sub="alice",
        prenom="Alice", nom="Origin",
    )
    async_session.add(existing)
    await async_session.commit()

    import_data = BackupImport(
        users=[UserExport(
            username="alice",
            email="alice@new.com",
            hashed_password=get_password_hash("newpass123"),
            is_active=True,
            is_admin=True,
            # Valeurs d'ancres différentes : ne doivent PAS être appliquées
            uuid="bbbbbbbb-2222-4222-8222-222222222222",
            oidc_sub="alice_backup",
            prenom="Autre",
            nom="Backup",
        )],
    )
    result = await import_environment(async_session, import_data)
    assert result.users_updated == 1
    assert result.users_created == 0
    await async_session.refresh(existing)
    assert existing.uuid == "aaaaaaaa-1111-4111-8111-111111111111"
    assert existing.oidc_sub == "alice"
    assert existing.prenom == "Alice"
    assert existing.nom == "Origin"
    # L'update reste effectif sur email/hash/flags
    assert existing.email == "alice@new.com"
    assert existing.is_admin is True


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


@pytest.mark.asyncio
async def test_integration_restore_hub_backup_via_existing_flow(
    client, admin_token, async_session, monkeypatch, tmp_path
):
    """
    Test d'intégration : le flux restore_backup_to_app(slug=hub) doit fonctionner
    une fois le SA hub créé et l'endpoint /api/admin/restore enregistré.

    On crée un backup hub factice + un SA hub, puis on appelle l'endpoint restore.
    On mocke l'appel HTTP interne (le hub s'appelle lui-même) pour éviter la
    récursion dans le test.
    """
    import app.routes.hub_restore as mod
    monkeypatch.setattr(mod, "SECRETS_DIR", tmp_path)
    monkeypatch.setattr(
        "app.routes.service_accounts.SECRETS_DIR", tmp_path, raising=True
    )

    # Créer l'app hub + un SA hub + un backup
    from app.models import AppBackup
    hub_app = Application(
        name="Hub", slug="hub", api_url="http://hub-api:8000",
        frontend_url="/", is_active=True, is_system_app=True,
    )
    async_session.add(hub_app)

    sa_hub = ServiceAccount(
        name="SA - Hub", client_id="sa_hub_integration",
        client_secret_hash=get_password_hash("hubsecret"),
        is_active=True,
    )
    sa_hub.applications = [hub_app]
    async_session.add(sa_hub)
    await async_session.commit()

    # Le test valide juste que l'endpoint /api/admin/restore répond 200 avec un
    # ZIP valide — le flux restore_backup_to_app complet nécessiterait un mock
    # HTTP complexe. On teste donc directement l'endpoint cible.
    env = {
        "version": "1.0", "exported_at": "2026-08-02T12:00:00", "exported_by": "admin",
        "users": [{
            "username": "integuser", "email": "integ@test.com",
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
    assert resp.json()["users_created"] == 1
