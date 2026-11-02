from contextlib import asynccontextmanager
import asyncio
import re
import time
import uuid
import traceback
from fastapi import FastAPI, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import (
    init_db,
    async_session_maker,
    apply_migrations,
    _ensure_user_identity_columns,
    _backfill_user_uuids,
    _backfill_user_oidc_subs,
    backfill_dns_names,
)
from app.outbox import outbox_service
from app.models import User, ErrorLog, utcnow
from app.auth import get_password_hash, cleanup_expired_tokens, get_current_admin, generate_oidc_sub
from app.config import get_settings, build_cors_config, get_instance_id
from app.routes import auth, users, apps, service_accounts, audit, docs, events, app_backups, profiles, apps_health, messages, account_requests, snapshots, vpn, oidc, github_catalog, hub_restore, tags, app_groups, app_deploy, workshop, jwt_keys, app_certs, pki_ca, admin_backup, atelier, vault
from app.routes import settings as settings_routes
from app.routes import network as network_routes
from app.routes import app_recreate
from app.routes import routing as routing_routes
from app.routes import app_socle_check

import logging

from app.logging_config import setup_logging

setup_logging()

logger = logging.getLogger(__name__)


class _SuppressLegacySSE401Filter(logging.Filter):
    """Filtre les logs d'accès uvicorn 401 sur /api/events/stream.

    Les applications legacy tentent en boucle d'ouvrir le flux SSE sans token
    valide, ce qui inonde les logs de lignes `GET /api/events/stream ... 401`.
    Ce bruit est attendu et sans intérêt : on l'ignore silencieusement.
    Les autres 401 (et tout autre statut) restent loggés normalement.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        method, path, status_code = args[1], args[2], args[4]
        if (
            status_code == 401
            and method == "GET"
            and str(path).split("?", 1)[0] == "/api/events/stream"
        ):
            return False
        return True


logging.getLogger("uvicorn.access").addFilter(_SuppressLegacySSE401Filter())

settings = get_settings()

# Variables globales pour controler les taches periodiques
_cleanup_task = None
_docker_sync_task = None
_log_purge_task = None
_backup_scheduler_task = None
_snapshot_scheduler_task = None
_msg_dispatch_task = None
_outbox_worker_task = None
_github_token_check_task = None


async def create_default_admin():
    """Crée l'admin par défaut si inexistant et force le changement de mot de passe par défaut"""
    if not settings.admin_password:
        logger.warning("⚠️  ADMIN_PASSWORD non défini ! L'admin par défaut ne sera pas créé.")
        return
    
    async with async_session_maker() as db:
        result = await db.execute(select(User).where(User.username == settings.admin_username))
        existing_admin = result.scalar_one_or_none()
        
        if not existing_admin:
            admin = User(
                username=settings.admin_username,
                email=settings.admin_email,
                hashed_password=get_password_hash(settings.admin_password),
                is_admin=True,
                must_change_password=True,
                # Le backfill lifespan court AVANT ce bootstrap : sans sub explicite,
                # l'admin resterait le seul user à oidc_sub NULL jusqu'au prochain boot.
                oidc_sub=await generate_oidc_sub(db, settings.admin_username)
            )
            db.add(admin)
            await db.commit()
            logger.info(f"✅ Admin créé: {settings.admin_username}")
        else:
            # Vérifier si le mot de passe par défaut est encore utilisé
            from app.auth import verify_password
            if verify_password("admin123", existing_admin.hashed_password):
                if not existing_admin.must_change_password:
                    existing_admin.must_change_password = True
                    await db.commit()
                logger.warning("⚠️  Mot de passe admin par défaut détecté ! Changez-le dès que possible.")


async def periodic_token_cleanup():
    """Tâche périodique pour nettoyer les tokens expirés (toutes les heures)"""
    while True:
        try:
            await asyncio.sleep(3600)  # Attendre 1 heure
            async with async_session_maker() as db:
                deleted_count = await cleanup_expired_tokens(db)
                if deleted_count > 0:
                    logger.info(f"🧹 Nettoyage: {deleted_count} tokens expirés supprimés")
        except asyncio.CancelledError:
            logger.info("🛑 Tâche de nettoyage arrêtée")
            break
        except Exception as e:
            logger.warning(f"⚠️ Erreur lors du nettoyage des tokens: {e}")


