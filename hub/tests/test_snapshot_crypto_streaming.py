# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Restore snapshot en streaming — incidents PRA VM 13/11/2026.

Deux blocages découverts au test PRA sur VM vierge (snapshot réel 3,5 Go) :
1. `decrypt_snapshot_bytes` one-shot : le backend openssl limite une longueur
   de buffer à int32 (~2,1 Go) → panique `TryFromIntError(PosOverflow)`.
2. `restore_snapshot.py` charge tout en RAM (read_bytes x2 + decompress) →
   OOM memcg (2g) puis OOM global VM.

Le fix : `decrypt_snapshot_to_file` (chunks, symétrique de `encrypt_file`)
et une décompression zstd streamée dans restore_snapshot.py.
"""
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.snapshot_crypto import (
    _ENCRYPT_CHUNK,
    SnapshotDecryptError,
    decrypt_snapshot_bytes,
    decrypt_snapshot_to_file,
    encrypt_file,
)
from app import restore_snapshot as rs


def _make_tar_with_absolute_link(tar_path: Path, tmp_path: Path) -> None:
    """Archive de test : un fichier + un symlink à cible absolue
    (motif réel : APPS/<slug>/socle -> chemin absolu HUBsocle, 17 apps)."""
    import io
    import tarfile

    with tarfile.open(tar_path, "w:") as tar:
        data = b"hello"
        info = tarfile.TarInfo("app/fichier.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("app/socle")
        link.type = tarfile.SYMTYPE
        link.linkname = "/opt/hub/HUBsocle"
        tar.addfile(link)


def test_extract_tar_archive_tolere_symlink_absolu(tmp_path):
    """F4 (PRA VM 13/11/2026) : l'extraction ne doit pas échouer sur les
    symlinks à cible absolue embarqués dans apps_code.tar — archive de
    confiance produite par notre snapshot engine ; le lien est préservé."""
    tar_path = tmp_path / "apps_code.tar"
    _make_tar_with_absolute_link(tar_path, tmp_path)

    dest = tmp_path / "extract"
    dest.mkdir()
    rs._extract_tar_archive(tar_path, dest)

    assert (dest / "app" / "fichier.txt").read_bytes() == b"hello"
    assert os.readlink(dest / "app" / "socle") == "/opt/hub/HUBsocle"


def _make_secrets_dir(tmp_path: Path) -> Path:
    """secrets_dir de test avec la paire backup (RSA PEM)."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    priv, pub = _gen_rsa_pair()
    (secrets_dir / "backup_private_key.pem").write_text(priv)
    (secrets_dir / "backup_public_key.pem").write_text(pub)
    return secrets_dir


def _gen_rsa_pair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub


def test_decrypt_snapshot_to_file_roundtrip_multi_chunks(tmp_path):
    """> 3 chunks de chiffrement : le déchiffrement streamé restitue
    byte-identique le payload (et ne charge pas tout d'un bloc)."""
    secrets_dir = _make_secrets_dir(tmp_path)
    payload = os.urandom(3 * _ENCRYPT_CHUNK + 1234)  # 3 chunks pleins + reste

    src = tmp_path / "payload.bin"
    src.write_bytes(payload)
    enc = tmp_path / "snapshot.tar.zst.enc"
    encrypt_file(src, enc, secrets_dir=secrets_dir)

    out = tmp_path / "snapshot.tar.zst"
    key_desc = decrypt_snapshot_to_file(enc, out, secrets_dir=secrets_dir)

    assert key_desc == "backup"
    assert out.read_bytes() == payload


def test_decrypt_snapshot_to_file_sans_cle_leve_snapshotdecrypterror(tmp_path):
    """Aucune clé candidate : SnapshotDecryptError, pas de fichier de sortie."""
    secrets_dir = _make_secrets_dir(tmp_path)
    payload = b"x" * 1024
    src = tmp_path / "payload.bin"
    src.write_bytes(payload)
    enc = tmp_path / "snapshot.tar.zst.enc"
    encrypt_file(src, enc, secrets_dir=secrets_dir)

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    out = tmp_path / "snapshot.tar.zst"
    with pytest.raises(SnapshotDecryptError):
        decrypt_snapshot_to_file(enc, out, secrets_dir=empty_dir)
    assert not out.exists()


