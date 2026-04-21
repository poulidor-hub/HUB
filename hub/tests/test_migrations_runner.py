"""
Tests du runner de migrations (`apply_migrations`).

Garanties vérifiées :
- Fail-fast : un SQL invalide lève MigrationError et n'enregistre PAS la
  migration (rollback complet, ré-exécution possible au prochain boot).
- Détection de collision : deux fichiers avec le même préfixe de version
  lèvent MigrationError avant toute exécution.
- Réussite : une migration valide s'applique et est enregistrée.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