async def periodic_docker_sync():
    """Tâche périodique pour synchroniser l'état des apps avec Docker"""
    if not settings.docker_scan_enabled:
        logger.info("🐳 Scan Docker désactivé (DOCKER_SCAN_ENABLED=false)")
        return

    interval = settings.docker_sync_interval

    if interval <= 0:
        logger.info("🐳 Synchronisation Docker désactivée (DOCKER_SYNC_INTERVAL=0)")
        return
    
    logger.info(f"🐳 Synchronisation Docker activée (toutes les {interval}s)")
    
    # Attendre un peu avant le premier scan (laisser les apps démarrer)
    await asyncio.sleep(30)
    
    while True:
        try:
            from app.routes.apps import auto_sync_docker_applications
            async with async_session_maker() as db:
                result = await auto_sync_docker_applications(db)
                if result.get("changes", 0) > 0:
                    logger.info(f"🐳 Sync Docker: {result['changes']} application(s) mise(s) à jour")
            
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("🛑 Tâche de synchronisation Docker arrêtée")
            break
        except Exception as e:
            logger.warning(f"⚠️ Erreur lors de la synchronisation Docker: {e}")
            await asyncio.sleep(interval)


async def periodic_log_purge():
    """Tâche périodique pour purger les anciens logs d'audit, d'erreurs et de messages"""
    from app.routes.audit import purge_old_logs
    from app.routes.messages import purge_old_messages
    
    interval_hours = settings.log_purge_interval_hours
    audit_days = settings.audit_retention_days
    error_days = settings.error_retention_days
    message_days = settings.message_retention_days
    
    if audit_days <= 0 and error_days <= 0 and message_days <= 0:
        logger.info("🗑️ Purge des logs désactivée (rétention = 0)")
        return
    
    logger.info(f"🗑️ Purge des logs activée (audit: {audit_days}j, errors: {error_days}j, messages: {message_days}j, intervalle: {interval_hours}h)")
    
    # Attendre un peu avant la première purge
    await asyncio.sleep(60)
    
    while True:
        try:
            async with async_session_maker() as db:
                result = await purge_old_logs(db, audit_days, error_days)
                if result["audit_deleted"] > 0 or result["errors_deleted"] > 0:
                    logger.info(f"🗑️ Purge logs: {result['audit_deleted']} audit, {result['errors_deleted']} erreurs supprimés")
                
                # Purge des messages
                msg_deleted = await purge_old_messages(db, message_days)
                if msg_deleted > 0:
                    logger.info(f"🗑️ Purge messages: {msg_deleted} message(s) supprimé(s)")

                # Purge de l'historique des opérations atelier
                try:
                    from app.workshop_service import purge_workshop_operations
                    ws_deleted = await purge_workshop_operations(
                        db, settings.workshop_retention_days)
                    if ws_deleted > 0:
                        logger.info(f"🗑️ Purge atelier: {ws_deleted} opération(s) supprimée(s)")
                except Exception:
                    logger.exception("purge workshop_operations échouée")
            
            await asyncio.sleep(interval_hours * 3600)
        except asyncio.CancelledError:
            logger.info("🛑 Tâche de purge des logs arrêtée")
            break
        except Exception as e:
            logger.warning(f"⚠️ Erreur lors de la purge des logs: {e}")
            await asyncio.sleep(interval_hours * 3600)


