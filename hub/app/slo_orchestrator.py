# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""SLO v2 — Orchestrateur M2M : appelle les traducteurs pour révoquer les sessions distantes.

Au login HUB d'un user B par-dessus un user A, le HUB appelle directement
(côté serveur, via SA interne) les traducteurs des apps OIDC où A avait une
session active, pour DELETE les sessions en DB distante.

Cette approche remplace l'iframe cachée (v1) qui ne fonctionnait pas car les
SPAs externes (Vikunja) ne purgeaient pas leur localStorage automatiquement.

Le M2M casse le refresh token distant. Combiné avec un `jwtttl` court côté
app externe, l'ancien user est déconnecté dans les `jwtttl` secondes.
"""
import logging
import time
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Application

logger = logging.getLogger(__name__)

# Cache du token SA interne (token, expiry_epoch)
_sa_token_cache: tuple[Optional[str], float] = (None, 0.0)


async def _get_internal_sa_token(settings: Settings) -> Optional[str]:
    """Obtient un token M2M pour le SA 'hub-internal', avec cache.

    Returns None si les credentials ne sont pas configurés ou si l'auth échoue.
    """
    global _sa_token_cache

    # Cache valide ?
    cached_token, cached_expiry = _sa_token_cache
    if cached_token and time.time() < cached_expiry - 30:  # 30s de marge
        return cached_token

    if not settings.hub_internal_sa_client_id or not settings.hub_internal_sa_client_secret:
        logger.warning("SLO M2M: HUB_INTERNAL_SA_CLIENT_ID/SECRET non configurés")
        return None

    try:
        # Le HUB s'appelle lui-même pour obtenir un token SA
        # On utilise localhost puisque hub-api écoute sur 8000
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                "http://127.0.0.1:8000/api/auth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.hub_internal_sa_client_id,
                    "client_secret": settings.hub_internal_sa_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 900)
            if token:
                _sa_token_cache = (token, time.time() + expires_in)
                return token
    except Exception as e:
        logger.warning("SLO M2M: échec obtention token SA interne: %s", e)

    return None


async def propagate_remote_revoke(
    db: AsyncSession,
    old_identifier: str,
    remote_logout: list[dict],
    settings: Settings,
) -> None:
    """Appelle chaque traducteur pour révoquer les sessions de l'ancien user.

    L'identifiant provient du cookie hub_oidc_session : depuis v3.47.0 c'est
    un oidc_sub (immuable), pour les cookies antérieurs un username. Or les
    traducteurs indexent les sessions distantes par username courant — qui
    peut avoir dérivé après un renommage. On résout donc l'user en DB
    (resolve_user_by_oidc_sub) et on appelle le revoke pour chaque
    identifiant, dédupliqués : username courant d'abord, puis la valeur
    brute du cookie. Si l'user est introuvable (legacy), un seul appel avec
    la valeur brute.

    Best-effort : les erreurs (résolution, HTTP, réseau) sont loggées mais
    ne bloquent jamais le login.
    """
    if not remote_logout:
        return

    # Le cookie porte désormais un oidc_sub (immuable) ; les traducteurs
    # indexent les sessions par username courant — qui peut avoir dérivé
    # après un renommage. On tente les deux identifiants, dédupliqués.
    from app.auth import resolve_user_by_oidc_sub

    old_user = await resolve_user_by_oidc_sub(db, old_identifier)
    identifiers = [old_identifier]
    if old_user and old_user.username not in identifiers:
        identifiers.insert(0, old_user.username)

    # Filtrer les entrées qui ont un traducteur configuré
    entries_with_translator = [
        e for e in remote_logout if e.get("translator_slug")
    ]
    if not entries_with_translator:
        return

    sa_token = await _get_internal_sa_token(settings)
    if not sa_token:
        logger.warning(
            "SLO M2M: impossible d'obtenir token SA, skip revoke pour %d apps",
            len(entries_with_translator),
        )
        return

    for entry in entries_with_translator:
        translator_slug = entry["translator_slug"]
        if len(identifiers) > 1:
            logger.info(
                "SLO M2M: %s → double identifiant (username=%s, oidc_sub=%s)",
                translator_slug, identifiers[0], identifiers[1],
            )
        try:
            # Récupérer l'api_url du traducteur depuis la DB
            result = await db.execute(
                select(Application).where(Application.slug == translator_slug)
            )
            translator_app = result.scalar_one_or_none()
            if not translator_app or not translator_app.api_url:
                logger.warning("SLO M2M: traducteur %s sans api_url", translator_slug)
                continue

            api_url = str(translator_app.api_url).rstrip("/")

            async def _revoke_one(identifier: str) -> None:
                revoke_url = f"{api_url}/api/exemple-app/sessions/{identifier}/revoke"
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(
                        revoke_url,
                        headers={
                            "Authorization": f"Bearer {sa_token}",
                            "Content-Type": "application/json",
                        },
                    )
                    if resp.status_code == 200:
                        body = resp.json()
                        logger.info(
                            "SLO M2M: %s → %d sessions révoquées pour user=%s",
                            translator_slug,
                            body.get("deleted_sessions", 0),
                            identifier,
                        )
                    elif resp.status_code == 404:
                        logger.info(
                            "SLO M2M: %s → user %s introuvable côté distant (déjà purgé ?)",
                            translator_slug, identifier,
                        )
                    else:
                        logger.warning(
                            "SLO M2M: %s → HTTP %d pour user=%s: %s",
                            translator_slug, resp.status_code, identifier,
                            resp.text[:200],
                        )

            for identifier in identifiers:
                await _revoke_one(identifier)
        except Exception as e:
            logger.warning(
                "SLO M2M: erreur appel traducteur %s pour user=%s: %s",
                translator_slug, identifiers, e,
            )
