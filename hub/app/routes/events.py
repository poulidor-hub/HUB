# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Server-Sent Events (SSE) pour les mises à jour en temps réel.
Permet au frontend de recevoir les notifications sans rafraîchir la page.
Gère aussi le tracking de l'activité utilisateur et le cleanup des sessions.
"""
import asyncio
import json
import hmac
import hashlib
import time
from typing import Set, Optional, Dict
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.health_checker import start_health_checker, stop_health_checker, is_health_checker_running
from app.models import utcnow, User

import logging
logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/events", tags=["Events"])

# Ensemble des clients connectés (asyncio.Queue pour chaque client)
_connected_clients: Set[asyncio.Queue] = set()

# Mapping user_id -> set de queues pour le tracking par utilisateur (multi-onglets)
_user_connections: Dict[int, Set[asyncio.Queue]] = {}

# Task de cleanup périodique
_cleanup_task: Optional[asyncio.Task] = None

# Event global de shutdown pour fermer proprement les SSE
_shutdown_event: asyncio.Event = asyncio.Event()


async def broadcast_event(event_type: str, data: Optional[dict] = None):
    """
    Diffuse un événement à tous les clients SSE connectés.
    
    Args:
        event_type: Type d'événement (apps_updated, users_updated, etc.)
        data: Données optionnelles de l'événement
    """
    event = {
        "type": event_type,
        "data": data or {},
        "timestamp": utcnow().isoformat()
    }
    
    # Notifier tous les clients connectés
    for queue in list(_connected_clients):
        try:
            await queue.put(event)
        except Exception:
            # Client déconnecté, sera nettoyé automatiquement
            pass


def notify_event(event_type: str, data: Optional[dict] = None):
    """
    Version synchrone pour diffuser un événement.
    Utilise asyncio.create_task dans un contexte async.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_event(event_type, data))
    except RuntimeError:
        # Pas de loop actif
        pass


# Cooldown pour l'événement de présence (évite les rafales login/logout/segments)
ACTIVE_USERS_COOLDOWN_S = 2.0
_active_users_last_notify = 0.0


def notify_active_users_changed():
    """Diffuse 'active_users_changed' (changement de présence users/apps) avec
    cooldown 2 s. Payload volontairement vide : le client rafraîchit ses vues
    visibles (Audit vue d'ensemble / Uptime / Par Application / Sessions)."""
    global _active_users_last_notify
    current_time = time.monotonic()
    if current_time - _active_users_last_notify < ACTIVE_USERS_COOLDOWN_S:
        return
    _active_users_last_notify = current_time
    notify_event("active_users_changed", {})


async def send_to_user(user_id: int, event_type: str, data: Optional[dict] = None):
    """
    Envoie un événement SSE à un utilisateur spécifique (toutes ses connexions).
    Utilisé pour les messages ciblés.
    """
    if user_id not in _user_connections:
        return

    event = {
        "type": event_type,
        "data": data or {},
        "timestamp": utcnow().isoformat()
    }

    for queue in list(_user_connections[user_id]):
        try:
            await queue.put(event)
        except Exception:
            pass


async def compute_dashboard_stats(db) -> dict:
    """Compteurs du dashboard en UNE seule requête (scalar subqueries)."""
    from app.models import Application, User, ServiceAccount, UserSession
    from sqlalchemy import select, func

    row = (await db.execute(select(
        select(func.count(Application.id)).scalar_subquery(),
        select(func.count(Application.id)).where(Application.is_active == True).scalar_subquery(),
        select(func.count(User.id)).scalar_subquery(),
        select(func.count(ServiceAccount.id)).scalar_subquery(),
        select(func.count(UserSession.id)).where(UserSession.logout_at.is_(None)).scalar_subquery(),
    ))).one()
    apps_count, active_apps, users_count, sa_count, active_sessions = row
    return {
        "total_apps": apps_count or 0,
        "active_apps": active_apps or 0,
        "total_users": users_count or 0,
        "total_service_accounts": sa_count or 0,
        "active_sessions": active_sessions or 0,
    }


async def broadcast_stats_update():
    """
    Calcule et diffuse les statistiques légères à tous les clients.
    """
    from app.database import async_session_maker

    try:
        async with async_session_maker() as db:
            stats = await compute_dashboard_stats(db)
            stats["connected_sse_clients"] = len(_connected_clients)
            await broadcast_event("stats_updated", stats)
    except Exception as e:
        logger.warning(f"⚠️ Erreur calcul stats SSE: {e}")


