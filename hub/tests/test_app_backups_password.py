"""
Tests du déchiffrement des exports de backups protégés par mot de passe (format v2).
"""
import base64
import io
import json
import zipfile

import pytest
from fastapi import HTTPException

import app.routes.app_backups as ab
from tests.conftest import get_auth_header


def test_create_and_decrypt_password_roundtrip():
    data = b"donnees-de-backup-zip"
    package = ab.create_password_encrypted_package(data, "bk-1", "test-app", "motdepasse-fort-1")

    decrypted, header = ab.decrypt_encrypted_package(package, password="motdepasse-fort-1")

    assert decrypted == data
    assert header["encryption_mode"] == "password"
    assert header["version"] == "2.0"
    assert header["backup_id"] == "bk-1"
    assert header["app_slug"] == "test-app"


def test_decrypt_wrong_password_generic_error():
    data = b"donnees-de-backup-zip"
    package = ab.create_password_encrypted_package(data, "bk-1", "test-app", "motdepasse-fort-1")

    with pytest.raises(HTTPException) as exc:
        ab.decrypt_encrypted_package(package, password="mauvais-mot-de-passe")
    assert exc.value.status_code == 400
    assert exc.value.detail == ab.PASSWORD_DECRYPT_ERROR


def test_decrypt_password_mode_without_password():
    data = b"donnees"
    package = ab.create_password_encrypted_package(data, "bk-1", "test-app", "motdepasse-fort-1")

    with pytest.raises(HTTPException) as exc:
        ab.decrypt_encrypted_package(package, password=None)
    assert exc.value.status_code == 400


def test_password_package_structure():
    data = b"xyz"
    package = ab.create_password_encrypted_package(data, "bk-2", "app", "motdepasse-fort-1")

    header_length = int.from_bytes(package[:4], byteorder="big")
    header = json.loads(package[4:4 + header_length])
    assert header["encryption_mode"] == "password"
    assert header["kdf"] == "PBKDF2-HMAC-SHA256"
    assert header["kdf_iterations"] == ab.PBKDF2_ITERATIONS
    assert len(base64.b64decode(header["salt_b64"])) == 16
    assert len(base64.b64decode(header["nonce_b64"])) == 12
    # Le mot de passe n'apparaît nulle part dans le package
    assert b"motdepasse-fort-1" not in package


def test_decrypt_corrupted_header_generic_error():
    # salt_b64 manquant -> erreur générique, pas de fuite d'interne
    data = b"xyz"
    package = bytearray(
        ab.create_password_encrypted_package(data, "bk-3", "app", "motdepasse-fort-1")
    )
    header_length = int.from_bytes(package[:4], byteorder="big")
    header = json.loads(bytes(package[4:4 + header_length]))
    del header["salt_b64"]
    new_header = json.dumps(header).encode()
    corrupted = len(new_header).to_bytes(4, byteorder="big") + new_header + bytes(package[4 + header_length:])

    with pytest.raises(HTTPException) as exc:
        ab.decrypt_encrypted_package(bytes(corrupted), password="motdepasse-fort-1")
    assert exc.value.detail == ab.PASSWORD_DECRYPT_ERROR


def test_decrypt_huge_iterations_rejected():
    data = b"xyz"
    package = ab.create_password_encrypted_package(data, "bk-4", "app", "motdepasse-fort-1")
    header_length = int.from_bytes(package[:4], byteorder="big")
    header = json.loads(package[4:4 + header_length])
    header["kdf_iterations"] = 999_999_999
    new_header = json.dumps(header).encode()
    corrupted = len(new_header).to_bytes(4, byteorder="big") + new_header + package[4 + header_length:]

    with pytest.raises(HTTPException) as exc:
        ab.decrypt_encrypted_package(corrupted, password="motdepasse-fort-1")
    assert exc.value.detail == ab.PASSWORD_DECRYPT_ERROR


def test_decrypt_checksum_mismatch_generic_error():
    data = b"xyz"
    package = ab.create_password_encrypted_package(data, "bk-5", "app", "motdepasse-fort-1")
    header_length = int.from_bytes(package[:4], byteorder="big")
    header = json.loads(package[4:4 + header_length])
    header["checksum"] = "0" * 64
    new_header = json.dumps(header).encode()
    corrupted = len(new_header).to_bytes(4, byteorder="big") + new_header + package[4 + header_length:]

    with pytest.raises(HTTPException) as exc:
        ab.decrypt_encrypted_package(corrupted, password="motdepasse-fort-1")
    assert "Checksum invalide" in exc.value.detail


# ===== Endpoint POST /api/apps/{slug}/backup/import =====

