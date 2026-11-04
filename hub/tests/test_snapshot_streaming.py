# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Régression OOM du 29/10/2026 — le pipeline d'assemblage des snapshots doit
travailler en STREAMING (compression zstd, chiffrement AES-GCM, checksum).

Contexte : hub-api tourne avec mem_limit=2 Go. L'ancien pipeline chargeait le
payload entier en RAM à trois endroits :
  1. ``cctx.compress(src.read())``     (snapshot_engine, assemblage tar→zst)
  2. ``AESGCM.encrypt(nonce, input_path.read_bytes(), None)`` (snapshot_crypto)
  3. ``hashlib.sha256(encrypted_path.read_bytes())``          (checksum)
Un snapshot de 26 apps ≈ 4,4 Go →  OOM kill du container par le kernel
(dmesg : « Memory cgroup out of memory: Killed process uvicorn, anon-rss ~1,9 Go »)
→ redémarrage du Hub → snapshot marqué « interrompu ».

Ici on reproduit la contrainte mémoire avec ``RLIMIT_AS`` dans un subprocess :
tout code qui charge le payload (256 Mo) d'un bloc explose sous la limite
(160 Mo), le code en streaming (chunks de 4 Mo) passe.

Compatibilité : le format de l'enveloppe chiffrée et le frame zstd doivent
rester BYTE-COMPATIBLE avec l'existant (``decrypt_snapshot_bytes`` one-shot
et le ``dctx.decompress()`` one-shot de restore_snapshot.py exigent l'en-tête
content-size du frame zstd).
"""

import hashlib
import subprocess
import sys
import textwrap
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parents[1]

PAYLOAD_MB = 256
RLIMIT_AS_MB = 160

CHILD = textwrap.dedent(
    """
    import hashlib
    import resource
    import sys
    from pathlib import Path

    MB = 1024 * 1024
    limit = int(sys.argv[1]) * MB
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    mode, src, dst = sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])

    if mode == "compress":
        from app.snapshot_engine import _assemble_and_compress
        size = _assemble_and_compress(src.parent, dst)
        assert size > 0, "taille d'archive invalide"
    elif mode == "encrypt":
        from app.snapshot_crypto import encrypt_file
        encrypt_file(src, dst, secrets_dir=Path(sys.argv[5]))
    elif mode == "sha256":
        from app.snapshot_engine import _sha256_file
        digest = _sha256_file(src)
        assert len(digest) == 64
    else:
        raise SystemExit(f"mode inconnu: {mode}")
    print("OK")
    """
)


def _run_child(mode: str, src: Path, dst: Path, secrets_dir: Path | None = None) -> None:
    """Exécute le pipeline visé dans un subprocess limité en mémoire (RLIMIT_AS)."""
    argv = [sys.executable, "-c", CHILD, str(RLIMIT_AS_MB), mode, str(src), str(dst)]
    if secrets_dir is not None:
        argv.append(str(secrets_dir))
    proc = subprocess.run(
        argv, cwd=str(HUB_ROOT), capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, (
        f"Le pipeline '{mode}' échoue sous RLIMIT_AS={RLIMIT_AS_MB} Mo "
        f"(payload {PAYLOAD_MB} Mo) — le payload est-il encore chargé d'un bloc ?\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr[-2000:]}"
    )
    assert "OK" in proc.stdout


def _make_payload(tmp_path: Path, name: str = "big.bin") -> Path:
    """Payload de PAYLOAD_MB Mo (compressible : zéros) dans un dossier source."""
    src_dir = tmp_path / "payload"
    src_dir.mkdir()
    payload = src_dir / name
    block = b"\x00" * (8 * 1024 * 1024)
    with open(payload, "wb") as f:
        for _ in range(PAYLOAD_MB // 8):
            f.write(block)
    return src_dir


def _make_secrets(tmp_path: Path) -> Path:
    """Trousseau de test (paire backup RSA) — même convention que test_snapshot_crypto."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (secrets_dir / "backup_private_key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (secrets_dir / "backup_public_key.pem").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return secrets_dir


def test_encrypt_file_streaming_under_memory_limit(tmp_path):
    """encrypt_file doit chiffrer un gros fichier sans le charger en RAM.

    Avant fix : MemoryError (read_bytes() du payload entier) — c'est l'étape
    qui OOM-killait le container en prod.
    """
    src_dir = _make_payload(tmp_path)
    secrets_dir = _make_secrets(tmp_path)
    out = tmp_path / "big.bin.enc"
    _run_child("encrypt", src_dir / "big.bin", out, secrets_dir=secrets_dir)

    # L'enveloppe doit rester lisible par le déchiffreur one-shot existant.
    from app.snapshot_crypto import decrypt_snapshot_bytes

    plain, desc = decrypt_snapshot_bytes(out.read_bytes(), secrets_dir=secrets_dir)
    assert desc == "backup"
    assert hashlib.sha256(plain).hexdigest() == hashlib.sha256(
        (src_dir / "big.bin").read_bytes()
    ).hexdigest()


def test_assemble_and_compress_streaming_under_memory_limit(tmp_path):
    """L'assemblage tar + compression zstd doit être streaming (chunké)."""
    src_dir = _make_payload(tmp_path)
    archive = tmp_path / "snapshot_test.tar.zst"
    _run_child("compress", src_dir, archive)

    # Compat restore : frame zstd avec content-size ⇒ décompression one-shot OK
    # (restore_snapshot.py fait dctx.decompress(data) sans max_output_size).
    import tarfile

    import zstandard as zstd

    tar_bytes = zstd.ZstdDecompressor().decompress(archive.read_bytes())
    with tarfile.open(fileobj=__import__("io").BytesIO(tar_bytes), mode="r") as tar:
        names = tar.getnames()
    assert any(n.endswith("big.bin") for n in names), f"tar incomplet: {names}"


def test_sha256_file_streaming_under_memory_limit(tmp_path):
    """Le checksum doit être calculé par chunks, pas via read_bytes()."""
    src_dir = _make_payload(tmp_path)
    _run_child("sha256", src_dir / "big.bin", tmp_path / "unused")