def notify_stats_update():
    """
    Version synchrone pour déclencher une mise à jour des statistiques.
    Appelée après login, logout, création/suppression d'entités.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_stats_update())
    except RuntimeError:
        pass


# =============================================================================
# CLEANUP PÉRIODIQUE DES SESSIONS
# =============================================================================

async def periodic_session_cleanup():
    """
    Tâche de fond qui nettoie les sessions inactives toutes les 2 minutes.
    Démarre automatiquement au premier client SSE connecté.
    """
    from app.database import async_session_maker
    from app.routes.audit import cleanup_stale_sessions
    
    while True:
        try:
            await asyncio.sleep(120)  # 2 minutes
            
            async with async_session_maker() as db:
                # Flush batché de la présence mémoire (1 tx pour tous les users)
                flushed = await _flush_presence_to_db(db)
                cleaned = await cleanup_stale_sessions(db)
                await db.commit()
                if cleaned > 0:
                    logger.info(f"🧹 Cleanup périodique: {cleaned} session(s) expirée(s) fermée(s)")
                    # Mettre à jour les stats après cleanup
                    await broadcast_stats_update()
                    notify_active_users_changed()
                elif flushed > 0:
                    logger.debug(f"💾 Flush présence SSE: {flushed} utilisateur(s)")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"⚠️ Erreur cleanup sessions: {e}")


def start_cleanup_task():
    """Démarre la tâche de cleanup et le health checker si pas déjà actifs."""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        try:
            loop = asyncio.get_running_loop()
            _cleanup_task = loop.create_task(periodic_session_cleanup())
            logger.info("🔄 Tâche de cleanup des sessions démarrée")
        except RuntimeError:
            pass
    
    # Démarrer aussi le health checker
    if not is_health_checker_running():
        start_health_checker()


def stop_cleanup_task():
    """Arrête le health checker quand plus aucun client SSE (le cleanup des
    sessions, lui, tourne désormais en continu depuis le lifespan)."""
    if len(_connected_clients) == 0:
        if is_health_checker_running():
            stop_health_checker()


def force_stop_cleanup_task():
    """Force l'arrêt de la tâche de cleanup (appelé au shutdown)."""
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        _cleanup_task = None


async def close_all_sse_connections():
    """
    Ferme proprement toutes les connexions SSE actives.
    Appelé lors du shutdown de l'application pour débloquer les générateurs.
    """
    _shutdown_event.set()
    
    # Envoyer un message shutdown dans toutes les queues pour débloquer les wait_for
    shutdown_msg = {"type": "shutdown", "timestamp": utcnow().isoformat()}
    for queue in list(_connected_clients):
        try:
            queue.put_nowait(shutdown_msg)
        except asyncio.QueueFull:
            pass
    
    logger.info(f"📡 Signal shutdown envoyé à {len(_connected_clients)} client(s) SSE")


def reset_shutdown_event():
    """Réinitialise l'event de shutdown (utile après un reload)."""
    global _shutdown_event
    _shutdown_event = asyncio.Event()


# =============================================================================
# TRACKING UTILISATEUR VIA SSE
# =============================================================================

def register_user_connection(user_id: int, queue: asyncio.Queue):
    """Enregistre une connexion SSE pour un utilisateur (supporte multi-onglets)."""
    if user_id not in _user_connections:
        _user_connections[user_id] = set()
    _user_connections[user_id].add(queue)


def unregister_user_connection(user_id: int, queue: asyncio.Queue):
    """Désenregistre une connexion SSE spécifique d'un utilisateur."""
    if user_id in _user_connections:
        _user_connections[user_id].discard(queue)
        if not _user_connections[user_id]:
            del _user_connections[user_id]


# Connexions SSE par couple (user, app) — pour fermer le bon segment
_app_connections: dict = {}


def register_app_connection(user_id: int, app_slug: str, queue: asyncio.Queue):
    _app_connections.setdefault((user_id, app_slug), set()).add(queue)


def unregister_app_connection(user_id: int, app_slug: str, queue: asyncio.Queue):
    conns = _app_connections.get((user_id, app_slug))
    if conns is not None:
        conns.discard(queue)
        if not conns:
            _app_connections.pop((user_id, app_slug), None)


