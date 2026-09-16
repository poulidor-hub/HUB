"""Tests API /api/auth/keys — endpoints admin du trousseau (spec §5.5)."""
import jwt as pyjwt
import pytest
from tests.conftest import get_auth_header
from tests.test_jwt_keyring import isolated_keyring


@pytest.fixture
def admin_token_isolated(admin_user, isolated_keyring):
    """Token admin signé par le trousseau isolé (seedé avant signature)."""
    isolated_keyring.ensure_seeded()
    from app.auth import create_access_token
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    return token


async def test_endpoints_require_auth(client, isolated_keyring):
    assert (await client.get("/api/auth/keys")).status_code == 401
    assert (await client.post("/api/auth/keys/rotate")).status_code == 401


async def test_list_requires_admin(client, isolated_keyring, async_session):
    isolated_keyring.ensure_seeded()
    from app.auth import create_access_token, get_password_hash
    from app.models import User
    user = User(username="pasadmin", email="pa@example.com",
                hashed_password=get_password_hash("x"), is_active=True)
    async_session.add(user)
    await async_session.commit()
    token, _, _ = create_access_token(username="pasadmin", apps={})
    resp = await client.get("/api/auth/keys", headers=get_auth_header(token))
    assert resp.status_code == 403


async def test_list_rotate_revoke_flow(client, isolated_keyring, admin_token_isolated,
                                       admin_user):
    hdr = get_auth_header(admin_token_isolated)

    resp = await client.get("/api/auth/keys", headers=hdr)
    assert resp.status_code == 200
    assert resp.json()["keys"][0]["status"] == "active"

    resp = await client.post("/api/auth/keys/rotate", headers=hdr)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"new_kid", "old_kid", "overlap_ends_at"}
    new_kid = body["new_kid"]

    resp = await client.get("/api/auth/keys", headers=hdr)
    statuses = {k["kid"]: k["status"] for k in resp.json()["keys"]}
    assert statuses[new_kid] == "active"
    assert statuses[isolated_keyring.LEGACY_KID] == "retiring"

    # Révoquer la clé active → 409
    resp = await client.post("/api/auth/keys/revoke", json={"kid": new_kid}, headers=hdr)
    assert resp.status_code == 409

    # Révoquer l'ancienne → 200 puis statut revoked
    resp = await client.post("/api/auth/keys/revoke",
                             json={"kid": isolated_keyring.LEGACY_KID}, headers=hdr)
    assert resp.status_code == 200
    # Le token admin était signé par la clé désormais révoquée (401 attendu) :
    # re-signature avec la clé active pour la vérification finale.
    from app.auth import create_access_token
    token2, _, _ = create_access_token(username=admin_user.username, apps={})
    resp = await client.get("/api/auth/keys", headers=get_auth_header(token2))
    statuses = {k["kid"]: k["status"] for k in resp.json()["keys"]}
    assert statuses[isolated_keyring.LEGACY_KID] == "revoked"


async def test_cleanup_endpoint(client, admin_token_isolated):
    hdr = get_auth_header(admin_token_isolated)
    resp = await client.post("/api/auth/keys/cleanup?older_than_months=12", headers=hdr)
    assert resp.status_code == 200
    assert resp.json() == {"removed": []}


async def test_public_key_endpoint_serves_active_key_after_rotate(client, isolated_keyring):
    """/api/auth/public-key suit la clé active du trousseau, même après rotation."""
    isolated_keyring.ensure_seeded()
    isolated_keyring.rotate()

    resp = await client.get("/api/auth/public-key")
    assert resp.status_code == 200
    served_key = resp.json()["public_key"]
    expected = isolated_keyring.get_public_pem(isolated_keyring.get_active_kid())
    assert "".join(served_key.split()) == "".join(expected.split())

    # Un token minté APRÈS la rotation se vérifie avec la clé servie.
    from app.auth import create_access_token
    token, _, _ = create_access_token(username="admin", apps={})
    payload = pyjwt.decode(token, served_key, algorithms=["RS256"])
    assert payload["sub"] == "admin"
