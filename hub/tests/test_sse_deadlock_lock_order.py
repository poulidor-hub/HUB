# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Ordre de verrouillage anti-deadlock user_sessions / app_usage_segments.

Bug du 12/11/2026 (31 deadlocks en 33 h en prod sur le hub 60) : AB-BA entre
- W1 handle_app_disconnect  : tout est flushé au commit, et le tri des mappers
  SQLAlchemy (_sort_key alphabétique : app.models.AppUsageSegment < app.models
  .UserSession) émet UPDATE app_usage_segments AVANT UPDATE user_sessions ;
- W2 end_session            : les affectations sur la session partent en UPDATE
  dès l'autoflush déclenché par le SELECT segments de close_open_segments,
  donc AVANT les segments.

Invariant imposé (ordre de verrouillage global unique) : toute transaction
touchant user_sessions + app_usage_segments doit émettre, avant tout statement
verrouillant sur ces deux tables, un SELECT ... FOR UPDATE sur la ligne
user_sessions concernée. Les segments ne sont jamais touchés qu'une fois le
verrou session acquis, et sans autoflush intercalé (émission déterministe).

Les tests écoutent les SQL émis sur l'engine de test ; le flag FOR UPDATE est
vérifié en recompilant le statement avec le dialecte PostgreSQL (SQLite
l'ignore silencieusement, le code produit doit le porter).
"""
import asyncio
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from app.auth import get_settings
from app.models import AppUsageSegment, UserSession, utcnow
from app.routes.audit import cleanup_stale_sessions, end_session
from app.routes.events import handle_app_disconnect, handle_user_disconnect
from app.usage_tracking import close_open_segments, touch_app_presence
from tests.test_usage_segments import _make_session

settings = get_settings()


# ---------------------------------------------------------------------------
# Espion SQL : capture les statements émis et leur portage FOR UPDATE
# ---------------------------------------------------------------------------

@contextmanager
def _spy_sql(async_engine):
    """Capture (verb, table, for_update) de chaque statement émis sur l'engine.

    Écoute `before_execute` (ClauseElement brut) : recompiler avec le dialecte
    PostgreSQL révèle le FOR UPDATE que SQLite ignore silencieusement."""
    records = []

    def _rec(conn, clause_element, multiparams, params, execution_options):
        verb = ""
        pg_sql = ""
        if clause_element is not None:
            try:
                pg_sql = str(clause_element.compile(dialect=postgresql.dialect()))
            except Exception:
                pg_sql = str(clause_element)
            text = pg_sql.strip()
            if text:
                verb = text.split(None, 1)[0].upper()
        records.append({
            "verb": verb,
            "for_update": "FOR UPDATE" in pg_sql.upper(),
            "sessions": "user_sessions" in pg_sql,
            "segments": "app_usage_segments" in pg_sql,
            "pg_sql": pg_sql,
        })

    event.listen(async_engine.sync_engine, "before_execute", _rec)
    try:
        yield records
    finally:
        event.remove(async_engine.sync_engine, "before_execute", _rec)


def _lock_taking(rec) -> bool:
    """Un statement qui prend un verrou de ligne sur PG (pas un simple SELECT)."""
    return rec["for_update"] or rec["verb"] in ("UPDATE", "INSERT", "DELETE")


def _dump(records) -> str:
    return "\n".join(
        f"  {i}: {r['verb']:<7} sessions={r['sessions']!s:<5} "
        f"segments={r['segments']!s:<5} for_update={r['for_update']}"
        for i, r in enumerate(records)
    )


def _assert_session_locked_first(records, context: str) -> None:
    """Premier statement verrouillant sur la paire sessions/segments :
    doit être le SELECT ... FOR UPDATE sur user_sessions."""
    lock_idx = [i for i, r in enumerate(records) if _lock_taking(r) and (r["sessions"] or r["segments"])]
    assert lock_idx, f"{context}: aucun statement verrouillant capturé\n{_dump(records)}"
    first = records[lock_idx[0]]
    assert first["sessions"] and first["verb"] == "SELECT" and first["for_update"], (
        f"{context}: la première prise de verrou n'est pas SELECT user_sessions "
        f"FOR UPDATE (reçu {first['verb']} sessions={first['sessions']} "
        f"for_update={first['for_update']}) — risque AB-BA\n{_dump(records)}"
    )


def _assert_no_autoflush_interleave(records, context: str) -> None:
    """Après le SELECT FOR UPDATE, aucun UPDATE user_sessions ne doit être
    émis avant le premier statement sur app_usage_segments (le SELECT segments
    ne doit pas déclencher d'autoflush inversé)."""
    fu_idx = next(i for i, r in enumerate(records) if r["for_update"])
    seg_idx = next((i for i, r in enumerate(records) if r["segments"]), None)
    if seg_idx is None:
        return
    upd_sess_idx = [i for i, r in enumerate(records) if r["verb"] == "UPDATE" and r["sessions"]]
    if upd_sess_idx:
        assert upd_sess_idx[0] > seg_idx, (
            f"{context}: UPDATE user_sessions émis (autoflush) entre le FOR UPDATE "
            f"(idx {fu_idx}) et le premier statement segments (idx {seg_idx}) — "
            f"encapsuler la fermeture segments dans no_autoflush\n{_dump(records)}"
        )


# ---------------------------------------------------------------------------
# W2 — end_session (logout / beacon / déconnexion SSE)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_end_session_verrouille_la_session_avant_les_segments(
    async_engine, async_session, test_user
):
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    await async_session.commit()

    with _spy_sql(async_engine) as records:
        session = await end_session(async_session, test_user.id, reason="sse_disconnect")
        await async_session.commit()

    assert session is not None
    _assert_session_locked_first(records, "end_session")
    _assert_no_autoflush_interleave(records, "end_session")


# ---------------------------------------------------------------------------
# W1 — handle_app_disconnect (déconnexion SSE d'une app, flush au commit)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_app_disconnect_verrouille_la_session_avant_les_segments(
    async_engine, async_session, test_user, monkeypatch
):
    import app.routes.events as events_mod

    # Registres SSE vides : aucune connexion restante -> la fermeture part
    monkeypatch.setattr(events_mod, "_app_connections", {})
    monkeypatch.setattr(events_mod, "_user_connections", {})

    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    s.current_app_slug = "test-app"
    await async_session.commit()

    with _spy_sql(async_engine) as records:
        await handle_app_disconnect(test_user.id, "test-app")

    _assert_session_locked_first(records, "handle_app_disconnect")


