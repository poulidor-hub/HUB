"""Colonnes identité User : uuid par défaut, backfill des lignes legacy."""
import pytest
from sqlalchemy import select, text

from app.database import _backfill_user_uuids
from app.models import User


@pytest.mark.asyncio
async def test_user_uuid_default_generated(async_session):
    """Tout nouveau User reçoit un uuid (36 car.) automatiquement."""
    user = User(username="jdupont", hashed_password="x")
    async_session.add(user)
    await async_session.commit()
    assert user.uuid is not None
    assert len(user.uuid) == 36


@pytest.mark.asyncio
async def test_backfill_user_uuids(async_session):
    """Les utilisateurs legacy (uuid NULL) en reçoivent un au backfill."""
    user = User(username="olduser", hashed_password="x")
    async_session.add(user)
    await async_session.commit()
    # Simuler une ligne legacy (créée avant la colonne)
    await async_session.execute(
        text("UPDATE users SET uuid = NULL WHERE username = 'olduser'")
    )
    await async_session.commit()

    await _backfill_user_uuids()

    result = await async_session.execute(
        select(User).where(User.username == "olduser")
    )
    refreshed = result.scalar_one()
    assert refreshed.uuid is not None
    assert len(refreshed.uuid) == 36


@pytest.mark.asyncio
async def test_backfill_idempotent(async_session):
    """Relancer le backfill ne modifie pas les uuid existants."""
    user = User(username="hasuuid", hashed_password="x")
    async_session.add(user)
    await async_session.commit()
    original = user.uuid

    await _backfill_user_uuids()
    await _backfill_user_uuids()

    await async_session.refresh(user)
    assert user.uuid == original
