# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests de l'extraction apps_code et de la reconciliation de schema au restore."""
import tarfile

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.restore_snapshot import _extract_apps_code, _reconcile_forked_migrations


def _make_apps_code_tar(payload_dir, dest):
    with tarfile.open(dest, "w") as t:
        t.add(str(payload_dir), arcname=".")


def test_extract_apps_code_restores_tree(tmp_path, monkeypatch):
    from app import restore_snapshot

    extract_dir = tmp_path / "extract"
    (extract_dir / "apps").mkdir(parents=True)
    payload = tmp_path / "payload"
    (payload / "monapp").mkdir(parents=True)
    (payload / "monapp" / "VERSION").write_text("1.2.3\n")
    _make_apps_code_tar(payload, extract_dir / "apps" / "apps_code.tar")

    target = tmp_path / "apps_source"
    target.mkdir()
    monkeypatch.setattr(restore_snapshot, "APPS_SOURCE_DIR", target)

    assert _extract_apps_code(extract_dir) is True
    assert (target / "monapp" / "VERSION").read_text() == "1.2.3\n"


def test_extract_apps_code_missing_mount_warns_without_error(tmp_path, monkeypatch):
    from app import restore_snapshot

    extract_dir = tmp_path / "extract"
    (extract_dir / "apps").mkdir(parents=True)
    (extract_dir / "apps" / "apps_code.tar").write_bytes(b"fake")

    monkeypatch.setattr(restore_snapshot, "APPS_SOURCE_DIR", tmp_path / "inexistant")

    assert _extract_apps_code(extract_dir) is False


def test_extract_apps_code_no_archive_is_noop(tmp_path):
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    assert _extract_apps_code(extract_dir) is False


# ---------------------------------------------------------------------------
# Réconciliation des migrations divergentes (hub legacy → hub neuf)
# ---------------------------------------------------------------------------

_SCHEMA_MIGRATIONS_DDL = """
    CREATE TABLE schema_migrations (
        version VARCHAR(10) PRIMARY KEY,
        filename VARCHAR(255) NOT NULL,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

_PORT_ALLOCATIONS_DDL = """
    CREATE TABLE port_allocations (
        id INTEGER PRIMARY KEY,
        app_slug VARCHAR(50) NOT NULL
    )
"""


async def _make_restore_db(monkeypatch, applied_versions, with_port_allocations=False):
    """DB SQLite mimant un dump restauré : schema_migrations + éventuellement port_allocations."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(_SCHEMA_MIGRATIONS_DDL))
        for v in applied_versions:
            await conn.execute(
                text("INSERT INTO schema_migrations (version, filename) VALUES (:v, :f)"),
                {"v": v, "f": f"{v}_quelque_chose.sql"},
            )
        if with_port_allocations:
            await conn.execute(text(_PORT_ALLOCATIONS_DDL))
    return engine


