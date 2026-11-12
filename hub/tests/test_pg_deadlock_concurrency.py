# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Preuve d'intégration sur PostgreSQL réel — deadlocks fermetures SSE.

HORS suite standard (SQLite mémoire) : ce test ne tourne que si la variable
d'environnement TEST_DATABASE_URL pointe vers une base PostgreSQL jetable, ex. :

    TEST_DATABASE_URL="postgresql+asyncpg://hub:***@localhost:5432/hub_deadlock_test" \
        python3 -m pytest tests/test_pg_deadlock_concurrency.py -v

Reproduit le scénario prod du 12/11/2026 (bug 31 deadlocks en 33 h) : à la
déconnexion, handle_app_disconnect et handle_user_disconnect partent en
parallèle sur la même session (grâce DISCONNECT_GRACE_S écoulée). Avant fix,
les deux writers verrouillaient segments/session dans des ordres opposés
(AB-BA, « deadlock detected » côté hub-db). L'invariant post-fix : la ligne
user_sessions est verrouillée (FOR UPDATE) avant tout touch de ses segments.

Signal : le compteur record_db_write_error() — alimenté par les exceptions de
fermeture SSE en prod (dont les deadlocks) — doit rester à zéro sur N
itérations, et chaque itération doit laisser segment(s) ET session fermés.
"""
import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AppUsageSegment, User, UserSession, utcnow

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL non défini : test d'intégration PostgreSQL réel "
           "(lancé manuellement ou dans le conteneur, cf. docstring)",
)

N_ITERATIONS = 20


@pytest.fixture()
async def pg_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    from app.database import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture()
def pg_session_maker(pg_engine):
    return async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_pas_de_deadlock_sur_fermetures_sse_concurrentes(
    pg_engine, pg_session_maker, monkeypatch
):
    import app.database as database
    import app.routes.events as events_mod
    from app.routes.events import db_write_errors_last_hour, handle_app_disconnect, handle_user_disconnect
    from app.auth import get_password_hash

    # Sessions des handlers dirigées vers la PG de test
    monkeypatch.setattr(database, "async_session_maker", pg_session_maker)
    # Registres SSE vides + grâce de déconnexion neutralisée
    monkeypatch.setattr(events_mod, "_app_connections", {})
    monkeypatch.setattr(events_mod, "_user_connections", {})

    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    errors_before = db_write_errors_last_hour()

    for i in range(N_ITERATIONS):
        async with pg_session_maker() as db:
            user = User(
                username=f"deadlock_it{i}",
                email=f"deadlock_it{i}@test.local",
                hashed_password=get_password_hash("x"),
                is_active=True,
            )
            db.add(user)
            await db.flush()
            user_id = user.id
            session_row = UserSession(
                user_id=user_id, login_at=utcnow(), last_activity_at=utcnow(),
                current_app_slug="test-app",
            )
            db.add(session_row)
            await db.flush()
            session_id = session_row.id
            db.add(AppUsageSegment(
                session_id=session_id, user_id=user_id,
                app_slug="test-app", started_at=utcnow(),
            ))
            await db.commit()

        # Scénario prod : les deux flux SSE ferment la même session en parallèle
        await asyncio.gather(
            handle_app_disconnect(user_id, "test-app"),
            handle_user_disconnect(user_id),
        )

        async with pg_session_maker() as db:
            seg = (await db.execute(
                select(AppUsageSegment).where(AppUsageSegment.session_id == session_id)
            )).scalars().first()
            assert seg is not None and seg.ended_at is not None, (
                f"itération {i}: segment non fermé"
            )
            sess = await db.get(UserSession, session_id)
            assert sess.logout_at is not None, f"itération {i}: session non fermée"

    errors_after = db_write_errors_last_hour()
    assert errors_after == errors_before, (
        f"{errors_after - errors_before} erreur(s) d'écriture DB (deadlock ?) "
        f"sur {N_ITERATIONS} fermetures SSE concurrentes"
    )
