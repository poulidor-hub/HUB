"""Accès atelier par utilisateur — table user_workshop_access (spec 2026-09-06)."""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import create_access_token
from app.models import (
    Application,
    User,
    UserWorkshopAccess,
    get_apps_with_roles_and_workshops,
    workshop_role_for_app,
)


async def _mk_user_app(async_session, username="wsuser", slug="canari"):
    user = User(username=username, email=f"{username}@test.com", hashed_password="x")
    app = Application(name=slug.capitalize(), slug=slug, api_url=f"http://x:1")
    async_session.add_all([user, app])
    await async_session.commit()
    await async_session.refresh(user)
    await async_session.refresh(app)
    return user, app


async def test_link_persists(async_session):
    user, app = await _mk_user_app(async_session)
    async_session.add(UserWorkshopAccess(user_id=user.id, app_id=app.id))
    await async_session.commit()
    rows = (await async_session.execute(select(UserWorkshopAccess))).scalars().all()
    assert len(rows) == 1 and rows[0].user_id == user.id and rows[0].app_id == app.id


async def test_link_unique(async_session):
    user, app = await _mk_user_app(async_session)
    async_session.add(UserWorkshopAccess(user_id=user.id, app_id=app.id))
    await async_session.commit()
    async_session.add(UserWorkshopAccess(user_id=user.id, app_id=app.id))
    with pytest.raises(IntegrityError):
        await async_session.commit()


async def test_link_cascade_on_app_delete(async_session):
    user, app = await _mk_user_app(async_session)
    async_session.add(UserWorkshopAccess(user_id=user.id, app_id=app.id))
    await async_session.commit()
    await async_session.delete(app)
    await async_session.commit()
    rows = (await async_session.execute(select(UserWorkshopAccess))).scalars().all()
    assert rows == []


async def test_workshop_role_for_app_nominal(async_session):
    user, base = await _mk_user_app(async_session)
    ws = Application(name="Canari ws", slug="canari-ws", api_url="http://x:2", is_workshop=True)
    async_session.add(ws)
    await async_session.commit()
    async_session.add(UserWorkshopAccess(user_id=user.id, app_id=base.id))
    await async_session.commit()
    assert await workshop_role_for_app(async_session, user.id, "canari-ws") == "user"


async def test_workshop_role_for_app_sans_acces(async_session):
    user, base = await _mk_user_app(async_session)
    ws = Application(name="Canari ws", slug="canari-ws", api_url="http://x:2", is_workshop=True)
    async_session.add(ws)
    await async_session.commit()
    assert await workshop_role_for_app(async_session, user.id, "canari-ws") is None


async def test_workshop_role_for_app_atelier_inexistant_ou_inactif(async_session):
    user, base = await _mk_user_app(async_session)
    ghost = Application(name="Ghost ws", slug="ghost-ws", api_url="http://x:3",
                        is_workshop=True, is_active=False)
    async_session.add(ghost)
    await async_session.commit()
    async_session.add(UserWorkshopAccess(user_id=user.id, app_id=base.id))
    await async_session.commit()
    assert await workshop_role_for_app(async_session, user.id, "canari-ws") is None   # n'existe pas
    assert await workshop_role_for_app(async_session, user.id, "ghost-ws") is None    # inactive


async def test_workshop_role_for_app_slug_non_ws(async_session):
    user, base = await _mk_user_app(async_session)
    assert await workshop_role_for_app(async_session, user.id, "canari") is None


async def test_map_jwt_avec_ateliers(async_session):
    user, base = await _mk_user_app(async_session)
    ws = Application(name="Canari ws", slug="canari-ws", api_url="http://x:2", is_workshop=True)
    ghost = Application(name="Ghost ws", slug="ghost-ws", api_url="http://x:3",
                        is_workshop=True, is_active=False)
    async_session.add_all([ws, ghost])
    await async_session.commit()
    async_session.add(UserWorkshopAccess(user_id=user.id, app_id=base.id))
    await async_session.commit()
    apps = await get_apps_with_roles_and_workshops(user, async_session)
    assert apps.get("canari-ws") == "user"
    assert "ghost-ws" not in apps


async def test_map_jwt_setdefault(async_session):
    """Le helper n'écrase pas une attribution explicite existante (setdefault)."""
    from app.models import UserAppRole

    user, base = await _mk_user_app(async_session)
    ws = Application(name="Canari ws", slug="canari-ws", api_url="http://x:2", is_workshop=True)
    async_session.add_all([user, ws])
    await async_session.commit()
    await async_session.refresh(user)
    async_session.add(UserAppRole(user_id=user.id, app_id=ws.id, role="manager"))
    async_session.add(UserWorkshopAccess(user_id=user.id, app_id=base.id))
    await async_session.commit()
    apps = await get_apps_with_roles_and_workshops(user, async_session)
    assert apps["canari-ws"] == "manager"  # rôle explicite gagne sur "user"
