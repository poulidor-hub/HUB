from contextlib import asynccontextmanager
import asyncio
import time
import uuid
import traceback
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import init_db, async_session_maker, apply_migrations
from app.models import User, ErrorLog
from app.auth import get_password_hash, cleanup_expired_tokens
from app.config import get_settings
from app.routes import auth, users, apps, service_accounts, backup, audit, docs, events, app_backups

settings = get_settings()

# Variables globales pour contrôler les tâches périodiques
_cleanup_task = None
_docker_sync_task = None
_log_purge_task = None
_backup_scheduler_task = None


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


async def periodic_docker_sync():
    """Tâche périodique pour synchroniser l'état des apps avec Docker"""
    interval = settings.docker_sync_interval
    
    if interval <= 0:
        print("🐳 Synchronisation Docker désactivée (DOCKER_SYNC_INTERVAL=0)")
        return
    
    print(f"🐳 Synchronisation Docker activée (toutes les {interval}s)")
    
    # Attendre un peu avant le premier scan (laisser les apps démarrer)
    await asyncio.sleep(30)
    
    while True:
        try:
            from app.routes.apps import auto_sync_docker_applications
            async with async_session_maker() as db:
                result = await auto_sync_docker_applications(db)
                if result.get("changes", 0) > 0:
                    print(f"🐳 Sync Docker: {result['changes']} application(s) mise(s) à jour")
            
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            print("🛑 Tâche de synchronisation Docker arrêtée")
            break
        except Exception as e:
            print(f"⚠️ Erreur lors de la synchronisation Docker: {e}")
            await asyncio.sleep(interval)


async def periodic_log_purge():
    """Tâche périodique pour purger les anciens logs d'audit et d'erreurs"""
    from app.routes.audit import purge_old_logs
    
    interval_hours = settings.log_purge_interval_hours
    audit_days = settings.audit_retention_days
    error_days = settings.error_retention_days
    
    if audit_days <= 0 and error_days <= 0:
        print("🗑️ Purge des logs désactivée (rétention = 0)")
        return
    
    print(f"🗑️ Purge des logs activée (audit: {audit_days}j, errors: {error_days}j, intervalle: {interval_hours}h)")
    
    # Attendre un peu avant la première purge
    await asyncio.sleep(60)
    
    while True:
        try:
            async with async_session_maker() as db:
                result = await purge_old_logs(db, audit_days, error_days)
                if result["audit_deleted"] > 0 or result["errors_deleted"] > 0:
                    print(f"🗑️ Purge logs: {result['audit_deleted']} audit, {result['errors_deleted']} erreurs supprimés")
            
            await asyncio.sleep(interval_hours * 3600)
        except asyncio.CancelledError:
            print("🛑 Tâche de purge des logs arrêtée")
            break
        except Exception as e:
            print(f"⚠️ Erreur lors de la purge des logs: {e}")
            await asyncio.sleep(interval_hours * 3600)


async def periodic_backup_scheduler():
    """
    Tâche périodique pour vérifier et déclencher les backups planifiés.
    Vérifie toutes les minutes si un backup doit être lancé.
    """
    from croniter import croniter
    from sqlalchemy import select
    from app.models import Application
    from datetime import datetime
    
    print("📅 Scheduler de backups démarré")
    
    # Attendre un peu avant le premier check
    await asyncio.sleep(30)
    
    while True:
        try:
            async with async_session_maker() as db:
                # Récupérer les apps avec backup scheduled
                result = await db.execute(
                    select(Application)
                    .where(Application.backup_mode == "scheduled")
                    .where(Application.backup_cron.isnot(None))
                    .where(Application.is_active == True)
                )
                apps = result.scalars().all()
                
                now = datetime.utcnow()
                
                for app in apps:
                    try:
                        # Vérifier si c'est l'heure du backup
                        if app.next_backup_at and app.next_backup_at <= now:
                            print(f"📦 Déclenchement backup planifié pour {app.slug}")
                            
                            # Mettre à jour le statut
                            app.last_backup_status = "running"
                            
                            # Calculer le prochain backup
                            cron = croniter(app.backup_cron, now)
                            app.next_backup_at = cron.get_next(datetime)
                            
                            await db.commit()
                            
                            # TODO: Déclencher réellement le backup
                            # Pour l'instant, on log juste l'événement
                            # L'implémentation complète nécessiterait d'appeler l'API de l'app
                            
                            print(f"📅 Prochain backup pour {app.slug}: {app.next_backup_at}")
                        
                        # Si next_backup_at n'est pas défini, le calculer
                        elif not app.next_backup_at and app.backup_cron:
                            cron = croniter(app.backup_cron, now)
                            app.next_backup_at = cron.get_next(datetime)
                            await db.commit()
                            print(f"📅 Prochain backup programmé pour {app.slug}: {app.next_backup_at}")
                            
                    except Exception as e:
                        print(f"⚠️ Erreur scheduler backup pour {app.slug}: {e}")
            
            # Vérifier toutes les 60 secondes
            await asyncio.sleep(60)
            
        except asyncio.CancelledError:
            print("🛑 Scheduler de backups arrêté")
            break
        except Exception as e:
            print(f"⚠️ Erreur dans le scheduler de backups: {e}")
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cleanup_task, _docker_sync_task, _log_purge_task, _backup_scheduler_task
    
    # Startup
    print("🚀 Démarrage du Hub d'Authentification...")
    await init_db()
    await apply_migrations()  # Appliquer les migrations SQL si nécessaires
    await create_default_admin()
    
    # Démarrer la tâche de nettoyage périodique
    _cleanup_task = asyncio.create_task(periodic_token_cleanup())
    print("🧹 Tâche de nettoyage des tokens démarrée")
    
    # Démarrer la tâche de synchronisation Docker
    _docker_sync_task = asyncio.create_task(periodic_docker_sync())
    
    # Démarrer la tâche de purge des logs
    _log_purge_task = asyncio.create_task(periodic_log_purge())
    
    # Démarrer le scheduler de backups
    _backup_scheduler_task = asyncio.create_task(periodic_backup_scheduler())
    
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
    if _docker_sync_task:
        _docker_sync_task.cancel()
        try:
            await _docker_sync_task
        except asyncio.CancelledError:
            pass
    if _log_purge_task:
        _log_purge_task.cancel()
        try:
            await _log_purge_task
        except asyncio.CancelledError:
            pass
    if _backup_scheduler_task:
        _backup_scheduler_task.cancel()
        try:
            await _backup_scheduler_task
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


