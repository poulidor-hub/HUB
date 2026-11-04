# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

import pytest
from app.models import User
from app.auth import get_password_hash
from tests.conftest import get_auth_header

pytestmark = pytest.mark.asyncio


async def test_admin_can_disable_user_totp(client, admin_user, admin_token, async_session):
    target = User(
        username="hastotp",
        email="hastotp@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        totp_enabled=True,
        totp_secret="ENC:dummy",
    )
    async_session.add(target)
    await async_session.commit()
    await async_session.refresh(target)

    resp = await client.post(
        f"/api/users/{target.id}/totp/disable", headers=get_auth_header(admin_token)
    )
    assert resp.status_code == 200
    await async_session.refresh(target)
    assert target.totp_enabled is False
    assert target.totp_secret is None


async def test_non_admin_cannot_disable_totp(client, test_user, user_token, async_session):
    resp = await client.post(
        f"/api/users/{test_user.id}/totp/disable", headers=get_auth_header(user_token)
    )
    assert resp.status_code == 403
