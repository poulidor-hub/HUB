# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Ateliers (workshops) — liste native pour l'admin Hub et l'agent exemple-app.

Découverte par marqueurs : tout dossier de apps_host_path contenant un
.workshop.env (contrat hubctl v0.6.1, cf. workshop_service.WS_ENV_KEYS) est un
atelier. L'état des conteneurs (projet compose = ws_slug, suffixe -ws) est lu
via le client Docker socket :ro de docker_scanner — Docker injoignable ⇒
containers_state="inconnu", la route ne lève jamais 500.

Shape de sortie (contrat, consommé par l'agent exemple-app et l'UI admin) :
    {"ateliers": [{"app", "ws_slug", "api_port", "frontend_port",
                   "containers_state"}, ...],
     "ghosts":   [{"slug", "name", "created_at"}, ...],
     "orphans":  [{"project", "containers", "running"}, ...]}

containers_state ∈ {"running", "partiel", "stopped", "absent", "inconnu"}.
ghosts : fiches Hub is_workshop=True dont le slug n'a plus de marqueur disque
(ex. unlink en échec sur fs read-only) — purgeables via POST /reconcile.
orphans : projets compose -ws actifs sans marqueur disque. Les clés ghosts/
orphans sont des listes vides en cas d'indisponibilité DB/Docker : la route
ne lève jamais 500. La clé "ateliers" reste inchangée (rétro-compat).
"""
import logging
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.config import get_settings
from app.database import get_db
from app.docker_scanner import docker_scanner
from app.models import User

logger = logging.getLogger("hub.atelier")

router = APIRouter(prefix="/api/admin/atelier", tags=["atelier"])

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE (lignes vides/commentaires ignorés) — même sémantique
    que workshop_service._read_env_file (contrat .workshop.env)."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return result
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _int_or_none(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _containers_state(docker_client, ws_slug: str) -> str:
    """État des conteneurs du projet compose ws_slug — ne lève jamais."""
    if docker_client is None:
        return "inconnu"
    try:
        containers = docker_client.containers.list(all=True)
        matching = [c for c in containers
                    if (c.labels or {}).get(COMPOSE_PROJECT_LABEL) == ws_slug]
    except Exception as e:  # socket down, permission, … → jamais 500
        logger.warning(f"état conteneurs atelier « {ws_slug} » indisponible : {e}")
        return "inconnu"
    if not matching:
        return "absent"
    running = sum(1 for c in matching if c.status == "running")
    if running == len(matching):
        return "running"
    if running == 0:
        return "stopped"
    return "partiel"


def scan_workshops(apps_path: Path, docker_client) -> list[dict]:
    """Liste les ateliers détectés par marqueurs .workshop.env sous apps_path.

    app = nom du dossier porteur du marqueur ; ws_slug = APP_SLUG du marqueur
    (repli <app>-ws, parité workshop_service) ; ports entiers lus depuis le
    marqueur (None si absents/non numériques). Trié par app pour un contrat
    stable. Ne lève jamais.
    """
    ateliers: list[dict] = []
    try:
        candidates = sorted(
            p for p in apps_path.iterdir()
            if p.is_dir() and not p.name.startswith(".")
            and (p / ".workshop.env").exists())
    except OSError as e:
        logger.warning(f"scan ateliers impossible dans {apps_path} : {e}")
        return ateliers
    for app_dir in candidates:
        env = _read_env_file(app_dir / ".workshop.env")
        app = app_dir.name
        ws_slug = env.get("APP_SLUG") or f"{app}-ws"
        ateliers.append({
            "app": app,
            "ws_slug": ws_slug,
            "api_port": _int_or_none(env.get("API_PORT")),
            "frontend_port": _int_or_none(env.get("FRONTEND_PORT")),
            "containers_state": _containers_state(docker_client, ws_slug),
        })
    return ateliers


async def _scan_ghosts(known_ws_slugs: set[str]) -> list[dict]:
    """Fiches Hub is_workshop=True sans marqueur .workshop.env disque.

    Fantôme = ligne Application {slug}-ws survivant à la suppression de
    l'atelier (ex. unlink en échec sur fs read-only) : ses URLs pointent sur
    des ports possiblement réattribués → purgeable via POST /reconcile.
    Tolérant aux pannes DB : DB injoignable ⇒ [] + warning (la liste des
    ateliers doit rester consultable pour diagnostiquer, jamais 500)."""
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import Application

    ghosts: list[dict] = []
    try:
        async with async_session_maker() as db:
            result = await db.execute(
                select(Application)
                .where(Application.is_workshop.is_(True))
                .order_by(Application.slug))
            for app in result.scalars().all():
                if app.slug in known_ws_slugs:
                    continue  # marqueur disque présent → atelier vivant
                ghosts.append({
                    "slug": app.slug,
                    "name": app.name,
                    "created_at": (app.created_at.isoformat()
                                   if app.created_at else None),
                })
    except Exception as e:
        logger.warning(f"scan ateliers fantômes indisponible (DB) : {e}")
        return []
    return ghosts


def _scan_orphans(docker_client, known_ws_slugs: set[str]) -> list[dict]:
    """Projets compose -ws actifs sans marqueur disque (conteneurs orphelins).

    Réutilise le client Docker de _containers_state (socket :ro) : Docker
    injoignable ⇒ [] — la route ne lève jamais 500. Trié par projet pour un
    contrat stable ; noms de conteneurs best-effort (getattr)."""
    if docker_client is None:
        return []
    try:
        containers = docker_client.containers.list(all=True)
    except Exception as e:
        logger.warning(f"scan ateliers orphelins indisponible : {e}")
        return []
    projects: dict[str, list] = {}
    for c in containers:
        project = (c.labels or {}).get(COMPOSE_PROJECT_LABEL)
        if not project or not project.endswith("-ws"):
            continue  # hors périmètre atelier (projets prod, hub, …)
        if project in known_ws_slugs:
            continue  # marqueur disque présent → atelier vivant
        projects.setdefault(project, []).append(c)
    return [{
        "project": project,
        "containers": [getattr(c, "name", "") or "?" for c in cs],
        "running": sum(1 for c in cs if c.status == "running"),
    } for project, cs in sorted(projects.items())]


@router.get("")
async def list_ateliers(_: User = Depends(get_current_admin)):
    """Liste native des ateliers (marqueurs .workshop.env + état conteneurs),
    enrichie du diagnostic incohérences : ghosts (fiches DB is_workshop=True
    sans marqueur) et orphans (projets compose -ws sans marqueur).

    Read-only : lecture du disque apps_host_path, du socket Docker :ro et de
    la table applications ; Docker ou DB indisponible ⇒ état "inconnu"/liste
    vide, jamais d'erreur 500. La clé "ateliers" est inchangée (rétro-compat
    agent exemple-app / UI admin).
    """
    docker_client = None
    try:
        if docker_scanner.is_available:
            docker_client = docker_scanner.client
    except Exception as e:
        logger.warning(f"client Docker indisponible : {e}")
        docker_client = None
    apps_path = Path(get_settings().apps_host_path)
    ateliers = scan_workshops(apps_path, docker_client)
    known_ws_slugs = {a["ws_slug"] for a in ateliers}
    return {
        "ateliers": ateliers,
        "ghosts": await _scan_ghosts(known_ws_slugs),
        "orphans": _scan_orphans(docker_client, known_ws_slugs),
    }


@router.post("/reconcile")
async def reconcile_ateliers(
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Purge les fiches atelier fantômes (is_workshop=True sans marqueur).

    Body JSON optionnel {"slugs": [...]} : cible explicite ; absent/vide ⇒
    tous les ghosts courants. Un slug redevenu porteur d'un marqueur est
    skip (l'atelier existe encore — jamais purgé à tort). La purge passe
    toujours par _delete_workshop_app_row : garde is_workshop=True, une
    fiche prod n'est JAMAIS visée. Réponse :
    {"purged": [...], "skipped": [{"slug", "reason"}], "errors": [{"slug", "error"}]}
    """
    try:
        body = await request.json()
    except ValueError:
        body = {}
    slugs = body.get("slugs") if isinstance(body, dict) else None

    apps_path = Path(get_settings().apps_host_path)
    known_ws_slugs = {a["ws_slug"] for a in scan_workshops(apps_path, None)}
    if slugs:
        targets = [str(s) for s in slugs]
    else:
        targets = [g["slug"] for g in await _scan_ghosts(known_ws_slugs)]

    from app.workshop_service import _delete_workshop_app_row

    purged: list[str] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    for slug in targets:
        if slug in known_ws_slugs:
            skipped.append({"slug": slug,
                            "reason": "marqueur .workshop.env présent"})
            continue
        try:
            if await _delete_workshop_app_row(slug):
                purged.append(slug)
            else:
                skipped.append({"slug": slug,
                                "reason": "aucune fiche is_workshop=True"})
        except Exception as e:
            logger.warning(f"purge fiche atelier « {slug} » échouée : {e}")
            errors.append({"slug": slug, "error": str(e)})

    # Audit Hub standard (action destructive, comme les routes admin).
    try:
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, "ATELIER_RECONCILE", target="atelier",
            details=f"purge fantômes ateliers (purged={len(purged)}, "
                    f"skipped={len(skipped)}, errors={len(errors)})",
            notify_sse=False, request=request)
        await db.commit()
    except Exception as e:
        logger.warning(f"audit reconcile impossible : {e}")

    return {"purged": purged, "skipped": skipped, "errors": errors}


