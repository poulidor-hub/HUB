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

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

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
    """Chiffre `input_path` vers `output_path` avec la clé publique backup."""
    base = _resolve_secrets_dir(secrets_dir)
    with open(base / "backup_public_key.pem", "rb") as f:
        public_key = serialization.load_pem_public_key(f.read())

    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(nonce, input_path.read_bytes(), None)
    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None),
    )
    output_path.write_bytes(
        len(encrypted_key).to_bytes(4, "big") + encrypted_key + nonce + ciphertext
    )


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
