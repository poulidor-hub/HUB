from datetime import timedelta

import pytest

from app.models import PasswordResetCode, utcnow

pytestmark = pytest.mark.asyncio


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