def make_small_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.json", json.dumps({"metadata": {}, "data": {}}))
    return buffer.getvalue()


@pytest.fixture
def backup_storage(tmp_path, monkeypatch):
    storage_path = tmp_path / "backups"
    monkeypatch.setattr(ab, "STORAGE_BASE_DIR", storage_path)
    return storage_path


async def test_import_stores_backup(client, admin_token, test_app, backup_storage, async_session):
    """Import d'un .encrypted password : déchiffré + stocké comme backup de l'app."""
    from sqlalchemy import select
    from app.models import AppBackup

    content = make_small_zip()
    package = ab.create_password_encrypted_package(content, "bk-mig-1", test_app.slug, "motdepasse-fort-1")

    resp = await client.post(
        f"/api/apps/{test_app.slug}/backup/import",
        headers=get_auth_header(admin_token),
        data={"password": "motdepasse-fort-1", "store_backup": "true", "restore_now": "false"},
        files={"file": ("backup_bk-mig-1.encrypted", package, "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["backup_stored"] is True
    assert body["backup_id"] == "bk-mig-1"

    row = (await async_session.execute(
        select(AppBackup).where(AppBackup.backup_id == "bk-mig-1")
    )).scalar_one_or_none()
    assert row is not None
    assert (ab.STORAGE_BASE_DIR / row.storage_path).exists()


async def test_import_without_password_fails(client, admin_token, test_app, backup_storage):
    package = ab.create_password_encrypted_package(b"x", "bk-mig-2", test_app.slug, "motdepasse-fort-1")
    resp = await client.post(
        f"/api/apps/{test_app.slug}/backup/import",
        headers=get_auth_header(admin_token),
        data={"store_backup": "true"},
        files={"file": ("backup_bk-mig-2.encrypted", package, "application/octet-stream")},
    )
    assert resp.status_code == 400


async def test_import_wrong_password_fails(client, admin_token, test_app, backup_storage):
    package = ab.create_password_encrypted_package(b"x", "bk-mig-3", test_app.slug, "motdepasse-fort-1")
    resp = await client.post(
        f"/api/apps/{test_app.slug}/backup/import",
        headers=get_auth_header(admin_token),
        data={"password": "mauvais-mot-de-passe", "store_backup": "true"},
        files={"file": ("backup_bk-mig-3.encrypted", package, "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == ab.PASSWORD_DECRYPT_ERROR


async def test_import_duplicate_backup_id_returns_409(client, admin_token, test_app, backup_storage):
    """Ré-importer le même .encrypted renvoie 409, pas 500."""
    content = make_small_zip()
    package = ab.create_password_encrypted_package(content, "bk-dup-1", test_app.slug, "motdepasse-fort-1")
    data = {
        "password": "motdepasse-fort-1",
        "store_backup": "true",
        "restore_now": "false",
    }
    files = {"file": ("backup_bk-dup-1.encrypted", package, "application/octet-stream")}
    headers = get_auth_header(admin_token)
    url = f"/api/apps/{test_app.slug}/backup/import"

    first = await client.post(url, headers=headers, data=data, files=files)
    assert first.status_code == 200, first.text

    second = await client.post(url, headers=headers, data=data, files=files)
    assert second.status_code == 409, second.text
    body = second.json()
    assert "bk-dup-1" in body["detail"]


# ===== Endpoint POST /api/apps/{slug}/backup/restore/upload =====

async def test_restore_upload_accepts_password(client, admin_token, test_app, service_account, backup_storage):
    package = ab.create_password_encrypted_package(
        make_small_zip(), "bk-up-1", test_app.slug, "motdepasse-fort-1"
    )
    resp = await client.post(
        f"/api/apps/{test_app.slug}/backup/restore/upload",
        headers=get_auth_header(admin_token),
        data={"password": "motdepasse-fort-1"},
        files={"file": ("backup_bk-up-1.encrypted", package, "application/octet-stream")},
    )
    # L'app cible est injoignable en test -> 502 prouve que le déchiffrement a réussi
    assert resp.status_code == 502


async def test_restore_upload_wrong_password(client, admin_token, test_app, service_account, backup_storage):
    package = ab.create_password_encrypted_package(
        make_small_zip(), "bk-up-2", test_app.slug, "motdepasse-fort-1"
    )
    resp = await client.post(
        f"/api/apps/{test_app.slug}/backup/restore/upload",
        headers=get_auth_header(admin_token),
        data={"password": "mauvais-mot-de-passe"},
        files={"file": ("backup_bk-up-2.encrypted", package, "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == ab.PASSWORD_DECRYPT_ERROR
