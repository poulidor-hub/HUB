"""Tests snapshot_crypto — chiffrement paire backup + chaîne de repli legacy."""
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import app.config as cfg
from app.snapshot_crypto import (
    SnapshotDecryptError,
    decrypt_snapshot_bytes,
    encrypt_file,
    list_candidate_private_keys,
)


def _make_secrets(tmp_path: Path) -> Path:
    """secrets/ isolé avec paire backup et paire JWT legacy générées."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    backup_priv, backup_pub = cfg.generate_rsa_keys()
    legacy_priv, legacy_pub = cfg.generate_rsa_keys()
    (secrets_dir / "backup_private_key.pem").write_text(backup_priv)
    (secrets_dir / "backup_public_key.pem").write_text(backup_pub)
    (secrets_dir / "jwt_private_key.pem").write_text(legacy_priv)
    (secrets_dir / "jwt_public_key.pem").write_text(legacy_pub)
    return secrets_dir


def _write_envelope(dest: Path, public_pem: str, plaintext: bytes) -> None:
    """Enveloppe au format historique, chiffrée avec la clé publique donnée."""
    public_key = serialization.load_pem_public_key(public_pem.encode())
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = b"\x01" * 12
    ct = AESGCM(aes_key).encrypt(nonce, plaintext, None)
    enc_key = public_key.encrypt(
        aes_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None),
    )
    dest.write_bytes(len(enc_key).to_bytes(4, "big") + enc_key + nonce + ct)


def test_encrypt_uses_backup_key_and_roundtrip(tmp_path):
    secrets_dir = _make_secrets(tmp_path)
    plain = tmp_path / "in.bin"
    plain.write_bytes(b"HUB-SNAPSHOT" * 100)
    out = tmp_path / "out.enc"
    encrypt_file(plain, out, secrets_dir=secrets_dir)
    data, desc = decrypt_snapshot_bytes(out.read_bytes(), secrets_dir=secrets_dir)
    assert data == plain.read_bytes()
    assert desc == "backup"


def test_decrypt_falls_back_to_legacy_jwt_key(tmp_path):
    secrets_dir = _make_secrets(tmp_path)
    (secrets_dir / "backup_private_key.pem").unlink()  # snapshot d'avant découplage
    env = tmp_path / "legacy.enc"
    _write_envelope(env, (secrets_dir / "jwt_public_key.pem").read_text(), b"OLD-SNAPSHOT")
    data, desc = decrypt_snapshot_bytes(env.read_bytes(), secrets_dir=secrets_dir)
    assert data == b"OLD-SNAPSHOT"
    assert desc == "jwt-legacy"


def test_decrypt_falls_back_to_keyring_pem(tmp_path):
    secrets_dir = _make_secrets(tmp_path)
    kr = secrets_dir / "jwt_keys"
    kr.mkdir()
    priv, pub = cfg.generate_rsa_keys()
    (kr / "abc123.pem").write_text(priv)
    env = tmp_path / "kr.enc"
    _write_envelope(env, pub, b"KR-SNAPSHOT")
    data, desc = decrypt_snapshot_bytes(env.read_bytes(), secrets_dir=secrets_dir)
    assert data == b"KR-SNAPSHOT"
    assert desc == "jwt_keyring:abc123.pem"


def test_decrypt_garbage_raises(tmp_path):
    secrets_dir = _make_secrets(tmp_path)
    with pytest.raises(SnapshotDecryptError):
        decrypt_snapshot_bytes(b"not-a-snapshot", secrets_dir=secrets_dir)


def test_candidate_order(tmp_path):
    secrets_dir = _make_secrets(tmp_path)
    kr = secrets_dir / "jwt_keys"
    kr.mkdir()
    p1, _ = cfg.generate_rsa_keys()
    (kr / "zzz.pem").write_text(p1)
    names = [d for d, _ in list_candidate_private_keys(secrets_dir)]
    assert names == ["backup", "jwt_keyring:zzz.pem", "jwt-legacy"]


def test_snapshot_engine_encrypt_roundtrip(tmp_path, monkeypatch):
    """_encrypt_file du snapshot_engine chiffre désormais avec la clé backup."""
    import app.snapshot_engine as se
    secrets_dir = _make_secrets(tmp_path)
    monkeypatch.setattr(se, "SECRETS_DIR", secrets_dir)
    plain = tmp_path / "in.bin"
    plain.write_bytes(b"ENGINE" * 50)
    out = tmp_path / "out.enc"
    se._encrypt_file(plain, out)
    data, desc = decrypt_snapshot_bytes(out.read_bytes(), secrets_dir=secrets_dir)
    assert data == plain.read_bytes()
    assert desc == "backup"