# =============================================================================
# MIDDLEWARE DE CONTEXTE DE REQUÊTE
# =============================================================================

@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """
    Middleware qui enrichit chaque requête avec un contexte de traçabilité :
    - request_id: UUID unique pour tracer la requête
    - source_app_slug: Application source (header X-Slug)
    - start_time: Horodatage de début pour calculer la durée
    """
    # Générer un request_id unique
    request.state.request_id = str(uuid.uuid4())
    
    # Extraire le X-Slug (application source)
    request.state.source_app_slug = request.headers.get("X-Slug")
    
    # Horodatage de début
    request.state.start_time = time.time()
    
    # Exécuter la requête
    response = await call_next(request)
    
    # Ajouter le request_id dans les headers de réponse (pour debug)
    response.headers["X-Request-ID"] = request.state.request_id
    
    return response


# =============================================================================
# GESTIONNAIRE D'ERREURS GLOBAL
# =============================================================================


async def _log_error_to_db(
    request: Request,
    error_code: int,
    error_type: str,
    error_message: str,
    stack: str = None
):
    """
    Fonction helper pour logger une erreur dans la table error_logs.
    Gère les erreurs silencieusement pour ne pas interférer avec la réponse.
    """
    try:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        source_app_slug = getattr(request.state, "source_app_slug", None)
        
        async with async_session_maker() as db:
            error_log = ErrorLog(
                request_id=request_id,
                error_code=error_code,
                error_type=error_type,
                error_message=error_message[:500] if error_message else None,
                user_id=None,
                service_account_id=None,
                source_app_slug=source_app_slug,
                request_method=request.method,
                request_path=str(request.url.path)[:255],
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent", "")[:255],
                stack_trace=stack
            )
            db.add(error_log)
            await db.commit()
            print(f"📝 Erreur {error_code} loggée: {request.url.path}")
    except Exception as log_error:
        print(f"⚠️ Erreur lors du logging d'erreur: {log_error}")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """
    Intercepte TOUTES les HTTPException (FastAPI et Starlette).
    C'est ce handler qui capture les erreurs 4xx et 5xx.
    """
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    error_code = exc.status_code
    error_message = str(exc.detail) if exc.detail else "Erreur HTTP"
    error_type = _get_error_type(error_code)
    
    # Logger toutes les erreurs HTTP (4xx et 5xx)
    if error_code >= 400:
        await _log_error_to_db(request, error_code, error_type, error_message)
    
    return JSONResponse(
        status_code=error_code,
        content={"detail": error_message, "request_id": request_id},
        headers=exc.headers
    )


def _sanitize_error_for_json(obj):
    """
    Convertit récursivement les objets non-sérialisables en JSON.
    Notamment les bytes qui apparaissent dans les erreurs de validation.
    """
    if isinstance(obj, bytes):
        try:
            return obj.decode('utf-8', errors='replace')
        except Exception:
            return "<binary data>"
    elif isinstance(obj, dict):
        return {k: _sanitize_error_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_sanitize_error_for_json(item) for item in obj]
    return obj


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Intercepte les erreurs de validation Pydantic (422) et les log.
    """
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    error_code = 422
    error_type = "validation"
    
    # Formater le message d'erreur
    errors = exc.errors()
    error_messages = []
    for err in errors:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        error_messages.append(f"{loc}: {msg}")
    error_message = "; ".join(error_messages)
    
    # Logger l'erreur
    await _log_error_to_db(request, error_code, error_type, error_message)
    
    # Sanitize les erreurs pour éviter les problèmes de sérialisation JSON
    # (bytes, etc. venant du body brut)
    sanitized_errors = _sanitize_error_for_json(errors)
    
    return JSONResponse(
        status_code=error_code,
        content={
            "detail": sanitized_errors,
            "request_id": request_id
        }
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Intercepte toutes les exceptions NON GÉRÉES (erreurs 500 internes).
    Les HTTPException sont gérées par http_exception_handler ci-dessus.
    """
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    error_code = 500
    error_message = str(exc)
    error_type = "internal"
    stack = traceback.format_exc()
    
    # Logger l'erreur avec la stack trace
    await _log_error_to_db(request, error_code, error_type, error_message, stack)
    
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Erreur interne du serveur",
            "request_id": request_id
        }
    )


def _get_error_type(status_code: int) -> str:
    """Détermine le type d'erreur selon le code HTTP"""
    if status_code == 401:
        return "authentication"
    elif status_code == 403:
        return "authorization"
    elif status_code == 404:
        return "not_found"
    elif status_code == 422:
        return "validation"
    elif status_code == 429:
        return "rate_limit"
    elif 400 <= status_code < 500:
        return "client_error"
    elif status_code >= 500:
        return "internal"
    return "unknown"


# Routes
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(apps.router)
app.include_router(service_accounts.router)
app.include_router(backup.router)
app.include_router(audit.router)
app.include_router(docs.router)
app.include_router(events.router)
app.include_router(app_backups.router)


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
