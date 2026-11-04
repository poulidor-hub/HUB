import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.auth import verify_password
from app.models import AuditLog, PasswordResetCode, utcnow
from tests.conftest import get_auth_header

pytestmark = pytest.mark.asyncio

CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
GEN = "/api/users/{uid}/password-reset-code"


async def test_password_reset_code_model_roundtrip(async_session):
    row = PasswordResetCode(
        user_id=1,
        code_hash="hash-bcrypt-jamais-le-code-en-clair",
        expires_at=utcnow() + timedelta(minutes=15),
        created_by_user_id=2,
    )
    async_session.add(row)
    await async_session.commit()
    await async_session.refresh(row)
    assert row.id is not None
    assert row.consumed_at is None
    assert row.code_hash != "XXXX-XXXX"


async def test_generate_requires_admin(client, test_user, user_token):
    assert (await client.post(GEN.format(uid=test_user.id))).status_code == 401
    r = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(user_token))
    assert r.status_code == 403


async def test_generate_unknown_user_404(client, admin_token):
    r = await client.post(GEN.format(uid=999999), headers=get_auth_header(admin_token))
    assert r.status_code == 404


async def test_generate_inactive_user_400(client, test_user, admin_token, async_session):
    test_user.is_active = False
    await async_session.commit()
    r = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(admin_token))
    assert r.status_code == 400


async def test_generate_success_format_hash_ttl(client, test_user, admin_token, async_session):
    r = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert CODE_RE.match(body["code"]) and body["expires_at"]
    row = (await async_session.execute(
        select(PasswordResetCode).where(PasswordResetCode.user_id == test_user.id))).scalar_one()
    from app.auth import normalize_reset_code
    assert row.code_hash != body["code"]
    assert verify_password(normalize_reset_code(body["code"]), row.code_hash)  # hash du code normalisé
    assert row.consumed_at is None and row.created_by_user_id is not None
    audit = (await async_session.execute(
        select(AuditLog).where(AuditLog.action == "password_reset_code_generated"))).scalar_one()
    assert audit.user_id is not None


async def test_generate_revokes_previous_code(client, test_user, admin_token, async_session):
    r1 = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(admin_token))
    r2 = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(admin_token))
    assert r1.json()["code"] != r2.json()["code"]
    rows = (await async_session.execute(
        select(PasswordResetCode).where(PasswordResetCode.user_id == test_user.id))).scalars().all()
    assert sum(1 for x in rows if x.consumed_at is None) == 1    # seul le dernier est actif


async def _gen(client, admin_token, uid):
    r = await client.post(GEN.format(uid=uid), headers=get_auth_header(admin_token))
    assert r.status_code == 200
    return r.json()["code"]


async def test_verify_reset_code_delivers_pwd_reset(client, test_user, admin_token, async_session):
    from app.auth import decode_reset_token
    code = await _gen(client, admin_token, test_user.id)
    r = await client.post("/api/auth/recover/verify",
                          json={"username": "testuser", "reset_code": code})
    assert r.status_code == 200
    payload = decode_reset_token(r.json()["reset_token"])
    assert payload["scope"] == "pwd_reset" and payload["sub"] == "testuser"
    row = (await async_session.execute(
        select(PasswordResetCode).where(PasswordResetCode.user_id == test_user.id))).scalar_one()
    assert row.consumed_at is not None
    assert (await async_session.execute(
        select(AuditLog).where(AuditLog.action == "password_reset_code_verified"))).scalar_one()


async def test_verify_normalization_accepts_lowercase_and_spaces(client, test_user, admin_token):
    code = await _gen(client, admin_token, test_user.id)
    messy = f" {code.lower().replace('-', ' ')} "
    r = await client.post("/api/auth/recover/verify",
                          json={"username": "testuser", "reset_code": messy})
    assert r.status_code == 200


async def test_verify_generic_errors_identical(client, test_user, admin_token, async_session):
    from app.routes.auth import _recover_attempts
    generic = "Code de récupération invalide ou récupération indisponible"
    code = await _gen(client, admin_token, test_user.id)

    async def try_code(body):
        return await client.post("/api/auth/recover/verify", json=body)

    r = await try_code({"username": "ghost", "reset_code": code})
    assert (r.status_code, r.json()["detail"]) == (401, generic)      # user inconnu
    _recover_attempts.clear()
    r = await try_code({"username": "testuser", "reset_code": "ZZZZ-ZZZZ"})
    assert (r.status_code, r.json()["detail"]) == (401, generic)      # mauvais code
    _recover_attempts.clear()
    r = await try_code({"username": "testuser", "reset_code": code.lower()})
    assert r.status_code == 200
    _recover_attempts.clear()
    r = await try_code({"username": "testuser", "reset_code": code})   # déjà consommé
    assert (r.status_code, r.json()["detail"]) == (401, generic)


async def test_verify_expired_code_401(client, test_user, admin_token, async_session):
    code = await _gen(client, admin_token, test_user.id)
    row = (await async_session.execute(
        select(PasswordResetCode).where(PasswordResetCode.user_id == test_user.id))).scalar_one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    await async_session.commit()
    r = await client.post("/api/auth/recover/verify",
                          json={"username": "testuser", "reset_code": code})
    assert r.status_code == 401


async def test_verify_rate_limit_dedicated(client, test_user, admin_token):
    from app.routes.auth import _login_attempts
    code = await _gen(client, admin_token, test_user.id)
    statuses = []
    for _ in range(6):
        r = await client.post("/api/auth/recover/verify",
                              json={"username": "testuser", "reset_code": "AAAA-AAAA"})
        statuses.append(r.status_code)
    assert statuses[-1] == 429
    r_last = await client.post("/api/auth/recover/verify",
                               json={"username": "testuser", "reset_code": code})
    assert r_last.status_code == 429 and "retry-after" in {k.lower() for k in r_last.headers}
    assert len(_login_attempts) == 0   # limiter login intact (Review Focus #2)
