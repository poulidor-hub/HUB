# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text
from app.config import get_settings
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10
)


# Activer les foreign keys pour SQLite (nécessaire pour ON DELETE CASCADE)
@event.listens_for(engine.sync_engine, "connect")
def enable_sqlite_fk(dbapi_connection, connection_record):
    if "sqlite" in settings.database_url:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _ensure_user_identity_columns() -> None:
    """Ajoute les colonnes identité User (uuid, prenom, nom) si absentes.

    Idempotent — safe sur base vierge (create_all les a déjà créées) comme
    sur base existante. Évite le piège ALTER TABLE du runner migrations/*.sql
    sur installation fraîche (colonne déjà présente).
    """
    from sqlalchemy import inspect, text

    def _existing_columns(sync_conn):
        return {col["name"] for col in inspect(sync_conn).get_columns("users")}

    async with engine.begin() as conn:
        existing = await conn.run_sync(_existing_columns)
        for col, ddl in (
            ("uuid", "VARCHAR(36)"),
            ("oidc_sub", "VARCHAR(50)"),
            ("prenom", "VARCHAR(100)"),
            ("nom", "VARCHAR(100)"),
        ):
            if col not in existing:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))
                logger.info("Colonne users.%s ajoutée", col)
        # Index unique sur uuid (CREATE INDEX est sûr sur SQLite et Postgres)
        await conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_uuid ON users (uuid)")
        )
        await conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_oidc_sub ON users (oidc_sub)")
        )


async def _backfill_user_uuids() -> None:
    """Assigne un uuid à chaque utilisateur existant qui n'en a pas."""
    from sqlalchemy import select

    from app.models import User  # import local : évite la circularité models<->database

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.uuid.is_(None)))
        users = result.scalars().all()
        if not users:
            return
        import uuid as _uuid

        for user in users:
            user.uuid = str(_uuid.uuid4())
        await session.commit()
        logger.info("Backfill users.uuid : %d utilisateur(s) migré(s)", len(users))


async def backfill_dns_names() -> int:
    """Chantier B : dns_name = slug pour les applications sans nom de routage.

    Idempotent : renvoie le nombre d'applications modifiées (0 au 2e appel).
    Appelé au lifespan, après les migrations (colonne dns_name déjà présente).
    """
    from sqlalchemy import select

    from app.models import Application  # import local : évite la circularité

    async with async_session_maker() as session:
        result = await session.execute(
            select(Application).where(Application.dns_name.is_(None))
        )
        apps = result.scalars().all()
        if not apps:
            return 0
        for app in apps:
            app.dns_name = app.slug
        await session.commit()
        logger.info("Backfill applications.dns_name : %d app(s) migrée(s)", len(apps))
        return len(apps)


async def _backfill_user_oidc_subs() -> None:
    """Assigne oidc_sub = username aux lignes legacy ; fallback uuid si collision
    (username recyclé après rename, déjà détenu comme oidc_sub d'un autre)."""
    from sqlalchemy import select

    from app.models import User

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.oidc_sub.is_(None)))
        users = result.scalars().all()
        if not users:
            return
        import uuid as _uuid

        for user in users:
            taken = await session.execute(
                select(User.id).where(User.oidc_sub == user.username, User.id != user.id)
            )
            user.oidc_sub = (
                user.username if taken.scalar_one_or_none() is None else str(_uuid.uuid4())
            )
        await session.commit()
        logger.info("Backfill users.oidc_sub : %d utilisateur(s) migré(s)", len(users))


def _strip_sql_comments(sql_block: str) -> str:
    """Retire les lignes de commentaires SQL (--) d'un bloc et renvoie le SQL nettoyé."""
    lines = [line for line in sql_block.splitlines() if not line.strip().startswith('--')]
    return '\n'.join(lines).strip()


class MigrationError(RuntimeError):
    """Levée quand une migration SQL échoue. Stoppe le démarrage de l'application."""


MIGRATION_ALIASES = {
    # Certaines instances ont enregistré la migration message sous le préfixe
    # 020 avant que ce numéro soit réservé à l'historique OIDC.
    ("020", "020_add_message_lifecycle_and_engagement.sql"): (
        "021",
        "021_add_message_lifecycle_and_engagement.sql",
    ),
}


