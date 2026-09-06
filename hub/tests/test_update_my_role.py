"""PUT /api/apps/me/role/{slug} (update_my_role) — idempotence sous concurrence.

Incident 2026-09-06 : double-émission du sélecteur « Mon rôle » → deux PUT
quasi simultanés ; le second SELECT s'exécute avant le commit du premier →
INSERT → UniqueViolationError → 500. Le endpoint doit être idempotent.
"""
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession as AsyncSessionForTest

from app.database import Base
from app.models import Application, User, UserAppRole
from app.routes.apps import update_my_role


async def test_double_put_sequentiel_idempotent(async_session, client, admin_token):
    """Deux PUT successifs (même rôle) : 200 aux deux, une seule ligne."""
    app = Application(name="Canari", slug="canari", api_url="http://x:1")
    async_session.add(app)
    await async_session.commit()
    headers = {"Authorization": f"Bearer {admin_token}"}
    r1 = await client.put("/api/apps/me/role/canari?role=admin", headers=headers)
    r2 = await client.put("/api/apps/me/role/canari?role=admin", headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    rows = (await async_session.execute(
        select(UserAppRole).where(UserAppRole.app_id == app.id))).scalars().all()
    assert len(rows) == 1 and rows[0].role == "admin"


async def test_update_my_role_race_idempotent(tmp_path, monkeypatch):
    """Course réelle : une requête concurrente (autre connexion, même DB)
    crée la ligne entre le SELECT du endpoint et son commit → IntegrityError
    au commit → le endpoint doit répondre 200 (idempotent), une seule ligne."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/race.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSessionForTest, expire_on_commit=False)
    async with maker() as sa:
        admin = User(username="raceadmin", email="race@test.com",
                     hashed_password="x", is_admin=True)
        app = Application(name="Canari", slug="canari", api_url="http://x:1")
        sa.add_all([admin, app])
        await sa.commit()
        await sa.refresh(admin)
        await sa.refresh(app)
        app_id = app.id  # capturé tôt : rollback (chemin race) expire les objets

        real_commit = sa.commit
        calls = {"n": 0}

        async def racing_commit():
            if calls["n"] == 0:
                # 1er commit (course) : la requête concurrente INSERT + COMMIT
                # pendant la fenêtre → notre INSERT percute l'unique.
                calls["n"] = 1
                async with engine.begin() as conn:
                    await conn.execute(text(
                        "INSERT INTO user_app_roles (user_id, app_id, role, source) "
                        f"VALUES ({admin.id}, {app.id}, 'admin', 'manual')"))
                await real_commit()  # → UniqueViolation (ligne déjà posée)
                return
            await real_commit()

        monkeypatch.setattr(sa, "commit", racing_commit)

        result = await update_my_role(
            app_slug=app.slug, role="admin", request=None,
            db=sa, current_user=admin,
        )
        assert result["my_role"] == "admin"

        rows = (await sa.execute(
            select(UserAppRole).where(UserAppRole.app_id == app_id))).scalars().all()
        assert len(rows) == 1 and rows[0].role == "admin"
    await engine.dispose()


async def test_update_my_role_race_sans_ligne_relance(async_session, monkeypatch):
    """Cas anormal : IntegrityError mais après rollback la ligne est toujours
    absente → l'erreur d'origine est relancée (pas de succès mensonger)."""
    admin, app = None, None
    admin = User(username="raceadmin2", email="race2@test.com",
                 hashed_password="x", is_admin=True)
    app = Application(name="Canari2", slug="canari2", api_url="http://x:1")
    async_session.add_all([admin, app])
    await async_session.commit()
    await async_session.refresh(admin)
    await async_session.refresh(app)
    app_id = app.id  # capturé tôt : rollback (chemin race) expire les objets

    real_commit = async_session.commit
    calls = {"n": 0}

    async def racing_commit():
        if calls["n"] == 0:
            calls["n"] = 1
            raise IntegrityError("stmt", {}, Exception("duplicate"))
        await real_commit()

    monkeypatch.setattr(async_session, "commit", racing_commit)

    with pytest.raises(IntegrityError):
        await update_my_role(
            app_slug=app.slug, role="admin", request=None,
            db=async_session, current_user=admin,
        )
    rows = (await async_session.execute(
        select(UserAppRole).where(UserAppRole.app_id == app_id))).scalars().all()
    assert rows == []
