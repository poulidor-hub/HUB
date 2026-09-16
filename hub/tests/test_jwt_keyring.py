"""Tests du trousseau JWT multi-kid (spec 2026-09-16 §5)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt
import pytest

import app.config as cfg


@pytest.fixture
def isolated_keyring(monkeypatch, tmp_path):
    """Trousseau isolé dans tmp_path, seedé depuis une paire legacy générée."""
    from app import jwt_keyring
    priv, pub = cfg.generate_rsa_keys()
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "jwt_private_key.pem").write_text(priv)
    (secrets_dir / "jwt_public_key.pem").write_text(pub)
    monkeypatch.setattr(cfg, "_SECRETS_DIR", secrets_dir)
    monkeypatch.setattr(jwt_keyring, "_KEYRING_DIR", secrets_dir / "jwt_keys")
    jwt_keyring.reload()
    yield jwt_keyring
    jwt_keyring.reload()


NOW0 = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def test_seed_from_legacy_pair(isolated_keyring, tmp_path):
    isolated_keyring.ensure_seeded()
    kr_dir = tmp_path / "secrets" / "jwt_keys"
    manifest = json.loads((kr_dir / "manifest.json").read_text())
    assert manifest[0]["kid"] == isolated_keyring.LEGACY_KID
    assert manifest[0]["status"] == "active"
    pem = kr_dir / f"{isolated_keyring.LEGACY_KID}.pem"
    assert pem.exists()
    assert (pem.stat().st_mode & 0o777) == 0o600
    assert isolated_keyring.get_active_kid() == isolated_keyring.LEGACY_KID


def test_seed_is_idempotent(isolated_keyring):
    isolated_keyring.ensure_seeded()
    isolated_keyring.ensure_seeded()  # ne duplique pas
    assert len(isolated_keyring._load_entries()) == 1


def test_seed_without_keys_raises(monkeypatch, tmp_path):
    from app import jwt_keyring
    secrets_dir = tmp_path / "empty"
    secrets_dir.mkdir()
    monkeypatch.setattr(cfg, "_SECRETS_DIR", secrets_dir)
    monkeypatch.setattr(jwt_keyring, "_KEYRING_DIR", secrets_dir / "jwt_keys")
    monkeypatch.delenv("AUTO_GENERATE_KEYS", raising=False)
    jwt_keyring.reload()
    with pytest.raises(jwt_keyring.KeyringError):
        jwt_keyring.ensure_seeded()


def test_sign_verify_roundtrip(isolated_keyring):
    isolated_keyring.ensure_seeded()
    token = pyjwt.encode({"sub": "u1"}, isolated_keyring.get_active_private_pem(),
                         algorithm="RS256")
    payload = pyjwt.decode(token, isolated_keyring.get_active_pem_pair()[1],
                           algorithms=["RS256"])
    assert payload["sub"] == "u1"


def test_manifest_missing_but_pem_present_rebuilds(isolated_keyring, tmp_path):
    isolated_keyring.ensure_seeded()
    kr_dir = tmp_path / "secrets" / "jwt_keys"
    (kr_dir / "manifest.json").unlink()
    isolated_keyring.reload()
    isolated_keyring.ensure_seeded()  # ré-adoption du PEM existant
    assert isolated_keyring.get_active_kid() == isolated_keyring.LEGACY_KID
    assert len(isolated_keyring._load_entries()) == 1


def test_adopted_pem_gets_chmod_600(isolated_keyring, tmp_path):
    isolated_keyring.ensure_seeded()
    kr_dir = tmp_path / "secrets" / "jwt_keys"
    pem = kr_dir / f"{isolated_keyring.LEGACY_KID}.pem"
    pem.chmod(0o644)  # simule un restore PRA avec umask laxiste
    (kr_dir / "manifest.json").unlink()
    isolated_keyring.reload()
    isolated_keyring.ensure_seeded()  # chemin d'adoption
    assert (pem.stat().st_mode & 0o777) == 0o600


def test_corrupt_manifest_fails_closed(isolated_keyring, tmp_path):
    isolated_keyring.ensure_seeded()
    kr_dir = tmp_path / "secrets" / "jwt_keys"
    (kr_dir / "manifest.json").write_text("{nope")
    isolated_keyring.reload()
    with pytest.raises(isolated_keyring.KeyringError):
        isolated_keyring.get_active_kid()
