"""oidc_sub : backfill username, immuabilité, règle anti-collision à la création."""
import pytest
from sqlalchemy import select, text

from app.auth import generate_oidc_sub
from app.database import _backfill_user_oidc_subs
from app.models import User


@pytest.mark.asyncio
async def test_oidc_sub_default_is_username(async_session):
    """Un nouveau user créé via le helper reçoit username comme oidc_sub."""
    from app.database import async_session_maker

    async with async_session_maker() as session:
        oidc_sub = await generate_oidc_sub(session, "newuser")
        user = User(username="newuser", hashed_password="x", oidc_sub=oidc_sub)
        session.add(user)
        await session.commit()
    assert oidc_sub == "newuser"


@pytest.mark.asyncio
async def test_generate_oidc_sub_falls_back_to_uuid_on_recycled_name(async_session):
    """Username recyclé (déjà détenu comme oidc_sub par un renommé) -> uuid4."""
    from app.database import async_session_maker

    async with async_session_maker() as session:
        # Un user renommé détient jdupont comme oidc_sub (username actuel autre)
        renamed = User(username="jmartin", hashed_password="x", oidc_sub="jdupont")
        session.add(renamed)
        await session.commit()

        oidc_sub = await generate_oidc_sub(session, "jdupont")  # recyclé !

        assert oidc_sub != "jdupont"
        assert len(oidc_sub) == 36  # uuid4


@pytest.mark.asyncio
async def test_backfill_oidc_sub_fills_legacy_rows(async_session):
    """Les lignes legacy (oidc_sub NULL) reçoivent leur username."""
    from app.database import async_session_maker

    async with async_session_maker() as session:
        user = User(username="olduser", hashed_password="x")
        session.add(user)
        await session.commit()
        await session.execute(text("UPDATE users SET oidc_sub = NULL WHERE username = 'olduser'"))
        await session.commit()

        await _backfill_user_oidc_subs()

        result = await session.execute(select(User).where(User.username == "olduser"))
        assert result.scalar_one().oidc_sub == "olduser"


@pytest.mark.asyncio
async def test_backfill_avoids_collision_with_recycled_oidc_sub(async_session):
    """Backfill d'une ligne dont le username est déjà pris comme oidc_sub -> uuid."""
    from app.database import async_session_maker

    async with async_session_maker() as session:
        renamed = User(username="jmartin", hashed_password="x", oidc_sub="jdupont")
        legacy = User(username="jdupont", hashed_password="x")  # créé hors helper
        session.add_all([renamed, legacy])
        await session.commit()
        await session.execute(text("UPDATE users SET oidc_sub = NULL WHERE username = 'jdupont'"))
        await session.commit()

        await _backfill_user_oidc_subs()

        await session.refresh(legacy)
        assert legacy.oidc_sub != "jdupont"  # collision évitée
        assert legacy.oidc_sub is not None
