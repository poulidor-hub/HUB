"""  
Health Checker - Vérification périodique de l'état des applications
=====================================================================

Ce module effectue un ping HTTP vers chaque application enregistrée
pour détecter les pannes et afficher automatiquement le mode maintenance.

Les applications sont sur le même serveur, donc la latence est minimale
et il ne devrait pas y avoir de faux négatifs.

Fonctionnalités:
- Ping HTTP toutes les 10 secondes
- Timeout court (2s) pour détecter rapidement les pannes
- Notifie le frontend via SSE quand le statut change
- Combine avec le statut Docker pour une détection complète
- Vérification SSL configurable via HEALTH_CHECK_VERIFY_SSL et HEALTH_CHECK_CA_BUNDLE
"""
import asyncio
import os
import logging
from typing import Dict, Optional
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# Cache des statuts de santé des applications: slug -> status
_app_health_cache: Dict[str, dict] = {}

# Intervalle de vérification en secondes (10s pour les apps locales)
HEALTH_CHECK_INTERVAL = 10

# Timeout pour chaque ping (court car apps sur même serveur)
HEALTH_CHECK_TIMEOUT = 2.0

# Nombre d'échecs consécutifs avant de considérer l'app comme "offline"
FAILURE_THRESHOLD = 1  # Immédiat car sur même serveur

# Configuration SSL pour les health checks
# HEALTH_CHECK_VERIFY_SSL=true active la vérification, false la désactive (défaut)
# HEALTH_CHECK_CA_BUNDLE=/path/to/ca.pem pour spécifier un certificat CA
_VERIFY_SSL = os.environ.get("HEALTH_CHECK_VERIFY_SSL", "false").lower() in ("true", "1", "yes")
_CA_BUNDLE = os.environ.get("HEALTH_CHECK_CA_BUNDLE", "")
HEALTH_CHECK_SSL_VERIFY = _CA_BUNDLE if (_VERIFY_SSL and _CA_BUNDLE) else _VERIFY_SSL

# Client httpx réutilisable (au lieu de recréer à chaque check)
_http_client: Optional[httpx.AsyncClient] = None

def _get_http_client() -> httpx.AsyncClient:
    """Retourne un client httpx réutilisable pour les health checks."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=HEALTH_CHECK_TIMEOUT,
            verify=HEALTH_CHECK_SSL_VERIFY
        )
    return _http_client

async def _close_http_client():
    """Ferme le client httpx."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None

# Tâche de fond
_health_check_task: Optional[asyncio.Task] = None


def get_app_health_status(slug: str) -> Optional[str]:
    """
    Retourne le statut de santé d'une application depuis le cache.
    
    Returns:
        'healthy': Application accessible et répondante
        'unhealthy': Application ne répond pas (en maintenance)
        None: Pas encore vérifié
    """
    status_data = _app_health_cache.get(slug)
    if not status_data:
        return None
    return status_data.get("status")


def get_all_health_statuses() -> Dict[str, dict]:
    """Retourne tous les statuts de santé en cache."""
    return _app_health_cache.copy()


async def check_app_health(app_slug: str, api_url: str) -> dict:
    """
    Vérifie la santé d'une application via un ping HTTP.
    
    Args:
        app_slug: Identifiant unique de l'application
        api_url: URL de l'API de l'application
        
    Returns:
        dict avec status ('healthy', 'unhealthy'), response_time_ms, error
    """
    result = {
        "slug": app_slug,
        "status": "unhealthy",
        "response_time_ms": None,
        "error": None,
        "last_check": datetime.utcnow().isoformat()
    }
    
    if not api_url:
        result["error"] = "Pas d'URL API configurée"
        return result
    
    try:
        start = asyncio.get_running_loop().time()
        
        # Client réutilisable avec configuration SSL depuis l'environnement
        client = _get_http_client()
        # Essayer /api/health d'abord (standard des apps), puis / en fallback
        for endpoint in ["/api/health", "/"]:
            try:
                url = api_url.rstrip('/') + endpoint
                response = await client.get(url)
                elapsed = (asyncio.get_running_loop().time() - start) * 1000
                
                # Toute réponse < 500 indique que l'app est vivante
                # (même 401/403 signifie que l'app tourne)
                if response.status_code < 500:
                    result["status"] = "healthy"
                    result["response_time_ms"] = round(elapsed, 2)
                    return result
            except Exception:
                continue
        
        # Aucun endpoint n'a répondu
        result["error"] = "Aucun endpoint accessible"
            
    except httpx.TimeoutException:
        result["error"] = f"Timeout ({HEALTH_CHECK_TIMEOUT}s)"
    except httpx.ConnectError:
        result["error"] = "Connexion refusée"
    except Exception as e:
        result["error"] = str(e)[:100]
    
    return result


