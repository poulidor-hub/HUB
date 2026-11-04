# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

import pyotp
import pytest
from httpx import AsyncClient

from app.models import User
from tests.conftest import get_auth_header

pytestmark = pytest.mark.asyncio


async def _setup_secret(client: AsyncClient, token: str) -> str:
    resp = await client.post("/api/auth/totp/setup", headers=get_auth_header(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["otpauth_uri"].startswith("otpauth://totp/")
    assert data["qr_data_uri"].startswith("data:image/svg+xml;base64,")
    return data["secret"]


async def test_setup_then_confirm_enables_totp(client, test_user, user_token, async_session):
    secret = await _setup_secret(client, user_token)
    code = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/api/auth/totp/confirm", headers=get_auth_header(user_token), json={"code": code}
    )
    assert resp.status_code == 200
    await async_session.refresh(test_user)
    assert test_user.totp_enabled is True
    assert test_user.totp_confirmed_at is not None


async def test_confirm_with_bad_code_fails(client, test_user, user_token, async_session):
    await _setup_secret(client, user_token)
    resp = await client.post(
        "/api/auth/totp/confirm", headers=get_auth_header(user_token), json={"code": "000000"}
    )
    assert resp.status_code == 400
    await async_session.refresh(test_user)
    assert test_user.totp_enabled is False


async def test_me_reports_totp_enabled(client, test_user, user_token, async_session):
    secret = await _setup_secret(client, user_token)
    await client.post(
        "/api/auth/totp/confirm", headers=get_auth_header(user_token),
        json={"code": pyotp.TOTP(secret).now()},
    )
    resp = await client.get("/api/auth/me", headers=get_auth_header(user_token))
    assert resp.status_code == 200
    assert resp.json()["totp_enabled"] is True


async def test_disable_requires_correct_password(client, test_user, user_token, async_session):
    secret = await _setup_secret(client, user_token)
    await client.post(
        "/api/auth/totp/confirm", headers=get_auth_header(user_token),
        json={"code": pyotp.TOTP(secret).now()},
    )
    bad = await client.post(
        "/api/auth/totp/disable", headers=get_auth_header(user_token),
        json={"current_password": "wrong"},
    )
    assert bad.status_code == 400
    ok = await client.post(
        "/api/auth/totp/disable", headers=get_auth_header(user_token),
        json={"current_password": "testpass123"},
    )
    assert ok.status_code == 200
    await async_session.refresh(test_user)
    assert test_user.totp_enabled is False
    assert test_user.totp_secret is None