async def periodic_backup_scheduler():
    """
    Tâche périodique pour vérifier et déclencher les backups planifiés.
    Vérifie toutes les minutes si un backup doit être lancé.
    """
    from croniter import croniter
    from sqlalchemy import select
    from app.models import Application
    from app.routes.app_backups import perform_app_backup, BackupExecutionError, _naive_utc
    from datetime import datetime, timezone
    
    logger.info("📅 Scheduler de backups démarré")
    
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
                    .where(Application.is_unmanaged == False)
                    # Les ateliers ({slug}-ws) ne sont jamais sauvegardés.
                    .where(~Application.slug.endswith("-ws"))
                )
                apps = result.scalars().all()
                
                now = utcnow()
                
                for app in apps:
                    try:
                        # Vérifier si c'est l'heure du backup.
                        # _naive_utc : next_backup_at est lu aware depuis TIMESTAMPTZ
                        # par asyncpg, alors que now (utcnow) est naive → TypeError sans normalisation.
                        nb = _naive_utc(app.next_backup_at)
                        if nb and nb <= now:
                            logger.info(f"📦 Déclenchement backup planifié pour {app.slug}")

                            # Marquer comme en cours et calculer le prochain créneau
                            app.last_backup_status = "running"
                            try:
                                cron = croniter(app.backup_cron, now)
                                app.next_backup_at = cron.get_next(datetime)
                            except Exception as cron_err:
                                logger.error(
                                    f"❌ Cron invalide pour {app.slug} ('{app.backup_cron}'): {cron_err}",
                                    exc_info=True,
                                )
                                app.last_backup_status = "failed"
                                await db.commit()
                                continue
                            await db.commit()

                            # Déclencher le backup réel via la logique partagée
                            try:
                                await perform_app_backup(
                                    db,
                                    app,
                                    created_by="scheduler",
                                    created_by_type="scheduler",
                                    description_prefix="Backup planifié (cron)",
                                )
                                logger.info(
                                    f"✅ Backup planifié réussi pour {app.slug}. "
                                    f"Prochain: {app.next_backup_at}"
                                )
                            except BackupExecutionError as be:
                                app.last_backup_status = "failed"
                                await db.commit()
                                logger.error(
                                    f"❌ Backup planifié échoué pour {app.slug}: {be.message}"
                                )
                            except Exception as run_err:
                                app.last_backup_status = "failed"
                                await db.commit()
                                logger.error(
                                    f"❌ Backup planifié échoué pour {app.slug}: {run_err}",
                                    exc_info=True,
                                )

                        # Si next_backup_at n'est pas défini, le calculer
                        elif not app.next_backup_at and app.backup_cron:
                            try:
                                cron = croniter(app.backup_cron, now)
                                app.next_backup_at = cron.get_next(datetime)
                                await db.commit()
                                logger.info(
                                    f"📅 Prochain backup programmé pour {app.slug}: {app.next_backup_at}"
                                )
                            except Exception as cron_err:
                                logger.error(
                                    f"❌ Cron invalide pour {app.slug} ('{app.backup_cron}'): {cron_err}",
                                    exc_info=True,
                                )

                    except Exception as e:
                        logger.error(
                            f"❌ Erreur scheduler backup pour {app.slug}: {e}",
                            exc_info=True,
                        )
            
            # Vérifier toutes les 60 secondes
            await asyncio.sleep(60)
            
        except asyncio.CancelledError:
            logger.info("🛑 Scheduler de backups arrêté")
            break
        except Exception as e:
            logger.error(f"❌ Erreur dans le scheduler de backups: {e}", exc_info=True)
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cleanup_task, _docker_sync_task, _log_purge_task, _backup_scheduler_task, _snapshot_scheduler_task, _msg_dispatch_task
    
    # Startup
    logger.info("🚀 Démarrage du Hub d'Authentification...")
    await init_db()
    await apply_migrations()  # Appliquer les migrations SQL si nécessaires
    await _ensure_user_identity_columns()  # Colonnes identité (uuid/prenom/nom)
    await _backfill_user_uuids()  # Backfill uuid des utilisateurs existants
    await _backfill_user_oidc_subs()  # Backfill oidc_sub des utilisateurs existants
    await backfill_dns_names()  # Chantier B : dns_name = slug pour les apps sans nom de routage

    # Chantier B : reconcile du routage au boot (zone DNS + vhosts SNI) — jamais bloquant
    try:
        from app.routing_service import regenerate as _regenerate_routing
        await _regenerate_routing()
    except Exception as exc:
        logger.warning(f"Routage: reconcile au boot échoué: {exc}")

    # Vérifier la configuration des secrets et de sécurité AVANT l'admin bootstrap
    # (un mdp admin/DB faible ou des secrets manquants bloquent en production)
    from app.config import get_settings, ensure_backup_keys_exist, validate_security_config, ConfigValidationError
    try:
        for _warn in validate_security_config(get_settings()):
            logger.warning(_warn)
    except ConfigValidationError as _cfg_err:
        logger.critical(f"⛔ Configuration de sécurité invalide : {_cfg_err}")
        raise SystemExit(str(_cfg_err))

    await create_default_admin()

    # Restaurer l'IP du Hub décidée par l'admin (app_settings "hub_host"
    # prime sur l'env HUB_HOST et sur l'auto-détection).
    # NOTE: import local — async_session_maker est ré-importé localement plus
    # bas dans le lifespan (pattern des tâches), une référence module-level
    # ici lèverait UnboundLocalError.
    from app.database import async_session_maker
    async with async_session_maker() as _net_db:
        await network_routes.apply_persisted_hub_host(_net_db)

    _settings = get_settings()
    if not _settings.secrets_encryption_key:
        logger.info(
            "⚠️  SECRETS_ENCRYPTION_KEY non définie : "
            "les secrets des service accounts ne seront pas chiffrés."
        )

    # Générer les clés de chiffrement backup (indépendantes des clés JWT)
    ensure_backup_keys_exist()

    # Trousseau JWT multi-kid : seed au premier boot (importe la paire legacy
    # sous kid hub-jwt-key-1, ou reconstruit depuis jwt_keys/ — spec §5.1)
    from app import jwt_keyring
    jwt_keyring.ensure_seeded()
    
    # Démarrer la tâche de nettoyage périodique
    _cleanup_task = asyncio.create_task(periodic_token_cleanup())
    logger.info("🧹 Tâche de nettoyage des tokens démarrée")
    
    # Démarrer la tâche de synchronisation Docker
    _docker_sync_task = asyncio.create_task(periodic_docker_sync())
    
    # Démarrer la tâche de purge des logs
    _log_purge_task = asyncio.create_task(periodic_log_purge())

    # Démarrer le moniteur de vivacité du token GitHub (alerte admins si invalide)
    from app.github_token_monitor import periodic_github_token_check
    _github_token_check_task = asyncio.create_task(periodic_github_token_check())

    # Démarrer le cleanup périodique des sessions utilisateurs (timeout 20 min).
    # Avant : démarré au 1er client SSE -> après un restart, les sessions
    # restées ouvertes au crash restaient "actives" indéfiniment.
    from app.routes.events import start_cleanup_task
    start_cleanup_task()
    logger.info("🧹 Cleanup des sessions utilisateurs démarré (boot)")
    
    # Démarrer le scheduler de backups
    _backup_scheduler_task = asyncio.create_task(periodic_backup_scheduler())

    # Demarrer le scheduler de snapshots
    from app.routes.snapshots import _snapshot_cron_scheduler
    _snapshot_scheduler_task = asyncio.create_task(_snapshot_cron_scheduler())

    # Demarrer le dispatcheur de messages differes
    from app.routes.messages import periodic_scheduled_message_dispatch
    _msg_dispatch_task = asyncio.create_task(periodic_scheduled_message_dispatch())

    # Demarrer le worker outbox (livraison garantie des appels M2M)
    async def _outbox_auth_provider(slug: str) -> dict:
        from app.auth import create_access_token
        from datetime import timedelta
        token, _, _ = create_access_token(
            username=f"sa_{slug}",
            apps={slug: "admin"},
            expires_delta=timedelta(minutes=10),
            is_service_account=True,
        )
        return {"Authorization": f"Bearer {token}", "X-Slug": slug}

    outbox_service.set_auth_provider(_outbox_auth_provider)
    outbox_service.start_worker()
    logger.info("📦 Worker outbox démarré")

    # Seed des templates de messages par defaut
    from app.routes.messages import seed_default_templates
    from app.database import async_session_maker
    async with async_session_maker() as seed_db:
        await seed_default_templates(seed_db)

    # Kill-switch backups : requalifier les backups restés "running" (cas crash)
    try:
        from app.routes.app_backups import cleanup_stale_running_backups
        await cleanup_stale_running_backups()
    except Exception as exc:
        logger.warning(f"⚠️ Kill-switch backups au démarrage a échoué: {exc}")

    # Kill-switch snapshots
    try:
        from app.snapshot_engine import cleanup_stale_snapshots
        async with async_session_maker() as kill_db:
            await cleanup_stale_snapshots(kill_db)
    except Exception as exc:
        logger.warning(f"⚠️ Kill-switch snapshots au démarrage a échoué: {exc}")

    # Kill-switch déploiements : requalifier les pending/running orphelins (crash)
    try:
        from app.deploy_service import requalify_stuck_deployments
        async with async_session_maker() as dep_db:
            requalified = await requalify_stuck_deployments(dep_db)
        if requalified:
            logger.info(f"♻️ {requalified} déploiement(s) stuck requalifié(s) en échec")
    except Exception as exc:
        logger.warning(f"⚠️ Kill-switch déploiements au démarrage a échoué: {exc}")

    logger.info("✅ Hub prêt !")
    yield
    
    # Shutdown
    logger.info("👋 Arrêt du Hub...")
    
    # 1. Fermer toutes les connexions SSE actives (débloquer les générateurs)
    from app.routes.events import close_all_sse_connections, force_stop_cleanup_task
    await close_all_sse_connections()
    force_stop_cleanup_task()
    
    # 2. Arrêter le health checker et fermer le client httpx
    from app.health_checker import stop_health_checker, _close_http_client
    stop_health_checker()
    await _close_http_client()
    
    # Laisser le temps aux SSE de se terminer proprement
    await asyncio.sleep(0.5)
    
    # 3. Annuler les tâches périodiques
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
    if _snapshot_scheduler_task:
        _snapshot_scheduler_task.cancel()
        try:
            await _snapshot_scheduler_task
        except asyncio.CancelledError:
            pass
    if _msg_dispatch_task:
        _msg_dispatch_task.cancel()
        try:
            await _msg_dispatch_task
        except asyncio.CancelledError:
            pass
    if _github_token_check_task:
        _github_token_check_task.cancel()
        try:
            await _github_token_check_task
        except asyncio.CancelledError:
            pass

    # Annuler les déploiements automatiques en cours
    from app.deploy_service import cancel_all_deployments
    cancel_all_deployments()

    # Annuler les opérations atelier en cours
    from app.workshop_service import cancel_all_workshop_operations
    cancel_all_workshop_operations()

    await outbox_service.stop_worker()
    await outbox_service.cleanup_old_messages()
    
    logger.info("✅ Shutdown terminé proprement")