async def run_health_checks():
    """
    Exécute un cycle de vérification de santé pour toutes les applications.
    Notifie via SSE si le statut d'une application change.
    """
    from app.database import async_session_maker
    from app.models import Application
    from sqlalchemy import select
    
    try:
        async with async_session_maker() as db:
            # Récupérer toutes les applications actives
            result = await db.execute(
                select(Application).where(Application.is_active == True)
            )
            apps = result.scalars().all()
            
            # Vérifier toutes les apps en parallèle
            check_tasks = [
                check_app_health(app.slug, app.api_url) 
                for app in apps
            ]
            results = await asyncio.gather(*check_tasks, return_exceptions=True)
            
            # Traiter les résultats et détecter les changements
            changes = []
            
            for app, health_result in zip(apps, results):
                if isinstance(health_result, Exception):
                    # Erreur lors de la vérification
                    health_result = {
                        "slug": app.slug,
                        "status": "unhealthy",
                        "error": str(health_result)[:100],
                        "last_check": datetime.utcnow().isoformat()
                    }
                
                # Récupérer l'ancien statut
                old_status = _app_health_cache.get(app.slug, {}).get("status")
                new_status = health_result["status"]
                
                # Mettre à jour le cache
                _app_health_cache[app.slug] = health_result
                
                # Détecter les changements de statut
                if old_status is not None and old_status != new_status:
                    changes.append({
                        "slug": app.slug,
                        "name": app.name,
                        "old_status": old_status,
                        "new_status": new_status,
                        "error": health_result.get("error")
                    })
                    
                    # Logger le changement
                    if new_status == "unhealthy":
                        logger.warning(f"🔴 Application '{app.name}' est passée OFFLINE: {health_result.get('error')}")
                    else:
                        logger.info(f"🟢 Application '{app.name}' est de nouveau ONLINE")
            
            # Notifier les changements via SSE
            if changes:
                from app.routes.events import broadcast_event
                for change in changes:
                    await broadcast_event("app_health_changed", {
                        "slug": change["slug"],
                        "name": change["name"],
                        "health_status": change["new_status"],
                        "previous_status": change["old_status"],
                        "error": change.get("error")
                    })
                    
    except Exception as e:
        logger.error(f"Erreur lors du health check: {e}")


async def periodic_health_check():
    """
    Tâche de fond qui effectue les health checks périodiquement.
    """
    logger.info(f"🏥 Health checker démarré (intervalle: {HEALTH_CHECK_INTERVAL}s, SSL verify: {HEALTH_CHECK_SSL_VERIFY})")
    
    # Premier check immédiat
    await run_health_checks()
    
    while True:
        try:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            await run_health_checks()
        except asyncio.CancelledError:
            await _close_http_client()
            logger.info("🏥 Health checker arrêté")
            break
        except Exception as e:
            logger.error(f"Erreur dans le health checker: {e}")
            # Continuer malgré les erreurs


def start_health_checker():
    """Démarre le health checker en arrière-plan."""
    global _health_check_task
    
    if _health_check_task is None or _health_check_task.done():
        try:
            loop = asyncio.get_running_loop()
            _health_check_task = loop.create_task(periodic_health_check())
        except RuntimeError:
            pass


def stop_health_checker():
    """Arrête le health checker."""
    global _health_check_task
    
    if _health_check_task and not _health_check_task.done():
        _health_check_task.cancel()
        _health_check_task = None


def is_health_checker_running() -> bool:
    """Vérifie si le health checker est en cours d'exécution."""
    return _health_check_task is not None and not _health_check_task.done()


def get_combined_app_status(
    docker_status: Optional[str], 
    health_status: Optional[str]
) -> str:
    """
    Combine le statut Docker et le statut de santé pour déterminer 
    si l'app est disponible ou en maintenance.
    
    Logique:
    - Si Docker n'est pas 'running' -> maintenance
    - Si health_status est 'unhealthy' -> maintenance
    - Sinon -> disponible
    
    Returns:
        'running': Application disponible
        'maintenance': Application en maintenance (conteneur arrêté ou non répondante)
    """
    # Docker arrêté = maintenance
    if docker_status and docker_status != 'running':
        return docker_status  # 'exited', 'stopped', etc.
    
    # Health check échoué = maintenance (même si Docker semble OK)
    if health_status == 'unhealthy':
        return 'unhealthy'
    
    # Tout va bien
    if docker_status == 'running' or health_status == 'healthy':
        return 'running'
    
    # Statut inconnu (pas encore vérifié)
    return docker_status or 'unknown'
