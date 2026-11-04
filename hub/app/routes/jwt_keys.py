# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Endpoints admin du trousseau de clés JWT (spec 2026-09-16 §5.5).

Rotation planifiée, révocation d'urgence, vue d'ensemble, nettoyage.
Audit : les mutations loguent l'opérateur (logger.warning dans jwt_keyring
+ info ici avec le username).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import jwt_keyring
from app.auth import get_current_admin
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/keys", tags=["auth-keys"])


class RevokeRequest(BaseModel):
    kid: str


@router.get("")
async def list_keys(current_admin: User = Depends(get_current_admin)):
    """Vue d'ensemble du trousseau : kid, statut dérivé, dates, publié-ou-pas."""
    now = jwt_keyring._now()
    keys = []
    for entry in jwt_keyring._load_entries():
        keys.append({
            "kid": entry.kid,
            "status": entry.effective_status(now),
            "published": entry.is_published(now),
            "created_at": entry.created_at,
            "activated_at": entry.activated_at,
            "retired_at": entry.retired_at,
        })
    return {"keys": keys}


@router.post("/rotate")
async def rotate_key(current_admin: User = Depends(get_current_admin)):
    """Rotation : nouvelle clé active, l'ancienne en overlap (défaut 24 h)."""
    result = jwt_keyring.rotate()
    logger.info("Rotation JWT déclenchée par %s : %s", current_admin.username, result)
    return result


@router.post("/revoke")
async def revoke_key(body: RevokeRequest, current_admin: User = Depends(get_current_admin)):
    """Révocation d'urgence (mode compromission). 409 si la cible est active."""
    try:
        jwt_keyring.revoke(body.kid)
    except jwt_keyring.KeyringError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    logger.info("Révocation JWT %s déclenchée par %s", body.kid, current_admin.username)
    return {"revoked": body.kid}


@router.post("/cleanup")
async def cleanup_keys(
    older_than_months: int = Query(12, ge=1),
    current_admin: User = Depends(get_current_admin),
):
    """Supprime les PEM archived/revoked au-delà du seuil (opt-in, jamais auto)."""
    removed = jwt_keyring.cleanup(older_than_months=older_than_months)
    return {"removed": removed}