app = FastAPI(
    title="Hub d'Authentification",
    description="Portail centralisé d'authentification avec gestion des applications",
    version=docs.get_hub_version(),
    lifespan=lifespan
)

# CORS - configuration centralisée via build_cors_config (voir app/config.py).
_cors_origins, _cors_regex, _cors_credentials = build_cors_config(settings)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_regex,
    allow_credentials=_cors_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Slug", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


# =============================================================================
# MIDDLEWARE DE CONTEXTE DE REQUÊTE
# =============================================================================

# Routes éligibles à la limite de body élargie : uploads de sauvegarde/import
# (backups potentiellement volumineux — cf. incident exemple-app > 150 Mo) :
#   - /api/admin/restore                     : restauration depuis fichier uploadé
#   - /api/apps/{slug}/backup/import         : import backup d'app
#   - /api/apps/{slug}/backup/restore/upload : restore d'app depuis fichier
#   - /api/admin/backup/import, /import/upload, /import/preview :
#     import d'environnement Hub (routes réimplémentées 10/2026, cf. admin_backup.py)
# Toutes les autres routes restent soumises à la limite globale (anti-DoS).
_UPLOAD_BODY_LIMIT_PATH_RE = re.compile(
    r"^/api/(admin/restore|admin/backup/import(?:/upload|/preview)?"
    r"|apps/[^/]+/backup/(?:import|restore/upload))$"
)

