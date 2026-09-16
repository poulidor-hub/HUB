"""Trousseau de clés JWT multi-kid du HUB (spec 2026-09-16 §5).

Source de vérité : `secrets/jwt_keys/` — un PEM par clé + `manifest.json`.
Le statut « archived » est dérivé à la lecture (retiring dont l'overlap est
dépassé) : aucun scheduler, comportement déterministe après PRA.

Invariants du manifest (validés au chargement, fail-closed) :
  - exactement une clé `active` ;
  - `kid` uniques ;
  - bijection manifest ↔ fichiers `<kid>.pem`.

Ce module ne dépend PAS de la DB. Imports de `app.config` paresseux
(fonctions) pour éviter tout cycle.
"""
import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger(__name__)

KEYS_DIRNAME = "jwt_keys"
MANIFEST_NAME = "manifest.json"
LEGACY_KID = "hub-jwt-key-1"

_overlap_hours = int(os.environ.get("JWT_KEY_OVERLAP_HOURS", "24"))
_KEYRING_DIR: Path | None = None  # injectable pour les tests

_lock = threading.Lock()
_entries: list["KeyEntry"] | None = None  # cache process


class KeyringError(Exception):
    """Manifest absent/invalide, invariant violé, ou opération illégale."""


def _now() -> datetime:
    from app.models import utcnow
    return utcnow()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _overlap() -> timedelta:
    return timedelta(hours=_overlap_hours)


@dataclass
class KeyEntry:
    kid: str
    status: str  # active | retiring | archived | revoked
    created_at: str
    activated_at: str | None = None
    retired_at: str | None = None

    def effective_status(self, now: datetime | None = None) -> str:
        now = now or _now()
        if self.status == "retiring":
            retired = _parse(self.retired_at)
            if retired and now >= retired + _overlap():
                return "archived"
        return self.status

    def is_published(self, now: datetime | None = None) -> bool:
        """Publié au JWKS et accepté en vérification (active ou retiring)."""
        return self.effective_status(now) in ("active", "retiring")

    def to_manifest(self) -> dict:
        return asdict(self)


def _resolve_keyring_dir() -> Path:
    if _KEYRING_DIR is not None:
        return _KEYRING_DIR
    from app.config import _resolve_secrets_dir
    return _resolve_secrets_dir() / KEYS_DIRNAME


def kid_thumbprint(public_pem: str) -> str:
    """RFC 7638 : SHA-256 du JWK canonique {"e","kty","n"} — hex 8 caractères."""
    import base64
    import hashlib
    public_key = serialization.load_pem_public_key(public_pem.encode())
    numbers = public_key.public_numbers()

    def int_to_b64url(value: int) -> str:
        length = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")

    canonical = '{"e":"%s","kty":"RSA","n":"%s"}' % (
        int_to_b64url(numbers.e), int_to_b64url(numbers.n))
    return hashlib.sha256(canonical.encode()).hexdigest()[:8]


def _private_to_public_pem(private_pem: str) -> str:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def reload() -> None:
    """Purge le cache process (après mutation externe ou en test)."""
    global _entries
    _entries = None


def _validate(entries: list[KeyEntry]) -> None:
    kids = [e.kid for e in entries]
    if len(kids) != len(set(kids)):
        raise KeyringError("kid dupliqué dans le manifest du trousseau")
    if sum(1 for e in entries if e.status == "active") != 1:
        raise KeyringError("Le trousseau doit contenir exactement une clé active")
    keyring_dir = _resolve_keyring_dir()
    for e in entries:
        if not (keyring_dir / f"{e.kid}.pem").exists():
            raise KeyringError(f"PEM manquant pour {e.kid}")


def _load_entries() -> list[KeyEntry]:
    global _entries
    if _entries is not None:
        return _entries
    manifest_path = _resolve_keyring_dir() / MANIFEST_NAME
    if not manifest_path.exists():
        raise KeyringError(f"Manifest du trousseau absent : {manifest_path}")
    try:
        raw = json.loads(manifest_path.read_text())
        entries = [KeyEntry(**item) for item in raw]
    except Exception as exc:
        raise KeyringError(f"Manifest du trousseau invalide : {exc}") from exc
    _validate(entries)
    _entries = entries
    return entries


def _save_entries(entries: list[KeyEntry]) -> None:
    """Écriture atomique du manifest (tmp + replace). Pas de validation ici :
    les tests manipulent directement les entries ; la validation s'applique au chargement."""
    keyring_dir = _resolve_keyring_dir()
    keyring_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = keyring_dir / MANIFEST_NAME
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps([e.to_manifest() for e in entries], indent=2))
    tmp.replace(manifest_path)
    global _entries
    _entries = entries


