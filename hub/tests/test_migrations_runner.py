# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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
from sqlalchemy import UniqueConstraint, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import database as db_module
from app.database import MigrationError, apply_migrations
from app.models import Application, user_profiles


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

    def patched_path(*args, **kwargs):
        if args and args[0] == "/code/migrations":
            return real_path(directory)
        return real_path(*args, **kwargs)

    monkeypatch.setattr(db_module, "Path", patched_path)


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


# ---------------------------------------------------------------------------
# Non-régression : sur une install fraîche, `init_db()` exécute
# `Base.metadata.create_all` AVANT `apply_migrations()` (cf. lifespan dans
# app/main.py). Toutes les colonnes des modèles actuels existent donc déjà
# quand le runner exécute les migrations historiques. Toute migration qui
# fait `ALTER TABLE ... ADD COLUMN` (ou `CREATE TABLE/INDEX`) sans garde
# `IF NOT EXISTS` provoquera un crash de boot (DuplicateColumnError sous
# PostgreSQL). Ce test garde-fou inspecte les fichiers .sql livrés et refuse
# toute régression de cette classe de bug.
# ---------------------------------------------------------------------------


_REPO_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _iter_real_migration_files() -> list[Path]:
    return sorted(_REPO_MIGRATIONS_DIR.glob("*.sql"))


def _strip_sql_text(sql: str) -> str:
    """Retire commentaires de ligne et normalise les espaces."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return " ".join(" ".join(lines).split())


@pytest.mark.parametrize(
    "migration_path",
    _iter_real_migration_files(),
    ids=lambda p: p.name,
)
def test_real_migration_is_idempotent_against_create_all(migration_path: Path):
    """Chaque migration livrée doit être rejouable après `create_all`.

    Sur une install fraîche, `init_db()` matérialise déjà toutes les colonnes
    et tables des modèles SQLAlchemy actuels. Les migrations historiques
    doivent donc utiliser `IF NOT EXISTS` (ou un équivalent comme
    `DROP CONSTRAINT IF EXISTS` avant `ADD CONSTRAINT`) sous peine de casser
    le démarrage avec une DuplicateColumnError / DuplicateTable.
    """
    sql_normalized = _strip_sql_text(migration_path.read_text()).upper()

    # `ALTER TABLE ... ADD COLUMN` doit être idempotent.
    import re

    add_column_pattern = re.compile(
        r"ALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN(?!\s+IF\s+NOT\s+EXISTS\b)",
        re.IGNORECASE,
    )
    matches = add_column_pattern.findall(sql_normalized)
    assert not matches, (
        f"{migration_path.name} contient un `ALTER TABLE ... ADD COLUMN` "
        f"sans `IF NOT EXISTS` ; cela casse le boot fresh install après "
        f"`Base.metadata.create_all`. Occurrences: {matches}"
    )

    # `CREATE TABLE` non temporaire doit être idempotent.
    create_table_pattern = re.compile(
        r"CREATE\s+TABLE(?!\s+IF\s+NOT\s+EXISTS\b)(?!\s+TEMP)",
        re.IGNORECASE,
    )
    matches = create_table_pattern.findall(sql_normalized)
    assert not matches, (
        f"{migration_path.name} contient un `CREATE TABLE` sans "
        f"`IF NOT EXISTS`. Occurrences: {matches}"
    )

    # `CREATE INDEX` non unique-anonyme doit être idempotent.
    create_index_pattern = re.compile(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX(?!\s+IF\s+NOT\s+EXISTS\b)",
        re.IGNORECASE,
    )
    matches = create_index_pattern.findall(sql_normalized)
    assert not matches, (
        f"{migration_path.name} contient un `CREATE INDEX` sans "
        f"`IF NOT EXISTS`. Occurrences: {matches}"
    )


def test_critical_migrations_001_002_use_if_not_exists():
    """Garde-fou explicite sur les deux migrations qui ont historiquement
    cassé le boot fresh install (cf. DuplicateColumnError sur
    `users.must_change_password` et `applications.approval_status`)."""
    sql_001 = (_REPO_MIGRATIONS_DIR / "001_add_must_change_password.sql").read_text()
    sql_002 = (_REPO_MIGRATIONS_DIR / "002_add_app_approval_status.sql").read_text()

    assert "ADD COLUMN IF NOT EXISTS must_change_password" in sql_001
    assert "ADD COLUMN IF NOT EXISTS approval_status" in sql_002
    assert "ADD COLUMN IF NOT EXISTS approved_by_id" in sql_002
    assert "ADD COLUMN IF NOT EXISTS approved_at" in sql_002
    assert "ADD COLUMN IF NOT EXISTS rejection_reason" in sql_002


def test_user_profiles_model_exposes_on_conflict_target():
    """`create_all()` doit matérialiser l'unicité attendue par la 011."""
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in user_profiles.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("user_id", "profile_id") in unique_column_sets


