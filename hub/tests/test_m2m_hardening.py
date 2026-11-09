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

from app.auth import get_password_hash
from app.models import ServiceAccount
from app.routes import auth as auth_routes
from app.config import get_settings


def _decode_payload(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


# (Reset des compteurs de rate limit : conftest global `reset_rate_limiter`,
#  qui vide aussi _m2m_attempts.)


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


# ===== Compteur interne keyé par client_id (incident 09/11/2026) =====
# Toutes les apps du parc mintent derrière la même IP gateway (10.42.0.1) :
# le compteur par IP bloquait le mint M2M inter-apps (84×429 en cascade).


@pytest.mark.asyncio
async def test_m2m_apps_behind_gateway_share_no_quota(client, async_session, monkeypatch):
    """3 apps derrière la même IP interne : aucun 429 entre elles (3×10 mints)."""
    monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: True)
    client_ids = []
    for i in range(3):
        cid = f"sa_parque_app_{i}_aaaaaaaa"
        sa = ServiceAccount(
            name=f"SA parc app {i}",
            client_id=cid,
            client_secret_hash=get_password_hash("secret123"),
            is_active=True,
        )
        async_session.add(sa)
        client_ids.append(cid)
    await async_session.commit()

    for _ in range(10):
        for cid in client_ids:
            r = await client.post("/api/auth/token", data={
                "grant_type": "client_credentials",
                "client_id": cid,
                "client_secret": "secret123",
            })
            assert r.status_code == 200, (
                f"mint {cid} bloqué ({r.status_code}) — le compteur interne "
                f"ne doit pas être partagé entre apps : {r.text}"
            )


@pytest.mark.asyncio
async def test_m2m_internal_per_client_still_limited(client, service_account, monkeypatch):
    """IP interne : la borne anti-hammering reste active PAR client_id (20/60 s)."""
    monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: True)
    data = {
        "grant_type": "client_credentials",
        "client_id": service_account.client_id,
        "client_secret": "mauvais-secret",
    }
    statuses = []
    for _ in range(auth_routes._M2M_RATE_LIMIT_MAX_INTERNAL + 1):
        r = await client.post("/api/auth/token", data=data)
        statuses.append(r.status_code)
    assert statuses[:auth_routes._M2M_RATE_LIMIT_MAX_INTERNAL] == [401] * auth_routes._M2M_RATE_LIMIT_MAX_INTERNAL
    assert statuses[-1] == 429, f"la borne interne par client_id doit tenir : {statuses}"