@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """
    Middleware qui enrichit chaque requête avec un contexte de traçabilité :
    - request_id: UUID unique pour tracer la requête
    - source_app_slug: Application source (header X-Slug)
    - start_time: Horodatage de début pour calculer la durée
    Vérifie aussi la taille du body (protection DoS) :
    - limite globale (max_body_size_mb, défaut 10 MB) sur toutes les routes ;
    - limite élargie (max_upload_body_size_mb, défaut 512 MB) pour les uploads
      de sauvegarde/import. Limites lues dans Settings à chaque requête :
      ajustables via .env sans rebuild.
    """
    # Vérifier la taille du body — seuil choisi selon la route.
    content_length = request.headers.get("content-length")
    if content_length:
        _settings = get_settings()
        max_body_mb = (
            _settings.max_upload_body_size_mb
            if _UPLOAD_BODY_LIMIT_PATH_RE.match(request.url.path)
            else _settings.max_body_size_mb
        )
        if int(content_length) > max_body_mb * 1024 * 1024:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Corps de requête trop volumineux (max {max_body_mb} MB)"}
            )
    
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


async def _resolve_error_actor(db, request: Request):
    """
    Rattache l'appelant d'une erreur à un utilisateur / service account via le
    JWT Bearer de la requête.

    Le décodage vérifie la SIGNATURE (pas de sub forgé dans les logs) mais
    ignore l'EXPIRATION : un token expiré doit rester rattachable à son
    propriétaire. Ne lève jamais — en cas de doute, (None, None).

    Appelé uniquement sur le chemin d'erreur (zéro impact chemin nominal) :
    ~1 vérif RSA + 1-2 SELECT indexés.
    """
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None, None
        token = auth_header[len("Bearer "):].strip()
        if not token:
            return None, None

        import jwt as pyjwt
        from app import jwt_keyring

        entry, public_pem = jwt_keyring.select_verification_key(token)
        if entry is None:
            return None, None
        payload = pyjwt.decode(
            token,
            public_pem,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},
        )
        # Les tokens de reset de mot de passe ne désignent pas une session
        if payload.get("scope") == "pwd_reset":
            return None, None
        sub = payload.get("sub")
        if not sub:
            return None, None

        if payload.get("type") == "service_account":
            from app.models import ServiceAccount
            result = await db.execute(
                select(ServiceAccount.id).where(ServiceAccount.client_id == sub)
            )
            return None, result.scalar_one_or_none()

        # Utilisateur : claim uid (uuid stable) d'abord, fallback username
        user_id = None
        uid = payload.get("uid")
        if uid:
            result = await db.execute(select(User.id).where(User.uuid == uid))
            user_id = result.scalar_one_or_none()
        if user_id is None:
            result = await db.execute(select(User.id).where(User.username == sub))
            user_id = result.scalar_one_or_none()
        return user_id, None
    except Exception:
        return None, None


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

        # Import local (pattern des tâches) : respecte le patch test de
        # app.database.async_session_maker au lieu de la liaison module-level.
        from app.database import async_session_maker
        async with async_session_maker() as db:
            # Rattacher l'appelant (utilisateur / service account) si un JWT
            # Bearer accompagne la requête — silencieux sinon.
            user_id, service_account_id = await _resolve_error_actor(db, request)
            error_log = ErrorLog(
                request_id=request_id,
                error_code=error_code,
                error_type=error_type,
                error_message=error_message[:500] if error_message else None,
                user_id=user_id,
                service_account_id=service_account_id,
                source_app_slug=source_app_slug,
                request_method=request.method,
                request_path=str(request.url.path)[:255],
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent", "")[:255],
                stack_trace=stack
            )
            db.add(error_log)
            await db.commit()
            logger.debug(f"📝 Erreur {error_code} loggée: {request.url.path}")
            # Temps réel : badge erreurs navbar + liste Audit "Erreurs"
            try:
                from app.routes.audit import _notify_audit
                _notify_audit(extra={
                    "action": "error_logged",
                    "error_code": error_code,
                    "source_app_slug": source_app_slug,
                })
            except ImportError:
                pass
    except Exception as log_error:
        logger.warning(f"⚠️ Erreur lors du logging d'erreur: {log_error}")


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
    
    # Logger toutes les erreurs HTTP (4xx et 5xx) sauf :
    # - les 404 sur des paths non-API (scans, robots, favicon → polluent la DB)
    # - les 401 sur le flux SSE : bruit attendu (clients legacy sans token, tokens
    #   expirés côté navigateur avec reconnexion automatique toutes les 10 s —
    #   ~14k entrées/jour observées qui masquent les vraies erreurs).
    #   Ils restent visibles dans les logs stdout/uvicorn.
    if error_code >= 400:
        should_log = True
        if error_code == 404:
            path = str(request.url.path)
            # Ne loguer les 404 que pour les routes API (pas favicon, .env, wp-admin, etc.)
            if not path.startswith("/api/"):
                should_log = False
        elif error_code == 401 and str(request.url.path) == "/api/events/stream":
            should_log = False
        if should_log:
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
    Retourne un message simplifié (champ + erreur) sans exposer le schéma interne.
    """
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    error_code = 422
    error_type = "validation"
    
    # Formater le message d'erreur
    errors = exc.errors()
    error_messages = []
    simplified_errors = []
    for err in errors:
        loc = ".".join(str(x) for x in err.get("loc", []) if x != "body")
        msg = err.get("msg", "")
        error_messages.append(f"{loc}: {msg}")
        simplified_errors.append({"field": loc, "message": msg})
    error_message = "; ".join(error_messages)
    
    # Logger l'erreur complète côté serveur
    await _log_error_to_db(request, error_code, error_type, error_message)
    
    # Retourner uniquement les champs et messages (sans type, ctx, url internes)
    return JSONResponse(
        status_code=error_code,
        content={
            "detail": simplified_errors,
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
app.include_router(jwt_keys.router)
app.include_router(users.router)
app.include_router(apps_health.router)
app.include_router(apps.router)
app.include_router(service_accounts.router)
app.include_router(audit.router)
app.include_router(docs.router)
app.include_router(routing_routes.router)
app.include_router(events.router)
app.include_router(app_backups.router)
app.include_router(snapshots.router)
app.include_router(profiles.router)
app.include_router(messages.router)
app.include_router(account_requests.router)
app.include_router(settings_routes.router)
app.include_router(network_routes.router)
app.include_router(app_recreate.router)
app.include_router(app_socle_check.router)
app.include_router(vpn.router)
app.include_router(oidc.router)
app.include_router(github_catalog.router)
app.include_router(app_deploy.router)
app.include_router(workshop.router)
app.include_router(atelier.router)
app.include_router(vault.router)
app.include_router(hub_restore.router)
app.include_router(admin_backup.router)
app.include_router(tags.router)
app.include_router(app_groups.router)
app.include_router(app_certs.router)
app.include_router(pki_ca.router)


@app.get("/api/health", include_in_schema=False)
@app.get("/health")
async def health_check():
    from app.routes.docs import get_hub_version
    return {"status": "healthy", "service": "hub-auth", "version": get_hub_version(), "instance_id": get_instance_id()}


@app.get("/api/system/time")
async def get_system_time():
    """
    Retourne l'heure courante du Hub en UTC.

    Utile côté UI pour aider l'utilisateur à composer ses expressions cron
    en comparant avec son heure locale (les cron de backup sont évalués
    en UTC côté serveur).

    `epoch_ms` est le contrat de synchro d'horloge du parc : les apps
    l'utilisent pour horodater leurs logs à l'heure du Hub (offset mesuré
    au point médian request/réponse, cf. backend/logging_config.py du
    socle). Endpoint public, sans auth.
    """
    now = utcnow()
    return {
        "utc_now": now.isoformat(),
        "utc_now_human": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "timezone": "UTC",
        "epoch_ms": int(time.time() * 1000),
    }


@app.get("/api/config/hub-host")
async def get_hub_host():
    """Retourne l'IP/hostname du Hub pour la configuration des applications"""
    # get_settings() à l'appel (pas la référence module-level `settings`) :
    # si le singleton est reconstruit (reload, tests), la valeur configurée
    # courante fait foi — une référence figée servirait un état périmé.
    current = get_settings()
    return {
        "host": current.get_hub_host(),
        "configured": bool(current.hub_host),
        "message": "IP auto-détectée" if not current.hub_host else "IP configurée",
        "api_port": current.hub_api_port,
        "frontend_port": current.hub_frontend_port,
        "instance_id": get_instance_id(),
    }