def test_decrypt_snapshot_to_file_corruption_leve_erreur(tmp_path):
    """Ciphertext altéré : le tag GCM invalide lève SnapshotDecryptError
    (et pas une InvalidTag brute du backend)."""
    secrets_dir = _make_secrets_dir(tmp_path)
    payload = os.urandom(2 * _ENCRYPT_CHUNK)
    src = tmp_path / "payload.bin"
    src.write_bytes(payload)
    enc = tmp_path / "snapshot.tar.zst.enc"
    encrypt_file(src, enc, secrets_dir=secrets_dir)

    corrupted = tmp_path / "corrupted.enc"
    raw = bytearray(enc.read_bytes())
    raw[len(raw) // 2] ^= 0xFF  # flip un octet au milieu du ciphertext
    corrupted.write_bytes(bytes(raw))

    out = tmp_path / "snapshot.tar.zst"
    with pytest.raises(SnapshotDecryptError):
        decrypt_snapshot_to_file(corrupted, out, secrets_dir=secrets_dir)


def test_decrypt_snapshot_to_file_repli_keyring_multi_candidates(tmp_path):
    """Chiffré avec une clé du trousseau jwt_keys/ (2e candidate) : la boucle
    candidates trouve la bonne clé et le roundtrip reste byte-identique."""
    secrets_dir = _make_secrets_dir(tmp_path)  # candidate 1 : "backup"
    keyring = secrets_dir / "jwt_keys"
    keyring.mkdir()
    priv2, pub2 = _gen_rsa_pair()
    (keyring / "2026-b.pem").write_text(priv2)  # candidate 2 (triée)

    payload = os.urandom(_ENCRYPT_CHUNK + 77)
    src = tmp_path / "payload.bin"
    src.write_bytes(payload)
    enc = tmp_path / "snapshot.tar.zst.enc"
    # Chiffrement fait avec la candidate 2 : secrets_dir éphémère ne contenant
    # qu'elle (encrypt_file prend la 1re clé publique backup, on chiffre donc
    # avec un secrets_dir dédié puis on déchiffre avec le secrets_dir complet).
    tmp_secrets = tmp_path / "enc_secrets"
    tmp_secrets.mkdir()
    (tmp_secrets / "backup_private_key.pem").write_text(priv2)
    (tmp_secrets / "backup_public_key.pem").write_text(pub2)
    encrypt_file(src, enc, secrets_dir=tmp_secrets)

    out = tmp_path / "snapshot.tar.zst"
    key_desc = decrypt_snapshot_to_file(enc, out, secrets_dir=secrets_dir)
    assert key_desc.startswith("jwt_keyring:")
    assert out.read_bytes() == payload


def test_extract_apps_code_tolere_symlink_absolu(tmp_path, monkeypatch):
    """F4 suite : l'extraction dédiée d'apps_code.tar (filter 'data' explicite
    avant le fix) levait AbsoluteLinkError sur les symlinks socle absolus."""
    extract_dir = tmp_path / "extract"
    tar_path = extract_dir / "apps" / "apps_code.tar"
    tar_path.parent.mkdir(parents=True)
    _make_tar_with_absolute_link(tar_path, tmp_path)
    apps_source = tmp_path / "apps_source"
    apps_source.mkdir()
    monkeypatch.setattr(rs, "APPS_SOURCE_DIR", apps_source)

    assert rs._extract_apps_code(extract_dir) is True
    assert (apps_source / "app" / "fichier.txt").read_bytes() == b"hello"
    assert os.readlink(apps_source / "app" / "socle") == "/opt/hub/HUBsocle"


def test_decrypt_snapshot_bytes_petit_fichier_compat(tmp_path):
    """Non-régression : decrypt_snapshot_bytes (one-shot) reste compatible
    avec le format produit par encrypt_file pour un petit payload."""
    secrets_dir = _make_secrets_dir(tmp_path)
    payload = os.urandom(2048)
    src = tmp_path / "payload.bin"
    src.write_bytes(payload)
    enc = tmp_path / "snapshot.tar.zst.enc"
    encrypt_file(src, enc, secrets_dir=secrets_dir)

    plaintext, key_desc = decrypt_snapshot_bytes(enc.read_bytes(), secrets_dir=secrets_dir)
    assert plaintext == payload
    assert key_desc == "backup"


def test_extract_zst_tar_streamed_roundtrip(tmp_path):
    """Pipeline décompression zstd → extraction tar en streaming : aucun
    tar intermédiaire sur disque ni en RAM (ENOSPC incident PRA VM
    13/11/2026), symlink absolu préservé (F4)."""
    import tarfile
    import zstandard as zstd

    tar_path = tmp_path / "payload.tar"
    _make_tar_with_absolute_link(tar_path, tmp_path)
    zst = tmp_path / "payload.tar.zst"
    with open(tar_path, "rb") as fin, open(zst, "wb") as fout:
        zstd.ZstdCompressor().copy_stream(fin, fout)

    dest = tmp_path / "extract"
    dest.mkdir()
    rs._extract_zst_tar_streamed(zst, dest)

    assert (dest / "app" / "fichier.txt").read_bytes() == b"hello"
    assert os.readlink(dest / "app" / "socle") == "/opt/hub/HUBsocle"
    assert not (dest / "payload.tar").exists()


def _bytes_io(data: bytes):
    import io

    return io.BytesIO(data)
