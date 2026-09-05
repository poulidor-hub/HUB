"""Renommage exceptionnel du login : tracé, révocatoire, unique."""
import pytest
from sqlalchemy import select

from app.auth import create_access_token
from app.models import User
from tests.conftest import get_auth_header


@pytest.mark.asyncio
async def test_rename_success(client, admin_user, test_user):
    token, _, _ = create_access_token(username=admin_user.username, apps={})

    response = await client.post(
        f"/api/users/{test_user.id}/rename",
        json={"username": "JMartin"},
        headers=get_auth_header(token),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "jmartin"  # normalisé lowercase

    # Vérifier en base
    from app.database import async_session_maker

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.id == test_user.id))
        assert result.scalar_one().username == "jmartin"


@pytest.mark.asyncio
async def test_rename_duplicate_returns_409(client, admin_user, test_user):
    token, _, _ = create_access_token(username=admin_user.username, apps={})

    response = await client.post(
        f"/api/users/{test_user.id}/rename",
        json={"username": admin_user.username},
        headers=get_auth_header(token),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_rename_forbidden_for_non_admin(client, admin_user, test_user):
    token, _, _ = create_access_token(username=test_user.username, apps={})

    response = await client.post(
        f"/api/users/{admin_user.id}/rename",
        json={"username": "hacked"},
        headers=get_auth_header(token),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_rename_revokes_old_token_and_notifies(client, admin_user, test_user, monkeypatch):
    """L'ancien JWT (sub=ancien username) devient invalide + event force_logout."""
    events = []

    from app.routes import users as users_module

    def fake_notify(event_type, data):
        events.append((event_type, data))

    monkeypatch.setattr(users_module, "notify_event", fake_notify)

    old_token, _, _ = create_access_token(username=test_user.username, apps={})
    admin_token, _, _ = create_access_token(username=admin_user.username, apps={})

    response = await client.post(
        f"/api/users/{test_user.id}/rename",
        json={"username": "jmartin"},
        headers=get_auth_header(admin_token),
    )
    assert response.status_code == 200

    # L'ancien token ne résout plus (sub = ancien username)
    old_response = await client.patch(
        f"/api/users/{test_user.id}",
        json={"is_active": True},
        headers=get_auth_header(old_token),
    )
    assert old_response.status_code == 401

    logout_events = [e for e in events if e[0] == "force_logout"]
    assert logout_events and logout_events[0][1]["reason"] == "username_renamed"


@pytest.mark.asyncio
async def test_rename_writes_audit_entry(client, admin_user, test_user):
    """Opération EXCEPTIONNELLE et TRACÉE : une entrée audit rename_user est écrite."""
    old = test_user.username  # capturer avant le rename (attribut fixture ensuite stale)

    token, _, _ = create_access_token(username=admin_user.username, apps={})

    response = await client.post(
        f"/api/users/{test_user.id}/rename",
        json={"username": "jmartin"},
        headers=get_auth_header(token),
    )
    assert response.status_code == 200

    from app.database import async_session_maker
    from app.models import AuditLog

    async with async_session_maker() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "rename_user")
        )
        entry = result.scalar_one()
        assert entry.target == f"{old} -> jmartin"
        assert entry.response_status == 200
