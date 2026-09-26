"""
Préférence globale (instance) : priorités comptées dans le badge messages navbar.
- défaut = toutes ; PUT roundtrip + persistance AppSetting ; 422 si priorité
  inconnue ; [] accepté (badge masqué) ; dedup + ordre canonique ; admin requis.
"""
import json
import pytest

from app.models import AppSetting
from .conftest import get_auth_header

URL = "/api/messages/badge-priorities"


@pytest.mark.asyncio
async def test_default_returns_all(client, admin_token):
    resp = await client.get(URL, headers=get_auth_header(admin_token))
    assert resp.status_code == 200
    assert resp.json() == {"priorities": ["info", "warning", "urgent"]}


@pytest.mark.asyncio
async def test_put_roundtrip_and_persistence(client, async_session, admin_token):
    resp = await client.put(URL, json={"priorities": ["warning", "urgent"]},
                            headers=get_auth_header(admin_token))
    assert resp.status_code == 200
    assert resp.json() == {"priorities": ["warning", "urgent"]}

    row = await async_session.get(AppSetting, "messages_badge_priorities")
    assert row is not None
    assert ["warning", "urgent"] == json.loads(row.value)

    resp = await client.get(URL, headers=get_auth_header(admin_token))
    assert resp.json() == {"priorities": ["warning", "urgent"]}


@pytest.mark.asyncio
async def test_put_invalid_priority_422(client, admin_token):
    resp = await client.put(URL, json={"priorities": ["info", "critique"]},
                            headers=get_auth_header(admin_token))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_empty_list_allowed(client, async_session, admin_token):
    """Tout décocher = badge masqué : [] est une valeur valide."""
    resp = await client.put(URL, json={"priorities": []}, headers=get_auth_header(admin_token))
    assert resp.status_code == 200
    assert resp.json() == {"priorities": []}


@pytest.mark.asyncio
async def test_put_dedup_and_canonical_order(client, admin_token):
    resp = await client.put(URL, json={"priorities": ["urgent", "info", "urgent"]},
                            headers=get_auth_header(admin_token))
    assert resp.json() == {"priorities": ["info", "urgent"]}


@pytest.mark.asyncio
async def test_requires_admin(client, user_token):
    resp = await client.get(URL, headers=get_auth_header(user_token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_requires_admin(client, user_token):
    resp = await client.put(URL, json={"priorities": ["info"]}, headers=get_auth_header(user_token))
    assert resp.status_code == 403
