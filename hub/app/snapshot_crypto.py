# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Chiffrement/déchiffrement des snapshots HUB.

Phase 1 « découplage » (spec 2026-09-16 §4) : les snapshots sont chiffrés
avec la paire dédiée `backup_public_key.pem` — et plus avec la clé de
signature JWT. Le déchiffrement essaie la clé backup, puis les PEM du
trousseau `jwt_keys/`, puis la clé JWT legacy : les snapshots d'avant
découplage restent déchiffrables à vie.

Format d'enveloppe INCHANGÉ (compatibilité snapshots existants) :
  key_len (4 octets BE) + enc_key (RSA-OAEP-SHA256) + nonce (12) + ciphertext (AES-256-GCM)

Ce module ne dépend PAS de la DB ni du reste de l'app : il est utilisé par
restore_snapshot.py avant le démarrage de l'API.
"""
import logging
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

# Chunks de chiffrement : les snapshots multi-Go ne transitent JAMAIS entiers
# en RAM (OOM kill du container hub-api, memcg 2 Go — incident 29/10/2026).
_ENCRYPT_CHUNK = 4 * 1024 * 1024

_SECRETS_DIR: Path | None = None  # injectable pour les tests


class SnapshotDecryptError(Exception):
    """Aucune clé candidate n'a pu déchiffrer le snapshot."""


def _resolve_secrets_dir(secrets_dir: Path | None = None) -> Path:
    if secrets_dir is not None:
        return secrets_dir
    if _SECRETS_DIR is not None:
        return _SECRETS_DIR
    if Path("/code").exists():
        return Path("/code/secrets")
    return Path(__file__).resolve().parent.parent.parent / "secrets"


def list_candidate_private_keys(secrets_dir: Path | None = None) -> list[tuple[str, Path]]:
    """Clés privées candidates au déchiffrement, dans l'ordre d'essai."""
    base = _resolve_secrets_dir(secrets_dir)
    candidates: list[tuple[str, Path]] = []
    backup_key = base / "backup_private_key.pem"
    if backup_key.exists():
        candidates.append(("backup", backup_key))
    keyring_dir = base / "jwt_keys"
    if keyring_dir.is_dir():
        for pem in sorted(keyring_dir.glob("*.pem")):
            candidates.append((f"jwt_keyring:{pem.name}", pem))
    legacy = base / "jwt_private_key.pem"
    if legacy.exists():
        candidates.append(("jwt-legacy", legacy))
    return candidates


def encrypt_file(input_path: Path, output_path: Path, *, secrets_dir: Path | None = None) -> None:
    """Chiffre `input_path` vers `output_path` avec la clé publique backup.

    Streaming (chunks de 4 Mo) : le plaintext et le ciphertext ne sont jamais
    chargés entièrement en RAM. La sortie est BYTE-IDENTIQUE à l'ancienne
    implémentation one-shot (``AESGCM.encrypt`` = ciphertext‖tag 16 o) :
    ``decrypt_snapshot_bytes`` et restore_snapshot.py restent compatibles sans
    migration de format.
    """
    base = _resolve_secrets_dir(secrets_dir)
    with open(base / "backup_public_key.pem", "rb") as f:
        public_key = serialization.load_pem_public_key(f.read())

    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None),
    )

    encryptor = Cipher(algorithms.AES(aes_key), modes.GCM(nonce)).encryptor()
    buffer = bytearray(_ENCRYPT_CHUNK + 16)
    with open(input_path, "rb") as src, open(output_path, "wb") as dst:
        dst.write(len(encrypted_key).to_bytes(4, "big"))
        dst.write(encrypted_key)
        dst.write(nonce)
        while chunk := src.read(_ENCRYPT_CHUNK):
            written = encryptor.update_into(chunk, buffer)
            dst.write(buffer[:written])
        encryptor.finalize()
        dst.write(encryptor.tag)


def decrypt_snapshot_bytes(data: bytes, *, secrets_dir: Path | None = None) -> tuple[bytes, str]:
    """Déchiffre une enveloppe snapshot ; retourne (plaintext, description de la clé)."""
    if len(data) < 4 + 12:
        raise SnapshotDecryptError("Enveloppe trop courte")
    key_len = int.from_bytes(data[:4], "big")
    enc_key = data[4:4 + key_len]
    nonce = data[4 + key_len:4 + key_len + 12]
    ciphertext = data[4 + key_len + 12:]

    for desc, pem_path in list_candidate_private_keys(secrets_dir):
        try:
            with open(pem_path, "rb") as f:
                priv_key = serialization.load_pem_private_key(f.read(), password=None)
            aes_key = priv_key.decrypt(
                enc_key,
                padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                             algorithm=hashes.SHA256(), label=None),
            )
            plaintext = AESGCM(aes_key).decrypt(nonce, ciphertext, None)
            if desc == "backup":
                logger.info("Snapshot déchiffré avec la clé backup")
            else:
                logger.warning("Snapshot déchiffré avec la clé %s (repli legacy)", desc)
            return plaintext, desc
        except Exception:  # noqa: BLE001 — mauvaise clé candidate, on essaie la suivante
            continue
    raise SnapshotDecryptError(
        "Aucune clé candidate (backup, trousseau jwt_keys/, jwt legacy) "
        "n'a pu déchiffrer ce snapshot"
    )