# --- Présence en mémoire (anti-churn) --------------------------------------
# Audit SSE du 08/11/2026 : le navigateur recrée ses flux SSE toutes les
# ~60 s (churn mesuré : 1583 fermetures/24h pour 1 utilisateur). Chaque
# cycle coûtait INSERT session + UPDATE fermeture + heartbeat DB, avec
# deadlocks sweeper×fermeture en prime. Désormais : heartbeat en mémoire,
# grâce de déconnexion > délai de reconnexion client, flush DB unique par
# le sweeper.
_last_seen: Dict[int, float] = {}
_last_app_slug: Dict[int, str] = {}
# Grâce > délais de reconnexion observés (5 s sse.js, 10 s AuthContext
# exemple-app) : le reconnect annule le end_session/close_segment planifié.
DISCONNECT_GRACE_S = 65
# Fenêtre au connect : en dessous, la (re)connexion ne touche pas la DB
# (la session ouverte est réutilisée, sa présence est à jour en mémoire).
RECONNECT_REUSE_WINDOW_S = 65


def _needs_db_session_touch(user_id: int, app_slug: Optional[str] = None) -> bool:
    """Vrai si cette (re)connexion doit garantir la session en DB :
    - première connexion, ou retour après une vraie absence (> fenêtre churn) ;
    - changement d'app courante (le segment d'usage doit suivre en DB).
    Un churn de reconnexion sur la même app ne coûte aucune écriture."""
    last = _last_seen.get(user_id)
    if last is None or (time.time() - last) > RECONNECT_REUSE_WINDOW_S:
        return True
    return app_slug is not None and _last_app_slug.get(user_id) != app_slug


async def _flush_presence_to_db(db: AsyncSession) -> int:
    """Flush batché de la présence mémoire vers la DB (une seule transaction
    pour tous les utilisateurs connectés — appelé par le sweeper 120 s)."""
    from app.routes.audit import ensure_session_presence

    flushed = 0
    for user_id, _ts in list(_last_seen.items()):
        if not _user_connections.get(user_id):
            continue  # parti : entrée obsolète, le sweeper fermera la session
        app_slug = _last_app_slug.get(user_id)
        await ensure_session_presence(db, user_id, app_slug=app_slug or None)
        flushed += 1
    await db.commit()
    return flushed


async def handle_app_disconnect(user_id: int, app_slug: str):
    """Ferme le segment d'usage de l'app quand sa dernière connexion SSE part."""
    from app.database import async_session_maker
    from app.routes.audit import get_active_session_for_user
    from app.usage_tracking import close_app_segment

    await asyncio.sleep(DISCONNECT_GRACE_S)  # tolère un churn de reconnexion
    if _app_connections.get((user_id, app_slug)):
        return

    try:
        async with async_session_maker() as db:
            session = await get_active_session_for_user(db, user_id)
            if session:
                closed = await close_app_segment(db, session.id, app_slug, utcnow(), "disconnect")
                if session.current_app_slug == app_slug:
                    session.current_app_slug = None
                await db.commit()
                if closed > 0:
                    notify_active_users_changed()
    except Exception as e:
        logger.warning(f"⚠️ Fermeture segment app {app_slug} user {user_id}: {e}")


async def handle_user_disconnect(user_id: int):
    """
    Appelé quand un utilisateur se déconnecte du SSE.
    Ferme sa session si elle est toujours active après un court délai.
    """
    from app.database import async_session_maker
    from app.routes.audit import end_session
    
    # Attendre la grâce : un churn de reconnexion (< DISCONNECT_GRACE_S)
    # annule la fermeture — la session ouverte est réutilisée au reconnect.
    await asyncio.sleep(DISCONNECT_GRACE_S)
    
    # Vérifier si l'utilisateur a encore des connexions actives
    if user_id in _user_connections and _user_connections[user_id]:
        return  # Encore connecté via un autre onglet
    
    try:
        async with async_session_maker() as db:
            session = await end_session(db, user_id, reason="sse_disconnect")
            if session:
                logger.info(f"📴 Session fermée (déconnexion SSE): user_id={user_id}")
                await db.commit()
                _last_seen.pop(user_id, None)
                _last_app_slug.pop(user_id, None)
                await broadcast_stats_update()
    except Exception as e:
        logger.warning(f"⚠️ Erreur fermeture session SSE: {e}")


