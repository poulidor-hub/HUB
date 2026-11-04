# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests pour les endpoints de templates de messages.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from app.models import MessageTemplate
from .conftest import get_auth_header


async def test_list_templates_empty(client: AsyncClient, admin_token: str):
    response = await client.get("/api/messages/templates", headers=get_auth_header(admin_token))
    assert response.status_code == 200
    assert isinstance(response.json(), list)


async def test_create_template(client: AsyncClient, admin_token: str):
    response = await client.post(
        "/api/messages/templates",
        json={
            "name": "Test Template",
            "title_template": "Test {{var}}",
            "default_priority": "urgent",
        },
        headers=get_auth_header(admin_token),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Template"
    assert data["default_priority"] == "urgent"
    assert data["title_template"] == "Test {{var}}"


async def test_create_duplicate_template(client: AsyncClient, admin_token: str):
    await client.post(
        "/api/messages/templates",
        json={"name": "Template Dup"},
        headers=get_auth_header(admin_token),
    )
    response = await client.post(
        "/api/messages/templates",
        json={"name": "Template Dup"},
        headers=get_auth_header(admin_token),
    )
    assert response.status_code == 400


async def test_update_template(client: AsyncClient, admin_token: str):
    create = await client.post(
        "/api/messages/templates",
        json={"name": "To Update"},
        headers=get_auth_header(admin_token),
    )
    tid = create.json()["id"]
    response = await client.put(
        f"/api/messages/templates/{tid}",
        json={"name": "Updated Name"},
        headers=get_auth_header(admin_token),
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"


async def test_update_nonexistent_template(client: AsyncClient, admin_token: str):
    response = await client.put(
        "/api/messages/templates/99999",
        json={"name": "Nope"},
        headers=get_auth_header(admin_token),
    )
    assert response.status_code == 404


async def test_delete_template(client: AsyncClient, admin_token: str):
    create = await client.post(
        "/api/messages/templates",
        json={"name": "To Delete"},
        headers=get_auth_header(admin_token),
    )
    tid = create.json()["id"]
    response = await client.delete(f"/api/messages/templates/{tid}", headers=get_auth_header(admin_token))
    assert response.status_code == 204

    get_resp = await client.get("/api/messages/templates", headers=get_auth_header(admin_token))
    assert not any(t["id"] == tid for t in get_resp.json())


async def test_delete_nonexistent_template(client: AsyncClient, admin_token: str):
    response = await client.delete("/api/messages/templates/99999", headers=get_auth_header(admin_token))
    assert response.status_code == 404


async def test_send_with_template_id(client: AsyncClient, admin_token: str):
    tmpl = await client.post(
        "/api/messages/templates",
        json={
            "name": "Broadcast Template",
            "title_template": "Default Title",
            "body_template": "Default body from template",
            "default_priority": "warning",
        },
        headers=get_auth_header(admin_token),
    )
    tid = tmpl.json()["id"]

    response = await client.post(
        "/api/messages/send",
        json={
            "title": "Override Title",
            "priority": "info",
            "target_type": "broadcast",
            "template_id": tid,
        },
        headers=get_auth_header(admin_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sent"] > 0
    assert not data.get("scheduled")


async def test_send_scheduled_message(client: AsyncClient, admin_token: str):
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    response = await client.post(
        "/api/messages/send",
        json={
            "title": "Scheduled Message",
            "priority": "info",
            "target_type": "broadcast",
            "scheduled_at": future,
        },
        headers=get_auth_header(admin_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scheduled"] is True
    assert data["sent"] == 0
    assert data["scheduled_at"] is not None