async def apply_migrations():
    """
    Applique automatiquement les migrations SQL depuis le dossier migrations/.

    Garanties :
    - Une transaction par migration : un échec ⇒ rollback complet de cette
      migration, qui n'est PAS marquée comme appliquée et sera réessayée au
      prochain boot après correction.
    - Fail-fast : toute erreur SQL lève une MigrationError et stoppe le boot
      (on refuse de servir l'API avec un schéma incohérent).
    - Détection de collision de version : deux fichiers avec la même version
      (ex. deux fichiers `020_*.sql`) lèvent une erreur explicite.
    - Détection de version déjà appliquée mais introuvable sur disque :
      log d'avertissement (cas typique d'une migration renommée/supprimée).

    Les fichiers doivent suivre le format NNN_description.sql.
    """
    migrations_dir = Path("/code/migrations")
    if not migrations_dir.exists():
        # Mode local (hors Docker)
        migrations_dir = Path(__file__).parent.parent / "migrations"

    if not migrations_dir.exists():
        logger.warning("⚠️ Dossier migrations non trouvé")
        return

    # Lister, trier et vérifier l'unicité des versions
    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        return

    by_version: dict[str, Path] = {}
    for mf in migration_files:
        version = mf.name.split('_')[0]
        if version in by_version:
            raise MigrationError(
                f"Collision de version de migration : {by_version[version].name} "
                f"et {mf.name} portent le même préfixe '{version}'."
            )
        by_version[version] = mf

    # Préparer la table de suivi et lire l'état actuel (transaction courte)
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(10) PRIMARY KEY,
                filename VARCHAR(255) NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        result = await conn.execute(
            text("SELECT version, filename FROM schema_migrations")
        )
        applied = {row[0]: row[1] for row in result.fetchall()}

    applied_versions = set(applied)
    for old_migration, current_migration in MIGRATION_ALIASES.items():
        old_version, old_filename = old_migration
        current_version, current_filename = current_migration
        current_path = by_version.get(current_version)
        if (
            applied.get(old_version) == old_filename
            and current_path is not None
            and current_path.name == current_filename
        ):
            applied_versions.add(current_version)
            logger.info(
                "↔️ Migration %s déjà couverte par l'historique %s",
                current_filename,
                old_filename,
            )

    # Avertir si la DB référence des migrations qui n'existent plus sur disque
    missing_on_disk = sorted(set(applied) - set(by_version))
    if missing_on_disk:
        logger.warning(
            "⚠️ Migrations enregistrées en DB mais absentes sur disque : %s "
            "(versions à ne pas réutiliser pour de nouvelles migrations).",
            ", ".join(f"{v} ({applied[v]})" for v in missing_on_disk),
        )

    pending = [
        (v, by_version[v])
        for v in sorted(by_version)
        if v not in applied_versions
    ]
    if not pending:
        logger.info("✅ Schéma à jour (%d migrations appliquées)", len(applied))
        return

    logger.info(
        "📦 %d migration(s) à appliquer : %s",
        len(pending), ", ".join(v for v, _ in pending),
    )

    # Une transaction par migration → rollback isolé en cas d'échec
    for version, mf in pending:
        logger.info("📦 Application de la migration %s...", mf.name)
        sql = mf.read_text()
        statements = [
            s for s in (_strip_sql_comments(stmt) for stmt in sql.split(';')) if s
        ]
        try:
            async with engine.begin() as conn:
                for statement in statements:
                    await conn.execute(text(statement))
                await conn.execute(
                    text(
                        "INSERT INTO schema_migrations (version, filename) "
                        "VALUES (:v, :f)"
                    ),
                    {"v": version, "f": mf.name},
                )
        except Exception as e:
            logger.error(
                "❌ Échec de la migration %s : %s. "
                "Aucune modification appliquée pour cette migration.",
                mf.name, e,
            )
            raise MigrationError(
                f"Migration {mf.name} a échoué : {e}"
            ) from e
        logger.info("✅ Migration %s appliquée", mf.name)
