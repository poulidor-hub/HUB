"""SLO (Single Logout) poste partagé — détection de changement d'utilisateur au login.

Au login HUB d'un user B, si un cookie `hub_oidc_session` précédent identifie un user A
différent, on collecte la liste des apps OIDC où A avait encore un refresh token valide.
Le frontend HUB ouvrira une iframe cachée vers `{app_frontend_url}/logout` pour chacune,
pour purger la session distante de A (localStorage + cookie HttpOnly) via la SPA distante.

Spec : docs/superpowers/specs/2026-08-06-slo-shared-workstation-design.md
"""
import logging
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import resolve_user_by_oidc_sub, utcnow
from app.config import Settings
from app.models import Application, OAuthClient, OAuthRefreshToken
from app.oidc_helpers import decode_oidc_session_cookie

logger = logging.getLogger(__name__)

# Mapping app_slug → traducteur (app HUB servant d'interface M2M vers l'app externe).
# Étendre au fur et à mesure. Futur : remplacer par une table `app_translators`.
APP_TRANSLATORS: dict[str, str] = {
    "vikunja": "exemple-app",
}


def lookup_translator_slug(app_slug: str) -> Optional[str]:
    """Retourne le slug du traducteur HUB pour une app externe donnée, ou None."""
    return APP_TRANSLATORS.get(app_slug)


async def detect_user_switch_and_collect_logouts(
    request: Request,
    db: AsyncSession,
    new_username: str,
    settings: Settings,
) -> list[dict]:
    """Détecte un changement d'user au login et liste les apps OIDC à purger.

    Retourne [] si :
      - OIDC_SLO_ENABLED=False (kill-switch)
      - pas de cookie `hub_oidc_session` précédent (premier login du poste)
      - cookie invalide / expiré / mal signé
      - même user (login renouvelé)
      - ancien user introuvable en DB

    Sinon retourne une liste d'entrées :
      [{"slug": str, "frontend_url": str, "logout_url": str, "translator_slug": str | None}]
    """
    if not settings.oidc_slo_enabled:
        return []

    cookie_value = request.cookies.get("hub_oidc_session")
    if not cookie_value:
        return []

    # Le cookie porte l'oidc_sub immuable (fallback username pour les cookies
    # posés avant le déploiement, TTL 8h).
    old_sub = decode_oidc_session_cookie(cookie_value, settings)
    if not old_sub:
        return []

    old_user = await resolve_user_by_oidc_sub(db, old_sub)
    if not old_user:
        logger.warning(
            "SLO: cookie précédent sub=%r mais user introuvable en DB", old_sub
        )
        return []

    # Même user (login renouvelé) : comparaison sur le username courant de
    # l'user résolu — pas sur la valeur brute du cookie, qui est l'oidc_sub
    # et diffère du username pour un utilisateur renommé.
    if old_user.username == new_username:
        return []

    # Clients OIDC où old_user a encore un refresh token valide (non révoqué, non expiré).
    rows_result = await db.execute(
        select(OAuthRefreshToken.client_id, OAuthClient.app_id)
        .join(OAuthClient, OAuthRefreshToken.client_id == OAuthClient.client_id)
        .where(
            OAuthRefreshToken.user_id == old_user.id,
            OAuthRefreshToken.revoked_at.is_(None),
            or_(
                OAuthRefreshToken.expires_at.is_(None),
                OAuthRefreshToken.expires_at > utcnow(),
            ),
        )
        .distinct()
    )

    logouts: list[dict] = []
    for client_id, app_id in rows_result.all():
        if not app_id:
            continue
        app_result = await db.execute(select(Application).where(Application.id == app_id))
        app = app_result.scalar_one_or_none()
        if not app or not app.frontend_url:
            continue
        frontend_url = str(app.frontend_url).rstrip("/")
        logouts.append({
            "slug": app.slug,
            "frontend_url": frontend_url,
            "logout_url": f"{frontend_url}/logout",
            "translator_slug": lookup_translator_slug(app.slug),
        })

    return logouts
