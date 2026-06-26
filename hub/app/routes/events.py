"""
Server-Sent Events (SSE) pour les mises à jour en temps réel.
Permet au frontend de recevoir les notifications sans rafraîchir la page.
Gère aussi le tracking de l'activité utilisateur et le cleanup des sessions.
"""
import asyncio
import json
import hmac
import hashlib
from typing import Set, Optional, Dict
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.health_checker import start_health_checker, stop_health_checker, is_health_checker_running
from app.models import utcnow

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
                cleaned = await cleanup_stale_sessions(db)
                if cleaned > 0:
                    logger.info(f"🧹 Cleanup périodique: {cleaned} session(s) expirée(s) fermée(s)")
                    # Mettre à jour les stats après cleanup
                    await broadcast_stats_update()
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
    """Arrête la tâche de cleanup et le health checker si plus aucun client connecté."""
    global _cleanup_task
    if len(_connected_clients) == 0:
        if _cleanup_task and not _cleanup_task.done():
            _cleanup_task.cancel()
            _cleanup_task = None
            logger.info("🔄 Tâche de cleanup des sessions arrêtée")
        
        # Arrêter aussi le health checker
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


async def handle_user_disconnect(user_id: int):
    """
    Appelé quand un utilisateur se déconnecte du SSE.
    Ferme sa session si elle est toujours active après un court délai.
    """
    from app.database import async_session_maker
    from app.routes.audit import end_session
    
    # Attendre un court délai pour permettre une reconnexion rapide
    await asyncio.sleep(5)
    
    # Vérifier si l'utilisateur a encore des connexions actives
    if user_id in _user_connections and _user_connections[user_id]:
        return  # Encore connecté via un autre onglet
    
    try:
        async with async_session_maker() as db:
            session = await end_session(db, user_id, reason="sse_disconnect")
            if session:
                logger.info(f"📴 Session fermée (déconnexion SSE): user_id={user_id}")
                await broadcast_stats_update()
    except Exception as e:
        logger.warning(f"⚠️ Erreur fermeture session SSE: {e}")


async def event_generator(request: Request, queue: asyncio.Queue, user_id: Optional[int] = None):
    """Générateur d'événements SSE pour un client."""
    try:
        # Envoyer un premier ping immédiatement pour confirmer la connexion
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"
        
        while True:
            # Vérifier le shutdown global
            if _shutdown_event.is_set():
                break
            
            # Vérifier si le client est toujours connecté
            if await request.is_disconnected():
                break
            
            try:
                # Attendre un événement avec timeout pour vérifier la connexion
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                
                # Message shutdown = sortie immédiate
                if event.get("type") == "shutdown":
                    break
                
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                # Envoyer un ping pour maintenir la connexion
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                
                # Mettre à jour l'activité de la session si user_id connu
                if user_id:
                    try:
                        from app.database import async_session_maker
                        from app.routes.audit import update_session_activity
                        async with async_session_maker() as db:
                            await update_session_activity(db, user_id)
                            await db.commit()
                    except Exception:
                        pass  # Ignorer les erreurs de mise à jour d'activité
    except asyncio.CancelledError:
        pass
    finally:
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
    - `ping` : Keep-alive (toutes les 30s) - Met aussi à jour l'activité de session
    
    Le ping SSE sert de heartbeat pour:
    - Maintenir la connexion active
    - Tracker l'activité utilisateur (last_activity_at)
    - Détecter les déconnexions (fermeture navigateur, etc.)
    
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
    # Essayer d'extraire l'user_id depuis le token (optionnel)
    user_id = None
    auth_header = request.headers.get("authorization", "")
    if not auth_header:
        # SSE via EventSource ne supporte pas les headers custom
        # Valider le user_id via le beacon_token HMAC (lié au secret serveur)
        user_id_param = request.query_params.get("user_id")
        beacon_token = request.query_params.get("beacon_token", "")
        if user_id_param and beacon_token:
            try:
                uid = int(user_id_param)
                # Vérifier le beacon token pour éviter le spoofing
                from app.routes.auth import verify_beacon_token
                if verify_beacon_token(uid, beacon_token):
                    user_id = uid
                else:
                    logger.warning(f"⚠️ SSE: beacon_token invalide pour user_id={user_id_param} depuis {request.client.host if request.client else 'unknown'}")
            except (ValueError, Exception):
                pass
    
    queue: asyncio.Queue = asyncio.Queue()
    _connected_clients.add(queue)
    
    # Enregistrer la connexion utilisateur si on a l'ID
    if user_id:
        register_user_connection(user_id, queue)
    
    # Démarrer le cleanup périodique si premier client
    start_cleanup_task()
    
    return StreamingResponse(
        event_generator(request, queue, user_id),
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
