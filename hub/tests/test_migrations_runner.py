"""
Tests du runner de migrations (`apply_migrations`).

Garanties vérifiées :
- Fail-fast : un SQL invalide lève MigrationError et n'enregistre PAS la
    migration (rollback complet, ré-exécution possible au prochain boot).
- Détection de collision : deux fichiers avec le même préfixe de version
    lèvent MigrationError avant toute exécution.
- Réussite : une migration valide s'applique et est enregistrée.
- Historique réservé : une migration déjà enregistrée en DB mais conservée
    comme placeholder sur disque ne doit pas être signalée comme absente.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import database as db_module
from app.database import MigrationError, apply_migrations


@pytest.fixture
async def temp_engine(tmp_path: Path):
    """Engine SQLite temporaire isolé du reste de la suite."""
    db_path = tmp_path / "migrations_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    original_engine = db_module.engine
    db_module.engine = engine
    try:
        yield engine
    finally:
        db_module.engine = original_engine
        await engine.dispose()


def _patch_migrations_dir(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Force apply_migrations à utiliser un dossier de migrations donné."""
    real_path = Path

    class _Path(real_path):  # type: ignore[misc]
        _flavour = real_path("/")._flavour  # type: ignore[attr-defined]

        def __new__(cls, *args, **kwargs):
            if args and args[0] == "/code/migrations":
                return real_path(directory)
            return real_path(*args, **kwargs)

    monkeypatch.setattr(db_module, "Path", _Path)


@pytest.mark.asyncio
async def test_apply_migrations_succeeds_on_valid_sql(
    temp_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    migrations_dir = tmp_path / "migrations_ok"
    migrations_dir.mkdir()
    (migrations_dir / "001_create_demo.sql").write_text(
        "CREATE TABLE demo (id INTEGER PRIMARY KEY);"
    )
    _patch_migrations_dir(monkeypatch, migrations_dir)

    await apply_migrations()

    async with temp_engine.begin() as conn:
        result = await conn.execute(
            text("SELECT version FROM schema_migrations ORDER BY version")
        )
        applied = [row[0] for row in result.fetchall()]
    assert applied == ["001"]


@pytest.mark.asyncio
async def test_apply_migrations_fails_fast_on_invalid_sql(
    temp_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Une migration invalide doit lever ET ne pas être marquée appliquée."""
    migrations_dir = tmp_path / "migrations_bad"
    migrations_dir.mkdir()
    (migrations_dir / "001_create_demo.sql").write_text(
        "CREATE TABLE demo (id INTEGER PRIMARY KEY);"
    )
    (migrations_dir / "002_broken.sql").write_text(
        "THIS IS NOT VALID SQL AT ALL;"
    )
    _patch_migrations_dir(monkeypatch, migrations_dir)

    with pytest.raises(MigrationError, match="002_broken.sql"):
        await apply_migrations()

    async with temp_engine.begin() as conn:
        result = await conn.execute(
            text("SELECT version FROM schema_migrations ORDER BY version")
        )
        applied = [row[0] for row in result.fetchall()]
    # 001 doit être appliquée, 002 doit avoir été rollback
    assert applied == ["001"]


@pytest.mark.asyncio
async def test_apply_migrations_detects_version_collision(
    temp_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Deux fichiers avec le même préfixe de version doivent être refusés."""
    migrations_dir = tmp_path / "migrations_collide"
    migrations_dir.mkdir()
    (migrations_dir / "001_first.sql").write_text(
        "CREATE TABLE a (id INTEGER PRIMARY KEY);"
    )
    (migrations_dir / "001_second.sql").write_text(
        "CREATE TABLE b (id INTEGER PRIMARY KEY);"
    )
    _patch_migrations_dir(monkeypatch, migrations_dir)

    with pytest.raises(MigrationError, match="Collision"):
        await apply_migrations()


@pytest.mark.asyncio
async def test_apply_migrations_keeps_reserved_placeholder_on_disk(
    temp_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
):
    """Un placeholder reserve ne doit pas etre signale comme manquant."""
    migrations_dir = tmp_path / "migrations_reserved"
    migrations_dir.mkdir()
    (migrations_dir / "020_add_oidc_support.sql").write_text(
        "-- migration reservee; aucune action SQL\n"
    )
    _patch_migrations_dir(monkeypatch, migrations_dir)

    async with temp_engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(10) PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                "INSERT INTO schema_migrations (version, filename) "
                "VALUES ('020', '020_add_oidc_support.sql')"
            )
        )

    await apply_migrations()

    assert "absentes sur disque" not in caplog.text


@pytest.mark.asyncio
async def test_apply_migrations_skips_message_migration_already_applied_as_020(
    temp_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
):
    """Une ancienne 020 messages couvre la 021 messages actuelle."""
    caplog.set_level(logging.INFO)
    migrations_dir = tmp_path / "migrations_message_alias"
    migrations_dir.mkdir()
    (migrations_dir / "020_add_oidc_support.sql").write_text(
        "-- migration reservee; aucune action SQL\n"
    )
    message_migration = (
        migrations_dir / "021_add_message_lifecycle_and_engagement.sql"
    )
    message_migration.write_text(
        "CREATE TABLE should_not_run (id INTEGER PRIMARY KEY);"
    )
    _patch_migrations_dir(monkeypatch, migrations_dir)

    async with temp_engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(10) PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                "INSERT INTO schema_migrations (version, filename) "
                "VALUES "
                "('020', '020_add_message_lifecycle_and_engagement.sql')"
            )
        )

    await apply_migrations()

    async with temp_engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'should_not_run'"
            )
        )
        assert result.scalar_one_or_none() is None
    assert "déjà couverte par l'historique" in caplog.text


@pytest.mark.asyncio
async def test_apply_migrations_runs_message_migration_after_reserved_020(
    temp_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Une 020 OIDC/no-op ne couvre pas la 021 messages."""
    migrations_dir = tmp_path / "migrations_reserved_then_message"
    migrations_dir.mkdir()
    (migrations_dir / "020_add_oidc_support.sql").write_text(
        "-- migration reservee; aucune action SQL\n"
    )
    message_migration = (
        migrations_dir / "021_add_message_lifecycle_and_engagement.sql"
    )
    message_migration.write_text(
        "CREATE TABLE message_marker (id INTEGER PRIMARY KEY);"
    )
    _patch_migrations_dir(monkeypatch, migrations_dir)

    async with temp_engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(10) PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                "INSERT INTO schema_migrations (version, filename) "
                "VALUES ('020', '020_add_oidc_support.sql')"
            )
        )

    await apply_migrations()

    async with temp_engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'message_marker'"
            )
        )
        assert result.scalar_one_or_none() == "message_marker"
        result = await conn.execute(
            text(
                "SELECT filename FROM schema_migrations "
                "WHERE version = '021'"
            )
        )
        assert (
            result.scalar_one()
            == "021_add_message_lifecycle_and_engagement.sql"
        )
