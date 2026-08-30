"""
Tests du déchiffrement des exports de backups protégés par mot de passe (format v2).
"""
import base64
import json

import pytest
from fastapi import HTTPException

import app.routes.app_backups as ab


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
