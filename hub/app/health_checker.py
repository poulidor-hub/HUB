# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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


def utcnow() -> datetime:
    """Retourne l'heure UTC actuelle sans timezone (compatible Python 3.12+)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

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


# Projet compose du Hub lui-même : jamais piloté ni compté via les actions app.
HUB_BASE_COMPOSE_PROJECT = "hub_base"


def _list_all_containers() -> Optional[list]:
    """
    Liste docker-py de TOUS les conteneurs (une seule liste par appel).

    Returns:
        list de conteneurs docker-py, ou None si Docker indisponible ou
        erreur (incertitude — l'appelant ne doit rien classifier).
    """
    try:
        # Import paresseux pour éviter tout cycle d'imports
        from app.docker_scanner import docker_scanner

        if not docker_scanner.is_available:
            return None

        return docker_scanner.client.containers.list(all=True)
    except Exception as e:
        logger.warning(f"Docker: liste des conteneurs impossible: {e}")
        return None


def _container_matched_slugs(container) -> set:
    """Slugs matchés par un conteneur : env APP_SLUG ou label hub.slug."""
    slugs = set()
    try:
        env_list = container.attrs.get('Config', {}).get('Env', [])
        for env in env_list:
            if '=' in env:
                key, value = env.split('=', 1)
                if key == 'APP_SLUG' and value:
                    slugs.add(value)
    except Exception:
        pass
    labels = container.labels or {}
    label_slug = labels.get('hub.slug')
    if label_slug:
        slugs.add(label_slug)
    return slugs


def _group_containers_for_slugs(containers: list, slugs: list) -> Dict[str, list]:
    """
    Construit {slug: groupe de conteneurs trié par nom} depuis UNE liste Docker.

    Une app = 2-3 conteneurs (api, frontend, postgres) partageant le même env
    APP_SLUG (postgres n'a que le label com.docker.compose.project). Le groupe
    = conteneurs matchés (APP_SLUG / hub.slug) + TOUS les conteneurs de leurs
    projets compose (le project ne suit pas le slug : GED -> feat-ged-v1,
    exemple-app -> exemple-app — il faut donc le dériver des conteneurs).
    Le projet hub_base (le Hub lui-même) est exclu, même si matché directement.
    Déduplication par id docker-py, tri stable par nom.
    """
    by_project: Dict[str, list] = {}
    matched: Dict[str, list] = {slug: [] for slug in slugs}

    for container in containers:
        labels = container.labels or {}
        project = labels.get('com.docker.compose.project')
        if project:
            by_project.setdefault(project, []).append(container)
        container_slugs = _container_matched_slugs(container)
        for slug in slugs:
            if slug in container_slugs:
                matched[slug].append(container)

    groups: Dict[str, list] = {}
    for slug in slugs:
        seed = matched[slug]
        # Projets compose dérivés des conteneurs matchés (hors hub_base)
        projects = {
            (c.labels or {}).get('com.docker.compose.project') for c in seed
        }
        projects.discard(None)
        projects.discard(HUB_BASE_COMPOSE_PROJECT)

        group = list(seed)
        for project in projects:
            group.extend(by_project.get(project, []))

        # Le projet hub_base n'est jamais piloté ni compté : exclu même en
        # match direct (env/label). Déduplication en gardant le premier ordre.
        seen = set()
        deduped = []
        for container in group:
            if (container.labels or {}).get(
                'com.docker.compose.project'
            ) == HUB_BASE_COMPOSE_PROJECT:
                continue
            key = getattr(container, 'id', None) or id(container)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(container)

        groups[slug] = sorted(
            deduped, key=lambda c: getattr(c, 'name', '') or ''
        )
    return groups


def find_containers_for_app(slug: str) -> list:
    """
    Retourne TOUS les conteneurs du groupe compose de l'app (triés par nom).

    Une seule liste Docker. Voir _group_containers_for_slugs pour la logique
    de grouping (APP_SLUG / hub.slug -> projets compose -> groupe complet).

    Returns:
        list (vide si aucun conteneur matché ou Docker indisponible)
    """
    containers = _list_all_containers()
    if containers is None:
        return []
    return _group_containers_for_slugs(containers, [slug])[slug]


def count_containers_batch(slugs) -> Dict[str, Optional[int]]:
    """
    Compte les conteneurs du groupe de chaque slug avec UNE SEULE liste Docker.

    Pour le dashboard : ne jamais faire un list() par app (N appels Docker par
    rafraîchissement). Les slugs dupliqués sont dédupliqués.

    Returns:
        dict {slug: nombre de conteneurs du groupe} ; None par slug si Docker
        indisponible ou erreur (incertitude — l'appelant reste à None).
    """
    unique_slugs = list(dict.fromkeys(slugs))
    result: Dict[str, Optional[int]] = {slug: None for slug in unique_slugs}
    containers = _list_all_containers()
    if containers is None:
        return result
    groups = _group_containers_for_slugs(containers, unique_slugs)
    for slug, group in groups.items():
        result[slug] = len(group)
    return result


def get_container_state(slug: str) -> Optional[str]:
    """
    Retourne l'état docker-py du GROUPE de conteneurs de l'app (group-aware).

    Une app = 2-3 conteneurs : le statut doit refléter le groupe entier, pas
    un seul conteneur (l'ancien code pouvait retourner 'exited' pendant que
    l'api tournait encore). Une seule liste Docker.

    Returns:
        'running': au moins un conteneur du groupe est running
        'created', 'paused', 'restarting', 'exited', 'dead':
            état du premier conteneur non-running du groupe (aucun running) —
            'stopped' n'est reporté que si TOUS les conteneurs sont off
        'absent': Docker joignable mais aucun conteneur pour ce slug
        None: Docker indisponible ou erreur (incertitude — l'appelant
            doit rester sur 'unhealthy', jamais classer 'stopped')
    """
    try:
        containers = _list_all_containers()
        if containers is None:
            return None

        group = _group_containers_for_slugs(containers, [slug])[slug]
        if not group:
            # Docker joignable mais aucun conteneur pour ce slug
            return 'absent'

        for container in group:
            if container.status == 'running':
                return 'running'
        # Tous off : état du premier non-running (ordre de tri par nom)
        return group[0].status

    except Exception as e:
        logger.warning(f"Docker: état du conteneur de '{slug}' indéterminable: {e}")
        return None


def get_container_states_batch(slugs) -> Dict[str, Optional[str]]:
    """
    États docker-py du GROUPE de conteneurs de plusieurs apps — UNE SEULE
    liste Docker pour tous les slugs. Même contrat par slug que
    get_container_state ('running' | état | 'absent' | None si Docker
    indisponible), dont c'est la variante batchée.

    Motivation : N appels get_container_state exécutés SYNCHRONEMENT dans
    l'event loop au démarrage des sondes parallèles bloquent le traitement
    des réponses HTTP et gonflent artificiellement les latences mesurées
    (gradient décroissant selon l'ordre du gather). La route /apps/health
    appelle cette fonction UNE fois, hors event loop (to_thread).
    """
    slugs = list(slugs)
    states: Dict[str, Optional[str]] = {slug: None for slug in slugs}
    try:
        containers = _list_all_containers()
        if containers is None:
            return states  # Docker indisponible : incertitude pour tous

        groups = _group_containers_for_slugs(containers, slugs)
        for slug, group in groups.items():
            if not group:
                # Docker joignable mais aucun conteneur pour ce slug
                states[slug] = 'absent'
                continue
            for container in group:
                if container.status == 'running':
                    states[slug] = 'running'
                    break
            if states[slug] is None:
                # Tous off : état du premier non-running (ordre de tri par nom)
                states[slug] = group[0].status
        return states
    except Exception as e:
        logger.warning(f"Docker: états des conteneurs indéterminables: {e}")
        return {slug: None for slug in slugs}


def get_app_health_status(slug: str) -> Optional[str]:
    """
    Retourne le statut de santé d'une application depuis le cache.

    Returns:
        'healthy': Application accessible et répondante
        'unhealthy': Application ne répond pas (en maintenance)
        'stopped': Application volontairement arrêtée (conteneur Docker
            non-running identifié lors de l'échec du ping)
        None: Pas encore vérifié
    """
    status_data = _app_health_cache.get(slug)
    if not status_data:
        return None
    return status_data.get("status")


def get_all_health_statuses() -> Dict[str, dict]:
    """Retourne tous les statuts de santé en cache."""
    return _app_health_cache.copy()


async def check_app_health(app_slug: str, api_url: str, docker_detectable: bool = True) -> dict:
    """
    Vérifie la santé d'une application via un ping HTTP.
    
    Args:
        app_slug: Identifiant unique de l'application
        api_url: URL de l'API de l'application
        docker_detectable: False pour les apps distantes (pas de Docker
            local → pas de classification Docker, toujours 'unhealthy'
            sur échec du ping)
        
    Returns:
        dict avec status ('healthy', 'unhealthy', 'stopped'), docker_status,
        response_time_ms, error. Sur échec du ping : si l'état Docker est
        fiable et non-running → 'stopped' (app volontairement arrêtée) ;
        Docker indisponible, conteneur 'running' ou app distante →
        'unhealthy' (jamais 'stopped' sur incertitude).
    """
    result = {
        "slug": app_slug,
        "status": "unhealthy",
        "docker_status": None,
        "response_time_ms": None,
        "error": None,
        "last_check": utcnow().isoformat()
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
    
    # Échec du ping (aucun endpoint accessible, timeout, connexion refusée...)
    # → classifier via l'état Docker. Un seul appel, quel que soit le chemin
    # d'échec ; chemin healthy : aucun appel Docker.
    if docker_detectable:
        docker_status = get_container_state(app_slug)
        result["docker_status"] = docker_status
        if docker_status not in (None, "running"):
            result["status"] = "stopped"
    
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
                select(Application)
                .where(Application.is_active == True)
                .where(Application.is_unmanaged == False)
            )
            apps = result.scalars().all()
            
            # Vérifier toutes les apps en parallèle
            # (les apps distantes n'ont pas de conteneur Docker local :
            # pas de classification Docker, 'unhealthy' sur échec du ping)
            check_tasks = [
                check_app_health(
                    app.slug,
                    app.api_url,
                    docker_detectable=not bool(getattr(app, "is_remote", False)),
                )
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
                        "last_check": utcnow().isoformat()
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
