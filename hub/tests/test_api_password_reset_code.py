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
    assert row.code_hash != body["code"]
    assert verify_password(body["code"], row.code_hash)          # hash bcrypt vérifiable
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