@pytest.mark.asyncio
async def test_reconcile_purges_forked_027_when_table_missing():
    """Dump legacy : 027 marquée appliquée mais port_allocations absente → purge de la ligne."""
    engine = await _make_restore_db(None, {"025", "026", "027"})
    try:
        reconciled = await _reconcile_forked_migrations(engine)
        assert reconciled == ["027"]
        async with engine.connect() as conn:
            rows = {r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations"))).fetchall()}
        assert rows == {"025", "026"}  # 027 purgée, les autres intactes
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_noop_when_port_allocations_exists():
    """Restore normal (même repo) : port_allocations existe → aucun changement."""
    engine = await _make_restore_db(None, {"026", "027"}, with_port_allocations=True)
    try:
        reconciled = await _reconcile_forked_migrations(engine)
        assert reconciled == []
        async with engine.connect() as conn:
            rows = {r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations"))).fetchall()}
        assert rows == {"026", "027"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_tolerates_missing_schema_migrations():
    """DB sans schema_migrations (cas dégénéré) → ne doit pas lever, rien à faire."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        reconciled = await _reconcile_forked_migrations(engine)
        assert reconciled == []
    finally:
        await engine.dispose()


# ── Fourches 028 (colonne email) et 029 (DROP backup_clusters) ──────────────
# Un dump legacy (hub-legacy 3.9.x) marque 028 = create_hub_snapshots et
# 029 = add_snapshot_settings : nos 028/029 ne sont JAMAIS rejouées après un
# restore → colonne account_requests.email manquante (crash runtime prouvé
# 12/11/2026) et tables backup_clusters résiduelles.

_ACCOUNT_REQUESTS_SANS_EMAIL_DDL = """
    CREATE TABLE account_requests (
        id INTEGER PRIMARY KEY,
        username VARCHAR(50) NOT NULL
    )
"""

_ACCOUNT_REQUESTS_AVEC_EMAIL_DDL = """
    CREATE TABLE account_requests (
        id INTEGER PRIMARY KEY,
        username VARCHAR(50) NOT NULL,
        email VARCHAR(100)
    )
"""

_BACKUP_CLUSTERS_DDL = """
    CREATE TABLE backup_clusters (
        id INTEGER PRIMARY KEY,
        name VARCHAR(50) NOT NULL
    )
"""


async def _make_restore_db_legacy(monkeypatch, applied_versions, *,
                                  account_requests="sans_email",
                                  with_backup_clusters=True):
    """DB SQLite mimant un dump legacy : schema_migrations + tables legacy typiques.

    account_requests : "sans_email" (structure legacy) | "avec_email" (notre 028)
                       | None (table absente).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(_SCHEMA_MIGRATIONS_DDL))
        for v in applied_versions:
            await conn.execute(
                text("INSERT INTO schema_migrations (version, filename) VALUES (:v, :f)"),
                {"v": v, "f": f"{v}_quelque_chose.sql"},
            )
        if account_requests == "sans_email":
            await conn.execute(text(_ACCOUNT_REQUESTS_SANS_EMAIL_DDL))
        elif account_requests == "avec_email":
            await conn.execute(text(_ACCOUNT_REQUESTS_AVEC_EMAIL_DDL))
        if with_backup_clusters:
            await conn.execute(text(_BACKUP_CLUSTERS_DDL))
    return engine


@pytest.mark.asyncio
async def test_reconcile_purges_forked_028_when_email_column_missing():
    """Dump legacy : 028 marquée (create_hub_snapshots) sans notre colonne email → purge."""
    engine = await _make_restore_db_legacy(None, {"027", "028", "029"})
    try:
        reconciled = await _reconcile_forked_migrations(engine)
        assert "028" in reconciled
        async with engine.connect() as conn:
            rows = {r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations"))).fetchall()}
        assert "028" not in rows  # purgée → le boot rejugera notre 028 (colonne email)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_noop_when_028_email_column_exists():
    """Restore normal (même repo) : la colonne email existe → 028 conservée."""
    engine = await _make_restore_db_legacy(None, {"028"}, account_requests="avec_email",
                                           with_backup_clusters=False)
    try:
        reconciled = await _reconcile_forked_migrations(engine)
        assert "028" not in reconciled
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_purges_028_when_table_entirely_missing():
    """Table account_requests absente mais 028 marquée → purge (le boot recrée tout)."""
    engine = await _make_restore_db_legacy(None, {"028"}, account_requests=None,
                                           with_backup_clusters=False)
    try:
        reconciled = await _reconcile_forked_migrations(engine)
        assert "028" in reconciled
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_purges_forked_029_when_backup_clusters_present():
    """Dump legacy : 029 marquée (add_snapshot_settings) et backup_clusters encore là → purge."""
    engine = await _make_restore_db_legacy(None, {"027", "028", "029"})
    try:
        reconciled = await _reconcile_forked_migrations(engine)
        assert "029" in reconciled
        async with engine.connect() as conn:
            rows = {r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations"))).fetchall()}
        assert "029" not in rows  # purgée → le boot rejugera notre 029 (DROP clusters)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_noop_when_backup_clusters_already_dropped():
    """Notre 029 a tourné (clusters supprimées) → 029 conservée."""
    engine = await _make_restore_db_legacy(None, {"029"}, account_requests="avec_email",
                                           with_backup_clusters=False)
    try:
        reconciled = await _reconcile_forked_migrations(engine)
        assert "029" not in reconciled
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_purges_all_three_forks_from_legacy_dump():
    """Dump legacy complet : 027+028+029 fourchues → les trois purgées, l'ordre est trié."""
    engine = await _make_restore_db_legacy(None, {"025", "026", "027", "028", "029"})
    try:
        reconciled = await _reconcile_forked_migrations(engine)
        assert reconciled == ["027", "028", "029"]
    finally:
        await engine.dispose()
