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
                    statement = _strip_sql_comments(statement)
                    if statement:
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
                    statement = _strip_sql_comments(statement)
                    if statement:
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
                    statement = _strip_sql_comments(statement)
                    if statement:
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
                    statement = _strip_sql_comments(statement)
                    if statement:
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 004 statement ignoré: {e}")
                logger.info("✅ Migration 004 appliquée")
        
        # Vérifier si la migration 005 est nécessaire (remote app fields)
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'applications' AND column_name = 'is_remote'
        """))
        if not result.fetchone():
            logger.info("📦 Application de la migration 005_add_remote_app_fields...")
            migration_file = migrations_dir / "005_add_remote_app_fields.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for statement in sql.split(';'):
                    statement = _strip_sql_comments(statement)
                    if statement:
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 005 statement ignoré: {e}")
                logger.info("✅ Migration 005 appliquée")
        
        # Vérifier si la migration 006 est nécessaire (app_backups table + scheduling fields)
        result = await conn.execute(text("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'app_backups'
        """))
        if not result.fetchone():
            logger.info("📦 Application de la migration 006_add_app_backups...")
            migration_file = migrations_dir / "006_add_app_backups.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for statement in sql.split(';'):
                    statement = _strip_sql_comments(statement)
                    if statement:
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 006 statement ignoré: {e}")
                logger.info("✅ Migration 006 appliquée")
        
        # Vérifier si la migration 007 est nécessaire (preserve backups on app delete)
        # On vérifie si la contrainte FK utilise SET NULL (au lieu de CASCADE)
        result = await conn.execute(text("""
            SELECT confdeltype FROM pg_constraint 
            WHERE conname = 'app_backups_app_id_fkey'
        """))
        row = result.fetchone()
        if row and row[0] != 'n':  # 'n' = SET NULL, 'c' = CASCADE
            logger.info("📦 Application de la migration 007_preserve_backups_on_app_delete...")
            migration_file = migrations_dir / "007_preserve_backups_on_app_delete.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for statement in sql.split(';'):
                    statement = _strip_sql_comments(statement)
                    if statement:
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 007 statement ignoré: {e}")
                logger.info("✅ Migration 007 appliquée")
        
        # Vérifier si la migration 008 est nécessaire (profiles)
        result = await conn.execute(text("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'profiles'
        """))
        if not result.fetchone():
            logger.info("📦 Application de la migration 008_add_profiles...")
            migration_file = migrations_dir / "008_add_profiles.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for statement in sql.split(';'):
                    statement = _strip_sql_comments(statement)
                    if statement:
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 008 statement ignoré: {e}")
                logger.info("✅ Migration 008 appliquée")
        else:
            # Vérifier si la colonne profile_id existe sur users
            result = await conn.execute(text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'users' AND column_name = 'profile_id'
            """))
            if not result.fetchone():
                logger.info("📦 Ajout de la colonne profile_id sur users...")
                try:
                    await conn.execute(text(
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL"
                    ))
                    await conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS idx_users_profile_id ON users(profile_id)"
                    ))
                    logger.info("✅ Colonne profile_id ajoutée")
                except Exception as e:
                    logger.warning(f"Ajout profile_id ignoré: {e}")

        # Vérifier si la migration 009 est nécessaire (api_endpoints)
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'applications' AND column_name = 'api_endpoints'
        """))
        if not result.fetchone():
            logger.info("📦 Application de la migration 009_add_api_endpoints...")
            migration_file = migrations_dir / "009_add_api_endpoints.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for statement in sql.split(';'):
                    statement = _strip_sql_comments(statement)
                    if statement:
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 009 statement ignoré: {e}")
                logger.info("✅ Migration 009 appliquée")

        # Vérifier si la migration 010 est nécessaire (source sur user_app_roles)
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'user_app_roles' AND column_name = 'source'
        """))
        if not result.fetchone():
            logger.info("📦 Application de la migration 010_add_user_app_role_source...")
            migration_file = migrations_dir / "010_add_user_app_role_source.sql"
            if migration_file.exists():
                sql = migration_file.read_text()
                for statement in sql.split(';'):
                    statement = _strip_sql_comments(statement)
                    if statement:
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            logger.warning(f"Migration 010 statement ignoré: {e}")
                logger.info("✅ Migration 010 appliquée")
