"""
Server-Sent Events (SSE) pour les mises à jour en temps réel.
Permet au frontend de recevoir les notifications sans rafraîchir la page.
"""
import asyncio
import json
from typing import Set, Optional
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse


router = APIRouter(prefix="/api/events", tags=["Events"])

# Ensemble des clients connectés (asyncio.Queue pour chaque client)
_connected_clients: Set[asyncio.Queue] = set()


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
        "timestamp": datetime.utcnow().isoformat()
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


async def broadcast_stats_update():
    """
    Calcule et diffuse les statistiques légères à tous les clients.
    
    Statistiques envoyées (compteurs simples, pas de requêtes lourdes) :
    - total_apps: nombre d'applications
    - active_apps: applications actives
    - total_users: nombre d'utilisateurs
    - total_service_accounts: nombre de service accounts
    - connected_sse_clients: nombre de clients SSE connectés
    """
    from app.database import async_session_maker
    from app.models import Application, User, ServiceAccount, UserSession
    from sqlalchemy import select, func
    
    try:
        async with async_session_maker() as db:
            # Compteurs simples (requêtes très légères)
            apps_count = await db.scalar(select(func.count(Application.id)))
            active_apps = await db.scalar(
                select(func.count(Application.id))
                .where(Application.is_active == True)
            )
            users_count = await db.scalar(select(func.count(User.id)))
            sa_count = await db.scalar(select(func.count(ServiceAccount.id)))
            
            # Sessions actives (pas de logout_at)
            active_sessions = await db.scalar(
                select(func.count(UserSession.id))
                .where(UserSession.logout_at.is_(None))
            )
            
            stats = {
                "total_apps": apps_count or 0,
                "active_apps": active_apps or 0,
                "total_users": users_count or 0,
                "total_service_accounts": sa_count or 0,
                "active_sessions": active_sessions or 0,
                "connected_sse_clients": len(_connected_clients)
            }
            
            await broadcast_event("stats_updated", stats)
    except Exception as e:
        # Ne pas faire planter si erreur de stats
        print(f"⚠️ Erreur calcul stats SSE: {e}")


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


async def event_generator(request: Request, queue: asyncio.Queue):
    """Générateur d'événements SSE pour un client."""
    try:
        # Envoyer un premier ping immédiatement pour confirmer la connexion
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"
        
        while True:
            # Vérifier si le client est toujours connecté
            if await request.is_disconnected():
                break
            
            try:
                # Attendre un événement avec timeout pour vérifier la connexion
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                # Envoyer un ping pour maintenir la connexion
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        # Cleanup quand le client se déconnecte
        _connected_clients.discard(queue)


@router.get("/stream")
async def event_stream(request: Request):
    """
    Endpoint SSE pour recevoir les mises à jour en temps réel.
    
    Événements disponibles :
    - `apps_updated` : Une application a été créée, modifiée ou supprimée
    - `users_updated` : Un utilisateur a été créé, modifié ou supprimé
    - `service_accounts_updated` : Un service account a été modifié
    - `stats_updated` : Statistiques mises à jour
    - `ping` : Keep-alive (toutes les 30s)
    
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
    queue: asyncio.Queue = asyncio.Queue()
    _connected_clients.add(queue)
    
    return StreamingResponse(
        event_generator(request, queue),
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