def test_migration_011_adds_unique_index_before_on_conflict():
    """La 011 doit être sûre si `user_profiles` existe déjà via `create_all`."""
    sql = _strip_sql_text(
        (_REPO_MIGRATIONS_DIR / "011_multi_profiles.sql").read_text()
    ).upper()
    unique_index_pos = sql.find(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "IDX_USER_PROFILES_UNIQUE_USER_PROFILE ON USER_PROFILES(USER_ID, PROFILE_ID)"
    )
    on_conflict_pos = sql.find("ON CONFLICT (USER_ID, PROFILE_ID) DO NOTHING")

    assert unique_index_pos != -1
    assert on_conflict_pos != -1
    assert unique_index_pos < on_conflict_pos


# ---------------------------------------------------------------------------
# Non-régression : l'INSERT explicite de la migration 026 (app système Hub)
# doit inclure toutes les colonnes NOT NULL sans server_default du modèle
# Application.  Toute nouvelle colonne NOT NULL ajoutée ultérieurement
# (ex: is_workshop mig 043, is_unmanaged mig 045) casse cet INSERT et
# provoque un NotNullViolation sur fresh install.
# ---------------------------------------------------------------------------


def test_migration_026_insert_covers_all_required_columns():
    """Chaque colonne NOT NULL sans server_default du modèle Application
    doit apparaître dans l'INSERT de la migration 026."""
    import re

    # Colonnes NOT NULL sans server_default (exclut id auto, PK)
    required = {
        col.name
        for col in Application.__table__.columns
        if not col.nullable
        and col.server_default is None
        and not col.primary_key
        and not col.autoincrement  # au cas où
    }

    sql = (_REPO_MIGRATIONS_DIR / "026_add_backup_clusters_and_system_app.sql").read_text()

    # Extraire la liste entre "INSERT INTO applications (" et ") VALUES"
    match = re.search(
        r"INSERT\s+INTO\s+applications\s*\((.*?)\)\s*VALUES",
        sql,
        re.DOTALL | re.IGNORECASE,
    )
    assert match, (
        "Migration 026: impossible de parser la liste des colonnes de l'INSERT"
    )

    columns_text = match.group(1)
    insert_columns = {
        col.strip().lower() for col in columns_text.split(",") if col.strip()
    }

    # Vérifier que le nombre de colonnes et de VALUES correspondent
    values_match = re.search(
        r"VALUES\s*\((.*?)\)\s*\n?\s*ON\s+CONFLICT",
        sql,
        re.DOTALL | re.IGNORECASE,
    )
    if values_match:
        values_text = values_match.group(1)
        # Compter les valeurs (split par virgule, en gérant les chaînes entre quotes)
        value_count = len(re.findall(r"(?:[^,'\"]|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")+", values_text))
        assert value_count == len(insert_columns), (
            f"Migration 026: mismatch colonnes/VALUES — "
            f"{len(insert_columns)} colonnes mais {value_count} valeurs"
        )

    missing = required - insert_columns
    assert not missing, (
        f"Migration 026: l'INSERT INTO applications omet des colonnes "
        f"NOT NULL sans server_default : {sorted(missing)}. "
        f"Sur fresh install, create_all() crée ces colonnes avant la "
        f"migration, et l'INSERT échoue avec NotNullViolation."
    )

    # Vérifie aussi que chaque colonne de l'INSERT existe bien dans le modèle
    model_columns = {col.name for col in Application.__table__.columns}
    unknown = insert_columns - model_columns
    assert not unknown, (
        f"Migration 026: l'INSERT référence des colonnes inconnues "
        f"du modèle Application : {sorted(unknown)}."
    )
