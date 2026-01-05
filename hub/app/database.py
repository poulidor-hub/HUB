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


async def apply_migrations():
    """
    Applique les migrations SQL si nécessaires.
    Vérifie d'abord si les colonnes existent avant d'appliquer.
    """
    migrations_dir = Path("/code/migrations")
    if not migrations_dir.exists():
        # Mode local
        migrations_dir = Path(__file__).parent.parent / "migrations"
    
    if not migrations_dir.exists():
        logger.warning("⚠️ Dossier migrations non trouvé")
        return
    
    async with engine.begin() as conn:
        # Vérifier si la migration 001 est nécessaire (must_change_password)
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'users' AND column_name = 'must_change_password'
        """))
        if not result.fetchone():
            logger.info("📦 Application de la migration 001_add_must_change_password...")
            migration_file = migrations_dir / "001_add_must_change_password.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for statement in sql.split(';'):
                    statement = statement.strip()
                    if statement and not statement.startswith('--'):
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 001 statement ignoré: {e}")
                logger.info("✅ Migration 001 appliquée")
        
        # Vérifier si la migration 002 est nécessaire (approval_status)
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'applications' AND column_name = 'approval_status'
        """))
        if not result.fetchone():
            logger.info("📦 Application de la migration 002_add_app_approval_status...")
            migration_file = migrations_dir / "002_add_app_approval_status.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for statement in sql.split(';'):
                    statement = statement.strip()
                    if statement and not statement.startswith('--'):
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 002 statement ignoré: {e}")
                logger.info("✅ Migration 002 appliquée")
        
        # Vérifier si la migration 003 est nécessaire (enrichissement audit_logs + error_logs)
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'audit_logs' AND column_name = 'request_id'
        """))
        if not result.fetchone():
            logger.info("📦 Application de la migration 003_add_audit_enrichment...")
            migration_file = migrations_dir / "003_add_audit_enrichment.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for statement in sql.split(';'):
                    statement = statement.strip()
                    if statement and not statement.startswith('--'):
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 003 statement ignoré: {e}")
                logger.info("✅ Migration 003 appliquée")
        
        # Vérifier si la migration 004 est nécessaire (can_change_password)
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'users' AND column_name = 'can_change_password'
        """))
        if not result.fetchone():
            logger.info("📦 Application de la migration 004_add_can_change_password...")
            migration_file = migrations_dir / "004_add_can_change_password.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for statement in sql.split(';'):
                    statement = statement.strip()
                    if statement and not statement.startswith('--'):
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 004 statement ignoré: {e}")
                logger.info("✅ Migration 004 appliquée")