def _read_atelier_token(token_file: str) -> str:
    """Lit le token partagé du service atelier hôte (fichier monté :ro).

    Fichier absent/illisible ⇒ chaîne vide : la route close-all répond
    alors 503 dédié (token à resynchroniser, pas un 401 du service).
    """
    try:
        return Path(token_file).read_text().strip()
    except OSError as e:
        logger.warning(f"lecture du token atelier impossible "
                       f"({token_file}) : {e}")
        return ""


@router.post("/close-all")
async def close_all_ateliers(
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Ferme TOUS les ateliers — proxy vers le service hôte `hubctl atelier
    serve` (127.0.0.1:8777, cf. settings.atelier_service_url).

    Passthrough strict de la réponse du service (200 « tout fermé » ou 207
    « échec partiel » — la shape est décidée par hubctl, on ne la déduit pas).
    Service injoignable (ConnectError/timeout) ⇒ 503 verbalisable : un
    `down -v` peut être long, le timeout est réglé par
    settings.atelier_close_timeout (600 s par défaut).
    """
    settings = get_settings()
    base_url = settings.atelier_service_url.rstrip("/")
    headers = {}
    token = _read_atelier_token(settings.atelier_service_token_file)
    if not token:
        raise HTTPException(
            status_code=503,
            detail=("Token atelier absent/illisible côté Hub "
                    "(secrets/atelier_token) — resynchronisez-le depuis "
                    "~/.config/hubctl/atelier_token"))
    headers["X-Atelier-Token"] = token
    try:
        async with httpx.AsyncClient(
                timeout=settings.atelier_close_timeout) as http:
            resp = await http.post(f"{base_url}/atelier/close-all",
                                   headers=headers)
    except httpx.TimeoutException as e:
        logger.warning(f"fermeture ateliers toujours en cours côté hôte : {e}")
        raise HTTPException(
            status_code=503,
            detail=("Fermeture toujours en cours côté hôte — consultez "
                    "journalctl -u hubctl-atelier avant de relancer"))
    except httpx.ConnectError as e:
        logger.warning(f"service atelier hôte injoignable : {e}")
        raise HTTPException(
            status_code=503,
            detail=("Service atelier hôte injoignable — "
                    "hubctl atelier serve est-il actif ?"))
    except httpx.HTTPError as e:
        logger.warning(f"erreur HTTP vers le service atelier hôte : {e}")
        raise HTTPException(
            status_code=502,
            detail="Erreur de communication avec le service atelier hôte.")
    try:
        payload = resp.json()
    except ValueError:
        logger.warning(f"réponse non JSON du service atelier "
                       f"(HTTP {resp.status_code})")
        raise HTTPException(
            status_code=502,
            detail="Réponse invalide du service atelier hôte.")

    # Audit Hub standard (action destructive, comme les routes admin).
    try:
        closed = failed = None
        if isinstance(payload, dict):
            closed = len(payload.get("closed") or [])
            failed = len(payload.get("failed") or [])
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, "ATELIER_CLOSE_ALL", target="atelier",
            details=f"fermeture ateliers via service hôte "
                    f"(closed={closed}, failed={failed})",
            notify_sse=False, request=request,
            response_status=resp.status_code)
        await db.commit()
    except Exception as e:
        logger.warning(f"audit close-all impossible : {e}")

    # Passthrough code + corps (bytes) — y compris 207 et codes d'erreur.
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get(
                        "content-type", "application/json"))