def decrypt_snapshot_to_file(src_path: Path, dst_path: Path, *, secrets_dir: Path | None = None) -> str:
    """Déchiffre une enveloppe snapshot vers `dst_path`, en streaming.

    Symétrique de `encrypt_file` (chunks de 4 Mo) : ni le ciphertext ni le
    plaintext ne transitent entiers en RAM. Deux incidents PRA (VM 13/11/2026,
    snapshot réel 3,5 Go) rendaient l'ancien chemin one-shot inutilisable :
    - `AESGCM.decrypt` dépasse la limite int32 du backend openssl
      (`TryFromIntError(PosOverflow)`) au-delà de ~2,1 Go ;
    - `restore_snapshot.py` chargeait en plus tout en mémoire → OOM memcg.

    Le format reste byte-compatible avec `encrypt_file` :
    `[4B key_len][enc_key][12B nonce][ciphertext…][16B tag]`.
    Retourne la description de la clé utilisée (comme `decrypt_snapshot_bytes`).
    """
    src_path, dst_path = Path(src_path), Path(dst_path)
    candidates = list_candidate_private_keys(secrets_dir)
    if not candidates:
        raise SnapshotDecryptError(
            "Aucune clé candidate (backup, trousseau jwt_keys/, jwt legacy) "
            "disponible pour déchiffrer ce snapshot"
        )
    if src_path.stat().st_size < 4 + 12 + 16:
        raise SnapshotDecryptError("Enveloppe trop courte")

    key_desc: str | None = None
    aes_key: bytes | None = None
    nonce: bytes = b""
    header_end = 0
    with open(src_path, "rb") as src:
        for desc, pem_path in candidates:
            src.seek(0)
            key_len = int.from_bytes(src.read(4), "big")
            enc_key = src.read(key_len)
            nonce = src.read(12)
            header_end = src.tell()
            try:
                with open(pem_path, "rb") as f:
                    priv_key = serialization.load_pem_private_key(f.read(), password=None)
                aes_key = priv_key.decrypt(
                    enc_key,
                    padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                                 algorithm=hashes.SHA256(), label=None),
                )
            except Exception:  # noqa: BLE001 — mauvaise clé candidate, on essaie la suivante
                continue
            key_desc = desc
            break

    if aes_key is None or key_desc is None:
        raise SnapshotDecryptError(
            "Aucune clé candidate (backup, trousseau jwt_keys/, jwt legacy) "
            "n'a pu déchiffrer ce snapshot"
        )

    total = src_path.stat().st_size
    if total < header_end + 16:
        raise SnapshotDecryptError("Enveloppe trop courte (tag manquant)")
    tag_offset = total - 16
    ciphertext_len = tag_offset - header_end

    decryptor = Cipher(algorithms.AES(aes_key), modes.GCM(nonce)).decryptor()
    buffer = bytearray(_ENCRYPT_CHUNK + 16)
    with open(src_path, "rb") as src, open(dst_path, "wb") as dst:
        src.seek(header_end)
        remaining = ciphertext_len
        while remaining > 0:
            chunk = src.read(min(_ENCRYPT_CHUNK, remaining))
            if not chunk:
                raise SnapshotDecryptError("Snapshot tronqué (ciphertext incomplet)")
            written = decryptor.update_into(chunk, buffer)
            dst.write(buffer[:written])
            remaining -= len(chunk)
        src.seek(tag_offset)
        tag = src.read(16)
        try:
            decryptor.finalize_with_tag(tag)
        except InvalidTag as exc:
            raise SnapshotDecryptError(
                "Tag GCM invalide — snapshot corrompu ou clé inadaptée"
            ) from exc

    if key_desc == "backup":
        logger.info("Snapshot déchiffré (streaming) avec la clé backup")
    else:
        logger.warning("Snapshot déchiffré (streaming) avec la clé %s (repli legacy)", key_desc)
    return key_desc
