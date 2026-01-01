from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.database import init_db, async_session_maker
from app.models import User
from app.auth import get_password_hash, cleanup_expired_tokens
from app.config import get_settings
from app.routes import auth, users, apps, service_accounts, backup, audit, docs

settings = get_settings()

# Variable globale pour contrôler la tâche de nettoyage
_cleanup_task = None


async def create_default_admin():
    """Crée l'admin par défaut si inexistant"""
    async with async_session_maker() as db:
        result = await db.execute(select(User).where(User.username == settings.admin_username))
        if not result.scalar_one_or_none():
            admin = User(
                username=settings.admin_username,
                email=settings.admin_email,
                hashed_password=get_password_hash(settings.admin_password),
                is_admin=True
            )
            db.add(admin)
            await db.commit()
            print(f"✅ Admin créé: {settings.admin_username}")


async def periodic_token_cleanup():
    """Tâche périodique pour nettoyer les tokens expirés (toutes les heures)"""
    while True:
        try:
            await asyncio.sleep(3600)  # Attendre 1 heure
            async with async_session_maker() as db:
                deleted_count = await cleanup_expired_tokens(db)
                if deleted_count > 0:
                    print(f"🧹 Nettoyage: {deleted_count} tokens expirés supprimés")
        except asyncio.CancelledError:
            print("🛑 Tâche de nettoyage arrêtée")
            break
        except Exception as e:
            print(f"⚠️ Erreur lors du nettoyage des tokens: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cleanup_task
    
    # Startup
    print("🚀 Démarrage du Hub d'Authentification...")
    await init_db()
    await create_default_admin()
    
    # Démarrer la tâche de nettoyage périodique
    _cleanup_task = asyncio.create_task(periodic_token_cleanup())
    print("🧹 Tâche de nettoyage des tokens démarrée")
    
    print("✅ Hub prêt !")
    yield
    
    # Shutdown
    print("👋 Arrêt du Hub...")
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Hub d'Authentification",
    description="Portail centralisé d'authentification avec gestion des applications",
    version="1.0.0",
    lifespan=lifespan
)

# CORS - autoriser tous les origines (réseau local)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # False avec origins=* pour éviter les erreurs
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Routes
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(apps.router)
app.include_router(service_accounts.router)
app.include_router(backup.router)
app.include_router(audit.router)
app.include_router(docs.router)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "hub-auth"}


@app.get("/api/config/hub-host")
async def get_hub_host():
    """Retourne l'IP/hostname du Hub pour la configuration des applications"""
    return {
        "host": settings.get_hub_host(),
        "configured": bool(settings.hub_host),
        "message": "IP auto-détectée" if not settings.hub_host else "IP configurée"
    }


@app.get("/")
async def root():
    return {
        "message": "Hub d'Authentification",
        "docs": "/docs",
        "health": "/health"
    }
