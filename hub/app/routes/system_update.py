# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Mises à jour du parc — routes proxy admin vers le service hôte.

Le service hôte `hubctl update --serve` (settings.update_service_url,
http://host.docker.internal:8778, joignable des conteneurs) expose le
check du parc (GET /update/status), le déclenchement de la mise à jour
(POST /update/run : pull méta + submodules, build, up, rollback auto)
et l'état du job (GET /update/job, update_state.json côté hôte). Le Hub
les proxifie pour les admins :

- GET  /update-status — TOLÉRANT à la panne : service injoignable
  (ConnectError/timeout) ⇒ 200 {"service": "unreachable", "hint": …}
  (recette vault /status) — l'état du service est une information, pas
  une erreur, le badge navbar dégrade proprement ;
- POST /update — passthrough STRICT (202 job soumis, 409 déjà en cours,
  … : la shape est décidée par hubctl, on ne la déduit pas, jamais de
  500 masqué) + audit SYSTEM_UPDATE_TRIGGERED à chaque déclenchement ;
- GET  /update-job — tolérant (même recette que /update-status) : le
  front sonde cette route toutes les 2 s pendant l'exécution (spec §5.3).

Token partagé X-Atelier-Token RÉUTILISÉ du fichier atelier (monté :ro,
cf. _read_atelier_token importé de routes.atelier — aucune nouvelle
setting token) : fichier absent/illisible ⇒ 503 verbalisable sur les 3
routes (à resynchroniser, pas un 401 du service).
"""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.config import get_settings
from app.database import get_db
from app.models import User
from app.routes.atelier import _read_atelier_token

logger = logging.getLogger("hub.system_update")

router = APIRouter(prefix="/api/admin/system", tags=["system-update"])

# Messages verbatim (contrat front, cf. spec §8) — toute divergence ici
# est une régression. Hint des 503 : journalctl -u hubctl-update.
UNREACHABLE_HINT = ("vérifier hubctl-update.service "
                    "(systemctl status hubctl-update)")
TOKEN_MISSING_DETAIL = (
    "Token atelier absent/illisible côté Hub (secrets/atelier_token) — "
    "resynchronisez-le depuis ~/.config/hubctl/atelier_token "
    "(journaux : journalctl -u hubctl-update)")
DOWN_503_DETAIL = ("Service update hôte injoignable — hubctl update "
                   "--serve est-il actif ? (journaux : journalctl -u "
                   "hubctl-update)")
RUN_TIMEOUT_DETAIL = ("Mise à jour toujours en cours côté hôte — consultez "
                      "journalctl -u hubctl-update avant de relancer")


async def _proxy_get_status_or_job(path: str) -> dict:
    """Proxy GET tolérant vers le service hôte (recette vault /status).

    Read-only. Service injoignable (ConnectError/timeout) ⇒ 200
    {"service": "unreachable", "hint": …}. Autre HTTPError / réponse non
    JSON / non-objet / code ≠ 200 ⇒ 502 verbalisable. Timeout
    settings.update_timeout (30 s) : la collecte de statut à froid côté
    hôte (cache TTL 60 s expiré ⇒ git fetch des dépôts) prend ~10 s sur
    le parc réel — un timeout plus court faisait répondre le premier
    appel « unreachable » puis le suivant « ok » (badge instable à
    chaque cycle TTL).
    """
    settings = get_settings()
    base_url = settings.update_service_url.rstrip("/")
    token = _read_atelier_token(settings.atelier_service_token_file)
    if not token:
        raise HTTPException(status_code=503, detail=TOKEN_MISSING_DETAIL)
    headers = {"X-Atelier-Token": token}
    try:
        async with httpx.AsyncClient(
                timeout=settings.update_timeout) as http:
            resp = await http.get(f"{base_url}{path}", headers=headers)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning(f"service update hôte injoignable ({path}) : {e}")
        return {"service": "unreachable", "hint": UNREACHABLE_HINT}
    except httpx.HTTPError as e:
        logger.warning(f"erreur HTTP vers le service update hôte : {e}")
        raise HTTPException(
            status_code=502,
            detail="Erreur de communication avec le service update hôte.")
    try:
        payload = resp.json()
    except ValueError:
        logger.warning(f"réponse non JSON du service update hôte "
                       f"(HTTP {resp.status_code})")
        raise HTTPException(
            status_code=502,
            detail="Réponse invalide du service update hôte.")
    if not isinstance(payload, dict):
        logger.warning(f"réponse non-objet du service update hôte "
                       f"(HTTP {resp.status_code})")
        raise HTTPException(
            status_code=502,
            detail="Réponse invalide du service update hôte.")
    # 401/403/409/500 JSON du service : ne JAMAIS présenter un code
    # d'erreur comme un état exploitable — 502 verbalisable.
    if resp.status_code != 200:
        logger.warning(f"code HTTP inattendu du service update hôte "
                       f"({path} : HTTP {resp.status_code})")
        raise HTTPException(
            status_code=502,
            detail="Réponse invalide du service update hôte.")
    return dict(payload)


@router.get("/update-status")
async def update_status(_: User = Depends(get_current_admin)):
    """État du parc (versions locales vs origin/main) — proxy GET
    {base}/update/status.

    Tolérant à la panne (cf. _proxy_get_status_or_job) : le badge navbar
    interroge cette route à l'initialisation et doit dégrader proprement
    si le service hôte est down.
    """
    return await _proxy_get_status_or_job("/update/status")


@router.post("/update")
async def run_system_update(
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Déclenche la mise à jour du Hub et du socle — proxy POST
    {base}/update/run.

    Passthrough strict de la réponse du service (202 « job soumis », 409
    « update déjà en cours », codes d'erreur : la shape est décidée par
    hubctl, on ne la déduit pas). Service injoignable (ConnectError/
    timeout) ⇒ 503 verbalisable (hint journalctl -u hubctl-update) ;
    timeout dédié : le run peut être long (pull, build, up, rollback).
    """
    settings = get_settings()
    base_url = settings.update_service_url.rstrip("/")
    token = _read_atelier_token(settings.atelier_service_token_file)
    if not token:
        raise HTTPException(status_code=503, detail=TOKEN_MISSING_DETAIL)
    try:
        async with httpx.AsyncClient(
                timeout=settings.update_timeout) as http:
            resp = await http.post(f"{base_url}/update/run",
                                   headers={"X-Atelier-Token": token})
    except httpx.TimeoutException as e:
        logger.warning(f"mise à jour toujours en cours côté hôte : {e}")
        raise HTTPException(status_code=503, detail=RUN_TIMEOUT_DETAIL)
    except httpx.ConnectError as e:
        logger.warning(f"service update hôte injoignable : {e}")
        raise HTTPException(status_code=503, detail=DOWN_503_DETAIL)
    except httpx.HTTPError as e:
        logger.warning(f"erreur HTTP vers le service update hôte : {e}")
        raise HTTPException(
            status_code=502,
            detail="Erreur de communication avec le service update hôte.")
    try:
        payload = resp.json()
    except ValueError:
        logger.warning(f"réponse non JSON du service update hôte "
                       f"(HTTP {resp.status_code})")
        raise HTTPException(
            status_code=502,
            detail="Réponse invalide du service update hôte.")

    # Audit Hub standard (action destructive, comme les routes admin) —
    # à chaque déclenchement, y compris 409 : l'audit porte le résultat
    # du POST (spec §7).
    try:
        state = None
        if isinstance(payload, dict):
            job = payload.get("job")
            if isinstance(job, dict):
                state = job.get("state")
            state = state or payload.get("state")
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, "SYSTEM_UPDATE_TRIGGERED",
            target="system-update",
            details=f"déclenchement mise à jour Hub+socle via service "
                    f"hôte (state={state})",
            notify_sse=False, request=request,
            response_status=resp.status_code)
        await db.commit()
    except Exception as e:
        logger.warning(f"audit system-update impossible : {e}")

    # Passthrough code + corps (bytes) — y compris 409 et codes d'erreur.
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get(
                        "content-type", "application/json"))


@router.get("/update-job")
async def update_job(_: User = Depends(get_current_admin)):
    """État courant/dernier du job (update_state.json côté hôte, état
    « idle » si aucun fichier) — proxy GET {base}/update/job, tolérant à
    la panne (polling front toutes les 2 s, cf. spec §5.3).
    """
    return await _proxy_get_status_or_job("/update/job")
