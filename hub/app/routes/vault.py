# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Vault (coffre hubctl) — consultation et sauvegarde pour l'admin Hub.

Proxy admin-only vers le service hôte `hubctl atelier serve` (le MÊME service
que les ateliers, cf. settings.atelier_service_url) : le coffre vit sur l'hôte
(hubctl vault), le conteneur Hub n'y touche jamais directement.

Shape de sortie (décidée par hubctl, passthrough strict — on ne la déduit pas) :
    GET  /vault/status → {"status", "locked", "last_backup", "vault_size",
                          ["entries", "divergences" si déverrouillé], "pra"}
    POST /vault/backup → {"status": "ok", env, files, db, pra}
                         | {"status": "locked"} | 409 (backup en cours)
                         | 500 (dépassement hôte, toujours en cours)

GET /status est une information, pas une action : service injoignable
(ConnectError/timeout) ⇒ 200 {"service": "unreachable", "hint": …} pour que
l'UI affiche l'état au lieu d'une erreur.
"""
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.config import get_settings
from app.database import get_db
from app.models import User

logger = logging.getLogger("hub.vault")

router = APIRouter(prefix="/api/admin/vault", tags=["vault"])


def _read_vault_token(token_file: str) -> str:
    """Lit le token partagé du service hôte (fichier monté :ro).

    Fichier absent/illisible ⇒ chaîne vide : la route backup répond alors
    503 dédié (token à resynchroniser, pas un 401 du service).
    """
    try:
        return Path(token_file).read_text().strip()
    except OSError as e:
        logger.warning(f"lecture du token atelier impossible "
                       f"({token_file}) : {e}")
        return ""


@router.get("/status")
async def vault_status(_: User = Depends(get_current_admin)):
    """État du coffre — proxy GET {base}/vault/status (token partagé).

    Read-only. Succès ⇒ payload du service fusionné avec {"service": "ok"}
    (clés hubctl préservées telles quelles, déverrouillé comme verrouillé).
    Service injoignable (ConnectError/timeout) ⇒ 200 {"service":
    "unreachable", "hint": …} : l'état du service est une information, pas
    une erreur. Autre HTTPError / réponse non JSON ⇒ 502. Timeout client
    court (10 s) : un status doit répondre vite.
    """
    settings = get_settings()
    base_url = settings.atelier_service_url.rstrip("/")
    token = _read_vault_token(settings.atelier_service_token_file)
    headers = {"X-Atelier-Token": token} if token else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(f"{base_url}/vault/status",
                                  headers=headers)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning(f"service atelier hôte injoignable (vault status) : {e}")
        return {"service": "unreachable",
                "hint": ("vérifier hubctl-atelier.service "
                         "(systemctl status hubctl-atelier)")}
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
    if not isinstance(payload, dict):
        logger.warning(f"réponse non-objet du service atelier "
                       f"(HTTP {resp.status_code})")
        raise HTTPException(
            status_code=502,
            detail="Réponse invalide du service atelier hôte.")
    # 401/403/405/500 JSON du service : ne JAMAIS fusionner avec
    # {"service": "ok"} — l'UI afficherait un faux état sain.
    if resp.status_code != 200:
        logger.warning(f"code HTTP inattendu du service atelier "
                       f"(vault status : HTTP {resp.status_code})")
        raise HTTPException(
            status_code=502,
            detail="Réponse invalide du service atelier hôte.")
    return dict(payload) | {"service": "ok"}


@router.post("/backup")
async def vault_backup(
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Déclenche un backup du coffre — proxy POST {base}/vault/backup.

    Passthrough intégral du code et du corps du service (200 « backup fait »,
    200 {"status": "locked"}, 409 « backup déjà en cours », 500 « dépassement
    hôte, toujours en cours » — la shape est décidée par hubctl). Timeout ⇒
    503 : côté hôte la sauvegarde continue, on ne ment pas en répondant 200.
    ConnectError ⇒ 503 (service inactif), autre HTTPError / non JSON ⇒ 502.
    """
    settings = get_settings()
    base_url = settings.atelier_service_url.rstrip("/")
    headers = {}
    token = _read_vault_token(settings.atelier_service_token_file)
    if not token:
        raise HTTPException(
            status_code=503,
            detail=("Token atelier absent/illisible côté Hub "
                    "(secrets/atelier_token) — resynchronisez-le depuis "
                    "~/.config/hubctl/atelier_token"))
    headers["X-Atelier-Token"] = token
    try:
        async with httpx.AsyncClient(
                timeout=settings.vault_backup_timeout) as http:
            resp = await http.post(f"{base_url}/vault/backup",
                                   headers=headers)
    except httpx.TimeoutException as e:
        logger.warning(f"backup coffre toujours en cours côté hôte : {e}")
        raise HTTPException(
            status_code=503,
            detail=("Sauvegarde toujours en cours côté hôte — consultez "
                    "journalctl -u hubctl-atelier"))
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

    # Audit Hub standard (action sensible, comme les routes admin).
    try:
        status_label = (payload.get("status") or "?"
                        if isinstance(payload, dict) else "?")
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, "VAULT_BACKUP", target="vault",
            details=f"backup coffre via service hôte (status={status_label})",
            notify_sse=False, request=request,
            response_status=resp.status_code)
        await db.commit()
    except Exception as e:
        logger.warning(f"audit vault backup impossible : {e}")

    # 409/500 du service : le client api.js (request()) ne lit que
    # "error.detail" — on réencapsule {"erreur": …} en {"detail": …} pour que
    # le toast affiche le vrai motif (« backup déjà en cours »…) au lieu d'un
    # message générique. Les shapes 200 restent des passthrough bytes stricts.
    if (resp.status_code != 200 and isinstance(payload, dict)
            and "erreur" in payload):
        return JSONResponse(content={"detail": payload["erreur"]},
                            status_code=resp.status_code,
                            media_type="application/json")

    # Passthrough code + corps (bytes) — y compris les codes d'erreur restants.
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get(
                        "content-type", "application/json"))