HEARTBEAT_INTERVAL_S = 15.0


async def _emit_heartbeat(user_id: int, app_slug: Optional[str] = None) -> None:
    """Heartbeat en mémoire à cadence fixe (15 s). Aucun accès DB : le flush
    est fait en batch par le sweeper (_flush_presence_to_db, 1 tx / 120 s).
    Le ping SSE transport, lui, reste à cadence 15 s côté générateur."""
    _last_seen[user_id] = time.time()
    if app_slug:
        _last_app_slug[user_id] = app_slug


async def event_generator(request: Request, queue: asyncio.Queue, user_id: Optional[int] = None, app_slug: Optional[str] = None):
    """Générateur d'événements SSE pour un client."""
    started_at = utcnow()
    exit_cause = "client_disconnect"
    try:
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"
        last_beat = utcnow()

        while True:
            if _shutdown_event.is_set():
                exit_cause = "server_shutdown"
                break
            if await request.is_disconnected():
                exit_cause = "client_disconnect"
                break

            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL_S)
                if event.get("type") == "shutdown":
                    exit_cause = "event_shutdown"
                    break
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                # Ping uniquement en période de silence (garde le proxy/navigateur vivants)
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

            # Heartbeat DB à cadence fixe, QUEL QUE SOIT le trafic SSE
            if user_id and (utcnow() - last_beat).total_seconds() >= HEARTBEAT_INTERVAL_S:
                last_beat = utcnow()
                await _emit_heartbeat(user_id, app_slug)
    except asyncio.CancelledError:
        exit_cause = "cancelled"
    except Exception:
        exit_cause = "error"
        logger.exception(f"💥 SSE erreur générateur: user_id={user_id} app={app_slug}")
    finally:
        duration_s = (utcnow() - started_at).total_seconds()
        logger.info(
            f"📴 SSE fermé cause={exit_cause} durée={duration_s:.0f}s "
            f"user_id={user_id} app={app_slug}"
        )
        # Cleanup quand le client se déconnecte
        _connected_clients.discard(queue)
        
        # Gérer la déconnexion utilisateur
        if user_id:
            unregister_user_connection(user_id, queue)
            # Lancer la fermeture de session en arrière-plan (avec délai)
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(handle_user_disconnect(user_id))
            except RuntimeError:
                pass

            if app_slug:
                unregister_app_connection(user_id, app_slug, queue)
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(handle_app_disconnect(user_id, app_slug))
                except RuntimeError:
                    pass

        # Arrêter le cleanup si plus aucun client
        stop_cleanup_task()


