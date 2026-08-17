"""Tests de l'override des rôles dérivés des profils (spec 2026-08-17)."""
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Profile, ProfileAppRole, User, UserAppRole, user_profiles


async def _create_profile(session: AsyncSession, name: str, app_roles: list) -> Profile:
    """Crée un profil avec ses app_roles [(app_id, role), ...]."""
    profile = Profile(name=name, color="#123456")
    session.add(profile)
    await session.flush()
    for app_id, role in app_roles:
        session.add(ProfileAppRole(profile_id=profile.id, app_id=app_id, role=role))
    await session.commit()
    await session.refresh(profile)
    return profile


async def _assign_profile(session: AsyncSession, user_id: int, profile_id: int) -> None:
    await session.execute(
        user_profiles.insert().values(user_id=user_id, profile_id=profile_id)
    )
    await session.commit()


@pytest.mark.asyncio
async def test_user_app_roles_unique_constraint(
    async_session: AsyncSession, test_user: User, test_app
):
    """Deux lignes (user_id, app_id) identiques sont interdites au niveau modèle."""
    async_session.add(UserAppRole(
        user_id=test_user.id, app_id=test_app.id, role="user", source="manual"
    ))
    await async_session.commit()

    async_session.add(UserAppRole(
        user_id=test_user.id, app_id=test_app.id, role="admin", source="profile:1"
    ))
    with pytest.raises(IntegrityError):
        await async_session.commit()
    await async_session.rollback()