def ensure_seeded() -> None:
    """Seed idempotent au premier accès :
    1. manifest présent → validation + retour ;
    2. PEMs présents sans manifest (restauration partielle) → adoption,
       le plus récent (mtime) devient active, les autres archived ;
    3. paire legacy `secrets/jwt_private_key.pem` → import sous `hub-jwt-key-1` ;
    4. DEV uniquement (`AUTO_GENERATE_KEYS=true`) → génération ;
    5. sinon → KeyringError (fail-closed)."""
    keyring_dir = _resolve_keyring_dir()
    if (keyring_dir / MANIFEST_NAME).exists():
        _load_entries()
        return
    keyring_dir.mkdir(parents=True, exist_ok=True)
    now = _now()

    existing: list[KeyEntry] = []
    for pem in sorted(keyring_dir.glob("*.pem")):
        kid = pem.stem
        try:
            private_pem = pem.read_text().strip()
            derived = kid_thumbprint(_private_to_public_pem(private_pem))
        except Exception:
            logger.warning("PEM orphelin illisible ignoré : %s", pem.name)
            continue
        if derived != kid and kid != LEGACY_KID:
            logger.warning("PEM orphelin ignoré (kid %s : thumbprint divergent)", kid)
            continue
        existing.append(KeyEntry(kid=kid, status="active", created_at=_iso(now),
                                 activated_at=_iso(now)))
        pem.chmod(0o600)
    if existing:
        existing.sort(key=lambda e: (keyring_dir / f"{e.kid}.pem").stat().st_mtime,
                      reverse=True)
        for entry in existing[1:]:
            entry.status = "archived"
        _save_entries(existing)
        logger.info("🔐 Trousseau JWT reconstruit depuis %d PEM(s)", len(existing))
        return

    from app.config import _resolve_secrets_dir, generate_rsa_keys
    legacy_priv = _resolve_secrets_dir() / "jwt_private_key.pem"
    if legacy_priv.exists():
        private_pem = legacy_priv.read_text().strip()
        kid = LEGACY_KID
    elif os.environ.get("AUTO_GENERATE_KEYS") == "true":
        private_pem, _ = generate_rsa_keys()
        kid = kid_thumbprint(_private_to_public_pem(private_pem))
        logger.warning("AUTO_GENERATE_KEYS : clé JWT initiale générée (DEV uniquement)")
    else:
        raise KeyringError(
            "Aucune clé JWT trouvée (secrets/jwt_private_key.pem absent). Restaurez "
            "secrets/ depuis le coffre ou définissez AUTO_GENERATE_KEYS=true (DEV)."
        )
    pem_path = keyring_dir / f"{kid}.pem"
    pem_path.write_text(private_pem)
    pem_path.chmod(0o600)
    _save_entries([KeyEntry(kid=kid, status="active", created_at=_iso(now),
                            activated_at=_iso(now))])
    logger.info("🔐 Trousseau JWT seedé : %s (kid %s)", pem_path, kid)


def get_active(now: datetime | None = None) -> KeyEntry:
    now = now or _now()
    for entry in _load_entries():
        if entry.status == "active":
            return entry
    raise KeyringError("Aucune clé active dans le trousseau")


def get_active_kid() -> str:
    return get_active().kid


def _read_private_pem(kid: str) -> str:
    path = _resolve_keyring_dir() / f"{kid}.pem"
    if not path.exists():
        raise KeyringError(f"PEM manquant pour {kid}")
    return path.read_text().strip()


def get_active_private_pem() -> str:
    return _read_private_pem(get_active().kid)


def get_active_pem_pair() -> tuple[str, str]:
    private_pem = get_active_private_pem()
    return private_pem, _private_to_public_pem(private_pem)


def get_public_pem(kid: str) -> str | None:
    try:
        return _private_to_public_pem(_read_private_pem(kid))
    except KeyringError:
        return None


def get_for_verification(kid: str | None, now: datetime | None = None) -> KeyEntry | None:
    """Clé validable : active, ou retiring dans la fenêtre d'overlap.
    kid=None → clé active. None si kid inconnu ou statut hors fenêtre."""
    now = now or _now()
    if kid is None:
        try:
            return get_active(now)
        except KeyringError:
            return None
    for entry in _load_entries():
        if entry.kid == kid and entry.effective_status(now) in ("active", "retiring"):
            return entry
    return None


def build_jwks(now: datetime | None = None) -> dict:
    """JWKS OIDC : publie les clés active + retiring (fenêtre d'overlap)."""
    import base64
    now = now or _now()

    def int_to_b64url(value: int) -> str:
        length = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")

    keys = []
    for entry in _load_entries():
        if not entry.is_published(now):
            continue
        public_key = serialization.load_pem_public_key(
            _private_to_public_pem(_read_private_pem(entry.kid)).encode())
        numbers = public_key.public_numbers()
        keys.append({
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": entry.kid,
            "n": int_to_b64url(numbers.n),
            "e": int_to_b64url(numbers.e),
        })
    return {"keys": keys}


def select_verification_key(token: str, now: datetime | None = None) -> tuple[KeyEntry | None, str | None]:
    """(entry, public_pem) selon le kid du header JWT ; (None, None) si invalide."""
    try:
        header = pyjwt.get_unverified_header(token)
    except pyjwt.PyJWTError:
        return None, None
    entry = get_for_verification(header.get("kid"), now)
    if entry is None:
        return None, None
    return entry, _private_to_public_pem(_read_private_pem(entry.kid))