# ---------------------------------------------------------------------------
# W3 — cleanup_stale_sessions (sweeper 120 s, fermeture timeout)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_stale_sessions_verrouille_la_session_avant_les_segments(
    async_engine, async_session, test_user
):
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    # Rendre la session stale : inactivité > session_timeout_minutes
    s.last_activity_at = utcnow() - timedelta(minutes=settings.session_timeout_minutes + 10)
    await async_session.commit()

    with _spy_sql(async_engine) as records:
        closed = await cleanup_stale_sessions(async_session)

    assert closed >= 1, "la session stale doit être fermée par le sweep"
    _assert_session_locked_first(records, "cleanup_stale_sessions")
    _assert_no_autoflush_interleave(records, "cleanup_stale_sessions")


# ---------------------------------------------------------------------------
# Filet — ordre déterministe des UPDATE segments multi-lignes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_open_segments_trie_les_segments_par_id(
    async_engine, async_session, test_user
):
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "app-a")
    await touch_app_presence(async_session, s, "app-b")
    await async_session.commit()

    with _spy_sql(async_engine) as records:
        n = await close_open_segments(async_session, s.id, utcnow(), "logout")
        await async_session.commit()

    assert n == 2
    seg_selects = [
        r["pg_sql"] for r in records
        if r["segments"] and r["verb"] == "SELECT"
    ]
    assert seg_selects, f"aucun SELECT app_usage_segments capturé\n{_dump(records)}"
    assert any("ORDER BY app_usage_segments.id" in sql for sql in seg_selects), (
        "close_open_segments doit ordonner les segments par id "
        "(filet anti-inversion intra-table)\n" + "\n".join(seg_selects)
    )


# ---------------------------------------------------------------------------
# Régression métier — fermetures successives, rien n'est rouvert
# (garde-fou comportemental ; la preuve concurrence PG réelle est dans
# test_pg_deadlock_concurrency.py, lancée avec TEST_DATABASE_URL)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fermetures_consecutives_laissent_tout_ferme(
    async_engine, async_session, test_user, monkeypatch
):
    import app.database
    import app.routes.events as events_mod
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    monkeypatch.setattr(events_mod, "_app_connections", {})
    monkeypatch.setattr(events_mod, "_user_connections", {})

    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    await async_session.commit()

    # 1) fermeture de session (logout SSE)
    assert await end_session(async_session, test_user.id, reason="sse_disconnect")
    await async_session.commit()

    # 2) la fermeture du segment app arrive ensuite (grâce écoulée)
    maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(app.database, "async_session_maker", maker)
    await handle_app_disconnect(test_user.id, "test-app")

    async with maker() as fresh:
        seg = (await fresh.execute(
            select(AppUsageSegment).where(AppUsageSegment.session_id == s.id)
        )).scalars().first()
        assert seg is not None
        assert seg.ended_at is not None, "le segment doit rester fermé"
        sess = await fresh.get(UserSession, s.id)
        assert sess.logout_at is not None, "la session doit rester fermée"
