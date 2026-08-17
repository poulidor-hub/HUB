"""Tests de l'override des rôles dérivés des profils (spec 2026-08-17)."""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Profile, ProfileAppRole, User, UserAppRole, user_profiles
from app.routes.profiles import _sync_user_profile_roles


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


async def _get_uar(session: AsyncSession, user_id: int, app_id: int) -> UserAppRole | None:
    result = await session.execute(
        select(UserAppRole).where(
            UserAppRole.user_id == user_id, UserAppRole.app_id == app_id
        )
    )
    return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_sync_ignores_active_override(
    async_session: AsyncSession, test_user: User, test_app, other_app
):
    """Un override dont le profil d'origine couvre encore l'app survit à la sync."""
    profile = await _create_profile(async_session, "P1", [(test_app.id, "user")])
    await _assign_profile(async_session, test_user.id, profile.id)

    # Rôle attendu par le profil, puis override admin vers manager
    async_session.add(UserAppRole(
        user_id=test_user.id, app_id=test_app.id,
        role="manager", source=f"override:{profile.id}",
    ))
    await async_session.commit()

    await _sync_user_profile_roles(async_session, test_user.id)
    await async_session.commit()

    uar = await _get_uar(async_session, test_user.id, test_app.id)
    assert uar.role == "manager"
    assert uar.source == f"override:{profile.id}"


@pytest.mark.asyncio
async def test_sync_deletes_override_when_profile_detached(
    async_session: AsyncSession, test_user: User, test_app
):
    """Override supprimé quand son profil d'origine ne couvre plus l'app (perte d'accès)."""
    profile = await _create_profile(async_session, "P1", [(test_app.id, "user")])
    await _assign_profile(async_session, test_user.id, profile.id)
    async_session.add(UserAppRole(
        user_id=test_user.id, app_id=test_app.id,
        role="manager", source=f"override:{profile.id}",
    ))
    await async_session.commit()

    # Détacher le profil (suppression de l'association), puis re-sync
    await async_session.execute(
        user_profiles.delete().where(user_profiles.c.user_id == test_user.id)
    )
    await async_session.commit()
    await _sync_user_profile_roles(async_session, test_user.id)
    await async_session.commit()

    uar = await _get_uar(async_session, test_user.id, test_app.id)
    assert uar is None


@pytest.mark.asyncio
async def test_sync_recreates_profile_line_after_override_stale(
    async_session: AsyncSession, test_user: User, test_app
):
    """Override:A périmé mais profil B couvre encore l'app → ligne profile:B recréée."""
    profile_a = await _create_profile(async_session, "PA", [(test_app.id, "manager")])
    profile_b = await _create_profile(async_session, "PB", [(test_app.id, "viewer")])
    await _assign_profile(async_session, test_user.id, profile_a.id)
    await _assign_profile(async_session, test_user.id, profile_b.id)
    async_session.add(UserAppRole(
        user_id=test_user.id, app_id=test_app.id,
        role="admin", source=f"override:{profile_a.id}",
    ))
    await async_session.commit()

    # Retirer l'app du profil A (l'override:A devient périmé)
    await async_session.execute(
        ProfileAppRole.__table__.delete().where(
            ProfileAppRole.profile_id == profile_a.id,
            ProfileAppRole.app_id == test_app.id,
        )
    )
    await async_session.commit()

    await _sync_user_profile_roles(async_session, test_user.id)
    await async_session.commit()

    uar = await _get_uar(async_session, test_user.id, test_app.id)
    assert uar.role == "viewer"                       # rôle attendu de B
    assert uar.source == f"profile:{profile_b.id}"    # override:A bien supprimé


from httpx import AsyncClient


