# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Durcissement M2M : rate limit sur client_credentials + TTL court.

Audit 08/11/2026 : la branche client_credentials de /api/auth/token était
la seule sans rate limit (brute force de secrets non bornée, endpoint
exposé sur 80/443/8000), et les tokens M2M vivaient 8 h (TTL utilisateur).
"""
import base64
import json

import pytest

from app.routes import auth as auth_routes
from app.config import get_settings


def _decode_payload(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    auth_routes._login_attempts.clear()
    yield
    auth_routes._login_attempts.clear()


@pytest.mark.asyncio
async def test_m2m_bad_secret_is_rate_limited(client, service_account):
    """6 tentatives avec mauvais secret : les 5 premières → 401, la 6e → 429."""
    data = {
        "grant_type": "client_credentials",
        "client_id": service_account.client_id,
        "client_secret": "mauvais-secret",
    }
    statuses = []
    for _ in range(6):
        r = await client.post("/api/auth/token", data=data)
        statuses.append(r.status_code)
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429, f"la 6e tentative doit être rate-limitée : {statuses}"


@pytest.mark.asyncio
async def test_m2m_token_ttl_is_short(client, async_session, service_account):
    """TTL M2M = m2m_access_token_expire_minutes (60 min par défaut), pas 480."""
    r = await client.post(
        "/api/auth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": service_account.client_id,
            "client_secret": "secret123",
        },
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    claims = _decode_payload(token)
    ttl_s = claims["exp"] - claims["iat"]
    expected = get_settings().m2m_access_token_expire_minutes * 60
    assert ttl_s == expected, f"TTL M2M {ttl_s}s != {expected}s"
    assert ttl_s < 480 * 60  # strictement plus court que le TTL utilisateur
