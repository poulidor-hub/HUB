# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests du cache d'objets clés privées RSA du trousseau (perf mints).

Régression perf : chaque mint RS256 relisait + re-parsait la clé privée
RSA-4096 (load_pem_private_key ≈ 250 ms dans le container) au lieu de
réutiliser l'objet clé chargé. Le cache est indexé par kid (thumbprint du
PEM public → PEM privé immuable pour un kid donné) et purgé par reload()
et toute mutation du manifest (rotate/revoke → reload, _save_entries).
"""
import pytest
import app.config as cfg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


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


def _to_pem(key: RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_get_active_private_key_returns_loaded_key(isolated_keyring):
    """get_active_private_key() retourne un objet RSAPrivateKey, même instance
    entre deux appels (parsé une seule fois, pas de re-load_pem_private_key)."""
    isolated_keyring.ensure_seeded()
    key1 = isolated_keyring.get_active_private_key()
    key2 = isolated_keyring.get_active_private_key()
    assert isinstance(key1, RSAPrivateKey)
    assert key1 is key2


def test_get_active_private_key_matches_pem(isolated_keyring):
    """L'objet clé caché correspond bien au PEM privé actif sur disque."""
    isolated_keyring.ensure_seeded()
    assert _to_pem(isolated_keyring.get_active_private_key()).strip() \
        == isolated_keyring.get_active_private_pem()


def test_private_key_cache_purged_on_reload(isolated_keyring):
    """reload() purge le cache : après modification du PEM actif sur disque
    (mutation externe type PRA), la clé retournée correspond au nouveau PEM."""
    isolated_keyring.ensure_seeded()
    kid = isolated_keyring.get_active_kid()
    old_key = isolated_keyring.get_active_private_key()  # remplit le cache
    new_priv, _ = cfg.generate_rsa_keys()
    pem_path = isolated_keyring._resolve_keyring_dir() / f"{kid}.pem"
    pem_path.write_text(new_priv)
    isolated_keyring.reload()
    new_key = isolated_keyring.get_active_private_key()
    assert new_key is not old_key
    assert _to_pem(new_key).strip() == new_priv.strip()