async def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_set_user_apps_creates_override(
    client: AsyncClient, admin_token: str, test_user: User, test_app, async_session
):
    """Assignment sur app profilée → ligne override:<pid> au rôle choisi."""
    profile = await _create_profile(async_session, "P1", [(test_app.id, "user")])
    await _assign_profile(async_session, test_user.id, profile.id)
    async_session.add(UserAppRole(
        user_id=test_user.id, app_id=test_app.id,
        role="user", source=f"profile:{profile.id}",
    ))
    await async_session.commit()

    resp = await client.post(
        f"/api/users/{test_user.id}/apps",
        headers=await _auth(admin_token),
        json={"assignments": [{"app_id": test_app.id, "role": "manager"}]},
    )
    assert resp.status_code == 200, resp.text

    uar = await _get_uar(async_session, test_user.id, test_app.id)
    assert uar.role == "manager"
    assert uar.source == f"override:{profile.id}"


@pytest.mark.asyncio
async def test_set_user_apps_reset_returns_to_profile_role(
    client: AsyncClient, admin_token: str, test_user: User, test_app, async_session
):
    """reset=True sur un override → retour au rôle attendu, source profile:<pid>."""
    profile = await _create_profile(async_session, "P1", [(test_app.id, "user")])
    await _assign_profile(async_session, test_user.id, profile.id)
    async_session.add(UserAppRole(
        user_id=test_user.id, app_id=test_app.id,
        role="manager", source=f"override:{profile.id}",
    ))
    await async_session.commit()

    resp = await client.post(
        f"/api/users/{test_user.id}/apps",
        headers=await _auth(admin_token),
        json={"assignments": [{"app_id": test_app.id, "role": "user", "reset": True}]},
    )
    assert resp.status_code == 200, resp.text

    uar = await _get_uar(async_session, test_user.id, test_app.id)
    assert uar.role == "user"
    assert uar.source == f"profile:{profile.id}"


@pytest.mark.asyncio
async def test_set_user_apps_no_duplicate_on_profile_app(
    client: AsyncClient, admin_token: str, test_user: User, test_app, async_session
):
    """Régression bug filtre source=='profile' : plus de ligne manuelle dupliquée
    sur une app couverte par un profil."""
    profile = await _create_profile(async_session, "P1", [(test_app.id, "user")])
    await _assign_profile(async_session, test_user.id, profile.id)
    async_session.add(UserAppRole(
        user_id=test_user.id, app_id=test_app.id,
        role="user", source=f"profile:{profile.id}",
    ))
    await async_session.commit()

    resp = await client.post(
        f"/api/users/{test_user.id}/apps",
        headers=await _auth(admin_token),
        json={"assignments": [{"app_id": test_app.id, "role": "viewer"}]},
    )
    assert resp.status_code == 200, resp.text

    result = await async_session.execute(
        select(UserAppRole).where(
            UserAppRole.user_id == test_user.id,
            UserAppRole.app_id == test_app.id,
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1                      # un seul doublon impossible (contrainte + upsert)
    assert rows[0].source == f"override:{profile.id}"
    assert rows[0].role == "viewer"


@pytest.mark.asyncio
async def test_set_user_apps_manual_unaffected(
    client: AsyncClient, admin_token: str, test_user: User, test_app, other_app, async_session
):
    """Une app non couverte par un profil reste manuelle ; reset y est ignoré."""
    resp = await client.post(
        f"/api/users/{test_user.id}/apps",
        headers=await _auth(admin_token),
        json={"assignments": [{"app_id": test_app.id, "role": "viewer", "reset": True}]},
    )
    assert resp.status_code == 200, resp.text

    uar = await _get_uar(async_session, test_user.id, test_app.id)
    assert uar.role == "viewer"
    assert uar.source == "manual"


@pytest.mark.asyncio
async def test_set_user_apps_rejects_duplicate_app_id(
    client: AsyncClient, admin_token: str, test_user: User, test_app, async_session
):
    """Doublon d'app_id dans le payload → 422, aucune ligne créée."""
    resp = await client.post(
        f"/api/users/{test_user.id}/apps",
        headers=await _auth(admin_token),
        json={"assignments": [
            {"app_id": test_app.id, "role": "user"},
            {"app_id": test_app.id, "role": "admin"},
        ]},
    )
    assert resp.status_code == 422, resp.text

    uar = await _get_uar(async_session, test_user.id, test_app.id)
    assert uar is None  # rien n'a été écrit
