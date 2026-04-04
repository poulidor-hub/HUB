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
    pool_size=10,
    max_overflow=20
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


def _strip_sql_comments(sql_block: str) -> str:
    """Retire les lignes de commentaires SQL (--) d'un bloc et renvoie le SQL nettoyé."""
    lines = [line for line in sql_block.splitlines() if not line.strip().startswith('--')]
    return '\n'.join(lines).strip()


async def apply_migrations():
    """
    Applique automatiquement les migrations SQL depuis le dossier migrations/.
    Utilise une table schema_migrations pour suivre les migrations appliquées.
    Les fichiers doivent suivre le format NNN_description.sql (ex: 001_add_column.sql).
    """
    migrations_dir = Path("/code/migrations")
    if not migrations_dir.exists():
        # Mode local (hors Docker)
        migrations_dir = Path(__file__).parent.parent / "migrations"

    if not migrations_dir.exists():
        logger.warning("⚠️ Dossier migrations non trouvé")
        return

    # Lister et trier les fichiers de migration
    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        return

    async with engine.begin() as conn:
        # Créer la table de suivi si elle n'existe pas
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(10) PRIMARY KEY,
                filename VARCHAR(255) NOT NULL,
                applied_at TIMESTAMP DEFAULT NOW()
            )
        """))

        # Vérifier si c'est une migration depuis l'ancien système manuel
        result = await conn.execute(text("SELECT COUNT(*) FROM schema_migrations"))
        count = result.scalar()

        if count == 0:
            # Vérifier si la base a déjà des tables (upgrade depuis le système manuel)
            result = await conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'users'
            """))
            if result.fetchone():
                # BD existante → marquer toutes les migrations actuelles comme appliquées
                logger.info("📦 Détection d'une BD existante, indexation des migrations déjà appliquées...")
                for mf in migration_files:
                    version = mf.name.split('_')[0]
                    await conn.execute(text(
                        "INSERT INTO schema_migrations (version, filename) VALUES (:v, :f) ON CONFLICT DO NOTHING"
                    ), {"v": version, "f": mf.name})
                logger.info(f"✅ {len(migration_files)} migrations indexées")
                return

        # Récupérer les migrations déjà appliquées
        result = await conn.execute(text("SELECT version FROM schema_migrations"))
        applied = {row[0] for row in result.fetchall()}

        # Appliquer les nouvelles migrations dans l'ordre
        for mf in migration_files:
            version = mf.name.split('_')[0]
            if version in applied:
                continue

            logger.info(f"📦 Application de la migration {mf.name}...")
            sql = mf.read_text()
            for statement in sql.split(';'):
                statement = _strip_sql_comments(statement)
                if statement:
                    try:
                        await conn.execute(text(statement))
                    except Exception as e:
                        logger.warning(f"Migration {mf.name} statement ignoré: {e}")

            await conn.execute(text(
                "INSERT INTO schema_migrations (version, filename) VALUES (:v, :f)"
            ), {"v": version, "f": mf.name})
            logger.info(f"✅ Migration {mf.name} appliquée")
