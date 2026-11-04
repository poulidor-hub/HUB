# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Configuration réseau du Hub (changement d'IP validé par l'admin).

Endpoints (tous admin) :
- GET    /api/config/network            : état complet (IP effective, source db/env/auto, ports, instance)
- PUT    /api/config/network/hub-host   : définir l'IP du Hub (mutation à chaud + persistance app_settings)
- DELETE /api/config/network/hub-host   : revenir à l'env HUB_HOST / auto-détection

Persistance : table app_settings (clé "hub_host"), prioritaire au boot sur
l'env HUB_HOST — la dernière décision admin fait foi. La mutation à chaud du
singleton get_settings() est effective immédiatement pour tous les consommateurs
(hub-host public, credentials M2M, OIDC, deploy).
"""

import ipaddress
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_instance_id, get_server_ip, get_settings
from app.database import get_db
from app.deploy_service import _run_cmd
from app.models import AppSetting, User
from app.auth import get_current_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config/network", tags=["Network"])

HUB_HOST_KEY = "hub_host"

# Hostname RFC 1123 (labels alphanumériques, pas de tiret en tête/queue)
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


def _validate_host(raw) -> str:
    """Normalise et valide une IP ou un hostname. Lève HTTPException 400 sinon."""
    if raw is None or not isinstance(raw, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Le champ 'hub_host' est requis (chaîne)")
    host = raw.strip()
    if not host or len(host) > 253 or any(c.isspace() for c in host):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="IP ou hostname invalide")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if not _HOSTNAME_RE.match(host):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="IP ou hostname invalide")
    return host


async def _read_db_hub_host(db: AsyncSession) -> str | None:
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == HUB_HOST_KEY)
    )
    setting = result.scalar_one_or_none()
    if setting and setting.value.strip():
        return setting.value.strip()
    return None


def _network_state(db_value: str | None) -> dict:
    settings = get_settings()
    env_value = (os.environ.get("HUB_HOST") or "").strip() or None
    effective = settings.get_hub_host()
    if db_value:
        source = "db"
    elif env_value:
        source = "env"
    else:
        source = "auto"
    return {
        "hub_host": effective,
        "source": source,
        "db_value": db_value,
        "env_value": env_value,
        "detected_ip": get_server_ip(),
        "api_port": settings.hub_api_port,
        "frontend_port": settings.hub_frontend_port,
        "instance_id": get_instance_id(),
    }


async def apply_persisted_hub_host(db: AsyncSession) -> bool:
    """Applique l'IP persistée en DB (décision admin) au démarrage du Hub.

    Retourne True si une valeur DB a été appliquée. À appeler dans le lifespan
    après apply_migrations() : l'admin prime sur l'env HUB_HOST et sur
    l'auto-détection.
    """
    db_value = await _read_db_hub_host(db)
    if not db_value:
        return False
    settings = get_settings()
    settings.hub_host = db_value
    logger.info(f"🌐 HUB_HOST restauré depuis app_settings : {db_value}")
    return True


@router.get("")
async def get_network_state(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """État réseau complet du Hub (admin)."""
    db_value = await _read_db_hub_host(db)
    return _network_state(db_value)


@router.put("/hub-host")
async def set_hub_host(
    payload: dict,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Définir l'IP/hostname du Hub (admin). Persistance app_settings + effet à chaud."""
    host = _validate_host(payload.get("hub_host") if payload else None)

    result = await db.execute(
        select(AppSetting).where(AppSetting.key == HUB_HOST_KEY)
    )
    setting = result.scalar_one_or_none()
    if not setting:
        setting = AppSetting(key=HUB_HOST_KEY, value=host)
        db.add(setting)
    else:
        setting.value = host
    await db.commit()

    settings = get_settings()
    settings.hub_host = host
    logger.info(f"🌐 HUB_HOST défini à '{host}' par {current_admin.username}")

    # Chantier B : l'IP sert aux enregistrements DNS + proxy_pass → republished
    try:
        from app.routing_service import regenerate as _regenerate_routing
        await _regenerate_routing()
    except Exception:
        pass

    try:
        from app.routes.events import broadcast_event
        await broadcast_event("settings_updated", {"key": HUB_HOST_KEY, "value": host})
    except ImportError:
        pass

    db_value = await _read_db_hub_host(db)
    return _network_state(db_value)


@router.delete("/hub-host")
async def unset_hub_host(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Revenir à l'env HUB_HOST / auto-détection (supprime la décision admin)."""
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == HUB_HOST_KEY)
    )
    setting = result.scalar_one_or_none()
    if setting:
        await db.delete(setting)
        await db.commit()

    settings = get_settings()
    settings.hub_host = (os.environ.get("HUB_HOST") or "").strip()
    logger.info(f"🌐 HUB_HOST admin supprimé — retour env/auto par {current_admin.username}")

    # Chantier B (review M5) : l'IP sert aux enregistrements DNS + proxy_pass
    try:
        from app.routing_service import regenerate as _regenerate_routing
        await _regenerate_routing()
    except Exception:
        pass

    db_value = await _read_db_hub_host(db)
    return _network_state(db_value)



# ==== Détection de l'IP LAN du HOST (via docker run --network host) ====

async def _detect_host_ip_via_docker() -> str:
    """IP LAN du host via un conteneur jetable en réseau host (pattern
    clone-db du workshop). La socket UDP du conteneur ne voit que l'IP Docker.
    iproute2 est ajouté à la volée : le `ip route get` busybox d'alpine ne
    renseigne pas le champ `src`. La sortie brute est parsée côté Python."""
    cmd = [
        "docker", "run", "--rm", "--network", "host", "alpine:3",
        "sh", "-c",
        "apk add --no-cache iproute2 >/dev/null 2>&1 && ip -4 route get 8.8.8.8",
    ]
    output = await _run_cmd(cmd, Path("/tmp"), 30)
    match = re.search(r"src (\d+\.\d+\.\d+\.\d+)", output)
    if not match:
        raise RuntimeError(
            f"IP du host non trouvée dans la sortie docker : {output[:200]}")
    return match.group(1)


@router.get("/detect-host-ip")
async def detect_host_ip(
    current_admin: User = Depends(get_current_admin),
):
    """Détecte l'IP LAN du serveur (l'hôte) — le conteneur du Hub lui-même ne
    voit que son IP Docker (10.42.0.x)."""
    try:
        ip = await _detect_host_ip_via_docker()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Détection de l'IP du host impossible : {e}")
    return {"ip": ip}
