from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import init_db, async_session_maker
from app.models import User, Application
from app.auth import get_password_hash
from app.config import get_settings
from app.routes import auth, users, apps

settings = get_settings()


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


async def create_default_app():
    """Crée l'application Salarie par défaut"""
    async with async_session_maker() as db:
        result = await db.execute(select(Application).where(Application.slug == "salarie"))
        if not result.scalar_one_or_none():
            app = Application(
                name="API Salariés",
                slug="salarie",
                description="Gestion des salariés et contrats",
                icon="bi-people-fill",
                color="#198754",
                api_url="http://host.docker.internal:18087",
                frontend_url="http://localhost:4178",
                callback_url="http://localhost:4178/callback",
                is_active=True,
                requires_auth=True
            )
            db.add(app)
            await db.commit()
            
            # Associer l'admin à cette app
            admin_result = await db.execute(select(User).where(User.is_admin == True))
            admin = admin_result.scalar_one_or_none()
            if admin:
                app_result = await db.execute(
                    select(Application)
                    .options(selectinload(Application.users))
                    .where(Application.slug == "salarie")
                )
                app_obj = app_result.scalar_one()
                app_obj.users.append(admin)
                await db.commit()
            
            print("✅ Application 'API Salariés' créée")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 Démarrage du Hub d'Authentification...")
    await init_db()
    await create_default_admin()
    await create_default_app()
    print("✅ Hub prêt !")
    yield
    # Shutdown
    print("👋 Arrêt du Hub...")


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


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "hub-auth"}


@app.get("/")
async def root():
    return {
        "message": "Hub d'Authentification",
        "docs": "/docs",
        "health": "/health"
    }
