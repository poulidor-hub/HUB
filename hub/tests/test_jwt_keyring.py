"""Tests du trousseau JWT multi-kid (spec 2026-09-16 §5)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi import HTTPException

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


def _setup_post_rotation(isolated_keyring):
    """Trousseau à 2 clés : nouvelle active + `hub-jwt-key-1` retiring à NOW0."""
    from app.jwt_keyring import KeyEntry
    kr = isolated_keyring
    kr.ensure_seeded()
    priv, pub = cfg.generate_rsa_keys()
    new_kid = kr.kid_thumbprint(pub)
    pem_path = kr._resolve_keyring_dir() / f"{new_kid}.pem"
    pem_path.write_text(priv)
    pem_path.chmod(0o600)
    entries = kr._load_entries()
    entries[0].status = "retiring"
    entries[0].retired_at = NOW0.isoformat()
    entries.append(KeyEntry(kid=new_kid, status="active", created_at=NOW0.isoformat(),
                            activated_at=NOW0.isoformat()))
    kr._save_entries(entries)
    return new_kid


def test_kid_thumbprint_is_stable(isolated_keyring):
    _, pub = cfg.generate_rsa_keys()
    assert isolated_keyring.kid_thumbprint(pub) == isolated_keyring.kid_thumbprint(pub)
    assert len(isolated_keyring.kid_thumbprint(pub)) == 8


def test_retiring_within_overlap_is_published(isolated_keyring, monkeypatch):
    isolated_keyring.ensure_seeded()
    new_kid = _setup_post_rotation(isolated_keyring)
    frozen = NOW0 + timedelta(hours=2)
    monkeypatch.setattr(isolated_keyring, "_now", lambda: frozen)
    jwks = isolated_keyring.build_jwks()
    assert {k["kid"] for k in jwks["keys"]} == {new_kid, isolated_keyring.LEGACY_KID}
    assert set(jwks["keys"][0]) == {"kty", "use", "alg", "kid", "n", "e"}


def test_retiring_past_overlap_is_archived(isolated_keyring, monkeypatch):
    isolated_keyring.ensure_seeded()
    new_kid = _setup_post_rotation(isolated_keyring)
    frozen = NOW0 + timedelta(hours=25)  # overlap 24 h dépassé
    monkeypatch.setattr(isolated_keyring, "_now", lambda: frozen)
    entries = isolated_keyring._load_entries()
    legacy = next(e for e in entries if e.kid == isolated_keyring.LEGACY_KID)
    assert legacy.effective_status(frozen) == "archived"
    assert legacy.is_published(frozen) is False
    kids = {k["kid"] for k in isolated_keyring.build_jwks()["keys"]}
    assert kids == {new_kid}  # seule la clé active reste publiée
    assert isolated_keyring.get_for_verification(isolated_keyring.LEGACY_KID) is None


def test_get_for_verification_within_overlap(isolated_keyring, monkeypatch):
    isolated_keyring.ensure_seeded()
    new_kid = _setup_post_rotation(isolated_keyring)
    monkeypatch.setattr(isolated_keyring, "_now", lambda: NOW0 + timedelta(hours=2))
    assert isolated_keyring.get_for_verification(new_kid).kid == new_kid
    assert isolated_keyring.get_for_verification(isolated_keyring.LEGACY_KID) is not None
    assert isolated_keyring.get_for_verification("inconnu") is None


def test_select_verification_key(isolated_keyring, monkeypatch):
    isolated_keyring.ensure_seeded()
    active_pem = isolated_keyring.get_active_private_pem()
    kid = isolated_keyring.get_active_kid()
    good = pyjwt.encode({"sub": "u"}, active_pem, algorithm="RS256", headers={"kid": kid})
    entry, public_pem = isolated_keyring.select_verification_key(good)
    assert entry is not None and entry.kid == kid and "BEGIN" in public_pem
    bad_kid = pyjwt.encode({"sub": "u"}, active_pem, algorithm="RS256",
                           headers={"kid": "unknown"})
    assert isolated_keyring.select_verification_key(bad_kid) == (None, None)
    no_kid = pyjwt.encode({"sub": "u"}, active_pem, algorithm="RS256")
    entry, _ = isolated_keyring.select_verification_key(no_kid)
    assert entry is not None  # fallback clé active
    assert isolated_keyring.select_verification_key("garbage") == (None, None)


def test_rotate_creates_new_active_and_retires_old(isolated_keyring):
    kr = isolated_keyring
    kr.ensure_seeded()
    old_kid = kr.get_active_kid()
    result = kr.rotate()
    assert result["old_kid"] == old_kid
    assert result["new_kid"] != old_kid
    entries = {e.kid: e for e in kr._load_entries()}
    assert entries[result["new_kid"]].status == "active"
    assert entries[old_kid].status == "retiring"
    assert entries[old_kid].retired_at is not None
    assert kr.get_active_kid() == result["new_kid"]


def test_rotate_keeps_chain_and_double_rotate(isolated_keyring):
    kr = isolated_keyring
    kr.ensure_seeded()
    r1 = kr.rotate()
    r2 = kr.rotate()
    kids = [e.kid for e in kr._load_entries()]
    assert len(kids) == 3
    assert kr.get_active_kid() == r2["new_kid"]
    statuses = {e.kid: e.status for e in kr._load_entries()}
    assert statuses[r1["new_kid"]] == "retiring"
    assert statuses[r1["old_kid"]] == "retiring"


def test_revoke_retiring_key(isolated_keyring):
    kr = isolated_keyring
    kr.ensure_seeded()
    result = kr.rotate()
    kr.revoke(result["old_kid"])
    entry = next(e for e in kr._load_entries() if e.kid == result["old_kid"])
    assert entry.status == "revoked"
    assert kr.get_for_verification(result["old_kid"]) is None


def test_revoke_active_key_raises(isolated_keyring):
    kr = isolated_keyring
    kr.ensure_seeded()
    with pytest.raises(kr.KeyringError, match="rotater"):
        kr.revoke(kr.get_active_kid())


def test_revoke_unknown_and_idempotent(isolated_keyring):
    kr = isolated_keyring
    kr.ensure_seeded()
    with pytest.raises(kr.KeyringError, match="inconnu"):
        kr.revoke("nosuchkid")
    kr.rotate()
    old_kid = [e.kid for e in kr._load_entries() if e.status == "retiring"][0]
    kr.revoke(old_kid)
    kr.revoke(old_kid)  # déjà révoqué → no-op sans erreur


def test_cleanup_removes_old_archived_only(isolated_keyring, monkeypatch):
    kr = isolated_keyring
    kr.ensure_seeded()
    result = kr.rotate()
    old_kid = result["old_kid"]
    # vieillir artificiellement la clé retiring → archived + ancienne
    # (effective_status dérive « archived » de retired_at, pas de created_at :
    # il faut vieillir les deux et persister, cleanup() repart du disque)
    old_ts = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
    entries = kr._load_entries()
    old_entry = next(e for e in entries if e.kid == old_kid)
    old_entry.created_at = old_ts
    old_entry.retired_at = old_ts
    kr._save_entries(entries)
    monkeypatch.setattr(kr, "_now", lambda: datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc))
    removed = kr.cleanup(older_than_months=12)
    assert old_kid in removed
    kids = [e.kid for e in kr._load_entries()]
    assert old_kid not in kids
    assert not (kr._resolve_keyring_dir() / f"{old_kid}.pem").exists()
    assert kr.get_active_kid() == result["new_kid"]


@pytest.fixture
def admin_token_isolated(admin_user, isolated_keyring):
    """Token admin signé par le trousseau isolé (override du conftest)."""
    from app.auth import create_access_token
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    return token


async def test_jwks_endpoint_lists_active_key(client, isolated_keyring):
    isolated_keyring.ensure_seeded()
    resp = await client.get("/api/auth/.well-known/jwks.json")
    assert resp.status_code == 200
    kids = [k["kid"] for k in resp.json()["keys"]]
    assert kids == [isolated_keyring.LEGACY_KID]


async def test_access_token_kid_follows_active_key(client, isolated_keyring, admin_user):
    isolated_keyring.ensure_seeded()
    from app.auth import create_access_token
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    assert pyjwt.get_unverified_header(token)["kid"] == isolated_keyring.LEGACY_KID
    isolated_keyring.rotate()
    token2, _, _ = create_access_token(username=admin_user.username, apps={})
    new_kid = isolated_keyring.get_active_kid()
    assert pyjwt.get_unverified_header(token2)["kid"] == new_kid
    resp = await client.get("/api/auth/.well-known/jwks.json")
    kids = {k["kid"] for k in resp.json()["keys"]}
    assert kids == {new_kid, isolated_keyring.LEGACY_KID}


async def test_rotation_timeline(client, isolated_keyring, monkeypatch):
    """Token ancien-kid : valide pendant l'overlap, rejeté après (spec §7)."""
    isolated_keyring.ensure_seeded()
    from app.auth import create_access_token, verify_token
    token, _, _ = create_access_token(username="admin", apps={})
    result = isolated_keyring.rotate()

    # Dans l'overlap : l'ancien token reste valide
    assert verify_token(token)["sub"] == "admin"

    # JWKS publie les deux clés
    resp = await client.get("/api/auth/.well-known/jwks.json")
    assert len(resp.json()["keys"]) == 2

    # Après l'overlap : rejet 401
    overlap_end = datetime.fromisoformat(result["overlap_ends_at"])
    monkeypatch.setattr(isolated_keyring, "_now",
                        lambda: overlap_end + timedelta(minutes=1))
    with pytest.raises(HTTPException) as exc_info:
        verify_token(token)
    assert exc_info.value.status_code == 401


async def test_token_with_unknown_kid_rejected(client, isolated_keyring):
    isolated_keyring.ensure_seeded()
    from app.auth import create_access_token, verify_token
    token, _, _ = create_access_token(username="admin", apps={})
    header = pyjwt.get_unverified_header(token)
    forged = pyjwt.encode({"sub": "admin"}, isolated_keyring.get_active_private_pem(),
                          algorithm="RS256", headers={"kid": "intruder"})
    assert header["kid"] != "intruder"
    with pytest.raises(HTTPException) as exc_info:
        verify_token(forged)
    assert exc_info.value.status_code == 401
