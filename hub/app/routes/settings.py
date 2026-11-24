# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Routes de paramétrage applicatif (app_settings).

Endpoints :
- GET /api/settings/{key} : Lire un paramètre public (sans auth)
- PUT /api/settings/{key} : Modifier un paramètre (admin only)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging

from app.database import get_db
from app.models import AppSetting, User, utcnow
from app.auth import get_current_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["Settings"])

# Clés accessibles sans authentification
PUBLIC_KEYS = {"account_requests_enabled"}

# Fragments de nom (insensible à la casse) désignant un réglage à valeur secrète.
# Sa valeur ne doit jamais être journalisée en clair ni diffusée par SSE : ce
# dernier part à tous les utilisateurs connectés, pas seulement aux admins.
_SECRET_KEY_FRAGMENTS = (
    "token", "secret", "password", "passphrase", "private_key", "api_key", "credential",
)


def _is_secret_setting_key(key: str) -> bool:
    """True si la clé désigne un réglage à valeur secrète (jeton, mot de passe…)."""
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS)


@router.get("/{key}")
async def get_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
):
    """Retourne la valeur d'un paramètre public."""
    if key not in PUBLIC_KEYS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paramètre inconnu")

    result = await db.execute(
        select(AppSetting).where(AppSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paramètre introuvable")

    return {"key": setting.key, "value": setting.value}


@router.put("/{key}")
async def update_setting(
    key: str,
    payload: dict,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Met à jour un paramètre applicatif (admin uniquement)."""
    raw_value = payload.get("value")
    if raw_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le champ 'value' est requis"
        )

    # Normaliser : booléens → "true"/"false", chaînes → telles quelles
    if isinstance(raw_value, bool):
        value = "true" if raw_value else "false"
    elif isinstance(raw_value, str) and raw_value.lower() in ("true", "false"):
        value = raw_value.lower()
    elif isinstance(raw_value, str):
        value = raw_value
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La valeur doit être un booléen ou une chaîne"
        )

    result = await db.execute(
        select(AppSetting).where(AppSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    if not setting:
        # Upsert : créer le paramètre s'il n'existe pas encore
        setting = AppSetting(key=key, value=value)
        db.add(setting)
    else:
        setting.value = value

    setting.updated_at = utcnow()
    setting.updated_by_id = current_admin.id
    await db.commit()

    # Invalider le cache du token GitHub si concerné
    if key == "github_token":
        try:
            from app.github_client import invalidate_token_cache
            invalidate_token_cache()
        except ImportError:
            pass

    is_secret = _is_secret_setting_key(key)

    # Un réglage secret n'est jamais journalisé en clair : la mise à jour reste
    # tracée par sa clé.
    if is_secret:
        logger.info(f"Setting '{key}' mis à jour (valeur masquée) par {current_admin.username}")
    else:
        logger.info(f"Setting '{key}' mis à jour à '{value}' par {current_admin.username}")

    # Broadcast SSE — un réglage secret part sans sa valeur : l'événement est
    # diffusé à tous les clients connectés, admin ou non. Le frontend n'utilise
    # la valeur que pour les clés ordinaires (sse.js, case 'settings_updated').
    event_data = {"key": key}
    if not is_secret:
        event_data["value"] = value
    try:
        from app.routes.events import broadcast_event
        await broadcast_event("settings_updated", event_data)
    except ImportError:
        pass

    return {"key": setting.key, "value": setting.value}