@router.get("/stream")
async def event_stream(request: Request):
    """
    Endpoint SSE pour recevoir les mises à jour en temps réel.
    
    Événements disponibles :
    - `apps_updated` : Une application a été créée, modifiée ou supprimée
    - `users_updated` : Un utilisateur a été créé, modifié ou supprimé
    - `service_accounts_updated` : Un service account a été modifié
    - `stats_updated` : Statistiques mises à jour
    - `ping` : Keep-alive (toutes les 15s)
      Note : rester < ~25 s — Firefox coupe une connexion SSE ~25-29 s après
      le dernier octet reçu (sinon déconnexions toutes les ~55 s).
    
    Le ping SSE sert de keep-alive transport pour:
    - Maintenir la connexion active (proxy/navigateur)
    - Détecter les déconnexions (fermeture navigateur, etc.)
    L'activité utilisateur (last_activity_at) est tenue en mémoire à la même
    cadence et flushée en DB par le sweeper toutes les 120 s.
    
    **Exemple d'utilisation JavaScript :**
    ```javascript
    const eventSource = new EventSource('/api/events/stream');
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'apps_updated') {
            loadApps(); // Rafraîchir la liste des apps
        }
    };
    ```
    """
    # Authentification requise : token Bearer OU beacon_token + user_id
    user_id = None
    auth_header = request.headers.get("authorization", "")
    authenticated = False
    auth_mode = None

    if auth_header:
        try:
            token = auth_header.split(" ", 1)[1] if auth_header.startswith("Bearer ") else None
            if token:
                from app.auth import verify_token_not_revoked
                from app.database import async_session_maker
                async with async_session_maker() as db:
                    payload = await verify_token_not_revoked(token, db)
                    if payload.get("type") == "user":
                        username = payload.get("sub")
                        if username:
                            result = await db.execute(select(User).where(User.username == username))
                            user = result.scalar_one_or_none()
                            if user and user.is_active:
                                user_id = user.id
                    authenticated = user_id is not None
                    if authenticated:
                        auth_mode = "bearer"
        except Exception as e:
            logger.warning(f"SSE: token Bearer invalide: {e}")

    if not authenticated:
        # Fallback 2: token JWT passé en query param (compatibilité EventSource)
        token_param = request.query_params.get("token")
        if token_param:
            try:
                from app.auth import verify_token_not_revoked
                from app.database import async_session_maker
                async with async_session_maker() as db:
                    payload = await verify_token_not_revoked(token_param, db)
                    if payload.get("type") == "user":
                        username = payload.get("sub")
                        if username:
                            result = await db.execute(select(User).where(User.username == username))
                            user = result.scalar_one_or_none()
                            if user and user.is_active:
                                user_id = user.id
                    authenticated = user_id is not None
                    if authenticated:
                        auth_mode = "query_token"
            except Exception as e:
                logger.warning(f"SSE: token URL invalide: {e}")

    if not authenticated:
        user_id_param = request.query_params.get("user_id")
        beacon_token = request.query_params.get("beacon_token", "")
        if user_id_param and beacon_token:
            try:
                uid = int(user_id_param)
                from app.routes.auth import verify_beacon_token
                if verify_beacon_token(uid, beacon_token):
                    user_id = uid
                    authenticated = True
                    auth_mode = "beacon"
                else:
                    logger.warning(f"SSE: beacon_token invalide pour user_id={user_id_param}")
            except (ValueError, Exception):
                pass

    if not authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise pour le flux SSE. Utilisez un token Bearer, ?token=JWT, ou beacon_token+user_id."
        )

    # Présence par app (optionnel) : slug validé, sinon ignoré (jamais d'échec)
    app_slug = None
    app_param = request.query_params.get("app")
    if app_param:
        from app.database import async_session_maker
        from app.models import Application
        async with async_session_maker() as db:
            result = await db.execute(
                select(Application).where(
                    Application.slug == app_param,
                    Application.is_active == True,  # noqa: E712
                )
            )
            found = result.scalar_one_or_none()
        if found:
            app_slug = found.slug
        else:
            logger.warning(f"SSE: param app='{app_param}' inconnu ou désactivée — ignoré")

    queue: asyncio.Queue = asyncio.Queue()
    _connected_clients.add(queue)

    if user_id:
        register_user_connection(user_id, queue)
        if user_id and app_slug:
            register_app_connection(user_id, app_slug, queue)

    # Instrumentation : contexte de connexion (identifie le client, sa source et
    # son mode d'auth) — clé du diagnostic du churn de reconnexion SSE.
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "inconnu")
    )

    # Garantie session en DB uniquement pour une vraie (re)connexion :
    # un churn (< RECONNECT_REUSE_WINDOW_S) réutilise la session ouverte
    # et ne coûte aucune écriture.
    #
    # NB : start_cleanup_task (health checker inclus) est volontairement
    # appelé APRÈS ce bloc — ses tâches de fond écrivent en DB et, en test
    # (StaticPool SQLite partagé), un rollback de pool concurrent peut
    # annuler une transaction en vol.
    if user_id and _needs_db_session_touch(user_id, app_slug):
        try:
            from app.database import async_session_maker
            from app.routes.audit import ensure_session_presence
            async with async_session_maker() as db:
                await ensure_session_presence(
                    db,
                    user_id,
                    app_slug=app_slug,
                    ip_address=client_ip,
                    user_agent=request.headers.get("user-agent"),
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"⚠️ Garantie session SSE user {user_id}: {e}")

    if user_id:
        _last_seen[user_id] = time.time()
        if app_slug:
            _last_app_slug[user_id] = app_slug

    start_cleanup_task()

    logger.info(
        f"🔌 SSE connecté: user_id={user_id} app={app_slug} auth={auth_mode} "
        f"ip={client_ip} clients_actifs={len(_connected_clients)}"
    )

    return StreamingResponse(
        event_generator(request, queue, user_id, app_slug=app_slug),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/status")
async def event_status():
    """Retourne le nombre de clients SSE connectés."""
    return {
        "connected_clients": len(_connected_clients),
        "status": "active"
    }