@app.get("/api/config/mode")
async def get_app_mode():
    """Retourne le mode actuel du Hub (development ou production).

    Endpoint public (pas d'auth) pour que le frontend puisse afficher
    l'indicateur de mode sans token.
    """
    return {
        "app_env": settings.app_env,
        "is_development": settings.app_env.strip().lower() == "development",
        "is_production": settings.app_env.strip().lower() == "production",
    }


@app.get("/")
async def root():
    return {
        "message": "Hub d'Authentification",
        "docs": "/docs",
        "health": "/health"
    }


# ============================================================
# ENDPOINTS OUTBOX (administration)
# ============================================================

@app.get("/api/outbox/stats", tags=["Admin"])
async def outbox_stats(request: Request, current_user: User = Depends(get_current_admin)):
    """Statistiques de la file outbox."""
    return await outbox_service.get_stats()


@app.post("/api/outbox/{msg_id}/retry", tags=["Admin"])
async def outbox_retry(
    msg_id: int,
    request: Request,
    current_user: User = Depends(get_current_admin),
):
    """Force le retry d'un message outbox en échec."""
    success = await outbox_service.retry_failed_message(msg_id)
    if success:
        return {"success": True, "message": f"Message #{msg_id} remis en file d'attente"}
    from starlette.exceptions import HTTPException
    raise HTTPException(status_code=404, detail=f"Message #{msg_id} introuvable ou pas en échec")


@app.post("/api/outbox/cleanup", tags=["Admin"])
async def outbox_cleanup(request: Request, current_user: User = Depends(get_current_admin)):
    """Purge les vieux messages outbox (delivered > 7j, failed > 30j)."""
    deleted = await outbox_service.cleanup_old_messages()
    return {"success": True, "deleted": deleted, "message": "Nettoyage effectué"}
