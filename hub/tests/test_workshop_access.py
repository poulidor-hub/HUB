"""Accès atelier par utilisateur — table user_workshop_access (spec 2026-09-06)."""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import create_access_token
from app.models import Application, User, UserWorkshopAccess


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
