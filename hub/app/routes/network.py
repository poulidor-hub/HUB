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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_instance_id, get_server_ip, get_settings
from app.database import get_db
from app.deploy_service import apply_env_values, get_apps_dir
from app.models import AppSetting, Application, User
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

    db_value = await _read_db_hub_host(db)
    return _network_state(db_value)


# ==== Plan de propagation aux apps (validation admin) ====

def _read_hub_host_from_env(env_path) -> str | None:
    """Lit l'IP du Hub d'un .env d'app — HUB_HOST puis variantes legacy
    (HUB_URL, HUB_BASE_URL) qui sont réécrites par apply_env_values."""
    try:
        lines = env_path.read_text().splitlines()
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    for key in ("HUB_HOST", "HUB_URL", "HUB_BASE_URL"):
        if values.get(key):
            return values[key]
    return None


@router.get("/propagation-plan")
async def get_propagation_plan(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Plan de propagation : pour chaque app v2 enregistrée (hors ateliers et
    apps système), l'IP HUB_HOST de son .env vs l'IP effective du Hub."""
    settings = get_settings()
    effective = settings.get_hub_host()

    apps_result = await db.execute(
        select(Application)
        .where(Application.is_workshop.is_(False),
               Application.is_system_app.is_(False))
        .order_by(Application.slug)
    )
    apps = []
    for app in apps_result.scalars():
        env_path = get_apps_dir() / app.slug / ".env"
        if not env_path.exists():
            apps.append({"slug": app.slug, "current_hub_host": None,
                         "status": "no_env", "is_active": app.is_active})
            continue
        current = _read_hub_host_from_env(env_path)
        apps.append({
            "slug": app.slug,
            "current_hub_host": current,
            "status": "up_to_date" if current == effective else "pending",
            "is_active": app.is_active,
        })

    ws_result = await db.execute(
        select(Application)
        .where((Application.is_workshop.is_(True)) |
               (Application.is_system_app.is_(True)))
        .order_by(Application.slug)
    )
    excluded = [
        {"slug": app.slug,
         "reason": "atelier (copie du .env de l'app parente)" if app.is_workshop
                   else "app système interne au Hub"}
        for app in ws_result.scalars()
    ]

    return {"hub_host": effective, "apps": apps, "excluded": excluded}


@router.post("/propagation-plan/apply")
async def apply_propagation_plan(
    payload: dict,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Applique l'IP effective du Hub aux .env des apps Explicitement
    validées par l'admin (liste de slugs). Écriture seule — la recréation des
    conteneurs est un second temps, déclenché par l'admin."""
    slugs = payload.get("slugs") if payload else None
    if not isinstance(slugs, list) or not slugs or \
            not all(isinstance(s, str) and s.strip() for s in slugs):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="'slugs' doit être une liste non vide de slugs")

    settings = get_settings()
    effective = settings.get_hub_host()

    applied = []
    for slug in dict.fromkeys(s.strip() for s in slugs):  # dédoublonné, ordre préservé
        result = await db.execute(
            select(Application).where(Application.slug == slug)
        )
        app = result.scalar_one_or_none()
        if app is None:
            applied.append({"slug": slug, "status": "not_found"})
            continue
        if app.is_workshop or app.is_system_app:
            applied.append({"slug": slug, "status": "excluded"})
            continue
        env_path = get_apps_dir() / app.slug / ".env"
        if not env_path.exists():
            applied.append({"slug": slug, "status": "no_env"})
            continue

        # Chirurgical : seules HUB_HOST et ses variantes sont réécrites
        apply_env_values(env_path, {"HUB_HOST": effective})
        logger.info(f"🌐 Propagation IP '{effective}' → {app.slug} (.env) par "
                    f"{current_admin.username}")
        applied.append({"slug": slug, "status": "written"})

    written = sum(1 for a in applied if a["status"] == "written")
    logger.info(f"🌐 Propagation IP appliquée : {written}/{len(applied)} app(s) "
                f"par {current_admin.username}")

    try:
        from app.routes.events import broadcast_event
        await broadcast_event("settings_updated",
                              {"key": "hub_host_propagation", "value": effective,
                               "applied": applied})
    except ImportError:
        pass

    return {"hub_host": effective, "applied": applied}
