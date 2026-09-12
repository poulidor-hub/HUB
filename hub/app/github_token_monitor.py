"""Moniteur de vivacité du token GitHub — tâche périodique + alerte admins.

Contexte (12 septembre 2026) : le PAT GitHub du parc est resté invalide
plusieurs heures sans que personne ne le voie — le hub survivait sur son
cache de catalogue, toutes les fonctions GitHub live dégradées.

Principe : toutes les GITHUB_TOKEN_CHECK_INTERVAL secondes, un GET
https://api.github.com/user avec le token courant (env prioritaire, sinon
app_settings.github_token). Alerte aux admins UNIQUEMENT sur la transition
ok→invalid (anti-spam par état persisté, pas de répétition horaire). Une
erreur réseau transitoire n'alerte pas et ne réarme pas l'anti-spam.

États de check : valid | invalid | missing | error.
"""
import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.github_client import load_db_token
from app.models import AppSetting, User, UserMessage

logger = logging.getLogger(__name__)

GITHUB_USER_URL = "https://api.github.com/user"

STATE_KEY = "github_token_alert_state"     # ok | alerted
LAST_CHECK_KEY = "github_token_last_check"  # date ISO du dernier check
LAST_RESULT_KEY = "github_token_last_result"  # valid | invalid | missing | error


def decide_alert(prev_state: str | None, result: str) -> tuple[str, bool]:
    """Fonction pure : (nouvel état, alerter ?) selon l'état précédent.

    - valid    → réarme ('ok'), jamais d'alerte (même après une alerte) ;
    - invalid  → alerte UNIQUEMENT sur la transition ok→alerted ;
    - missing  → réarme sans alerte (absence volontaire du token, visible en UI) ;
    - error    → état INCHANGÉ et muet (panne transitoire : on n'alerte pas,
      et on ne réarme pas l'anti-spam pendant une panne réseau prolongée).
    """
    prev = prev_state or "ok"
    if result == "valid":
        return "ok", False
    if result == "invalid":
        return ("alerted", False) if prev == "alerted" else ("alerted", True)
    if result == "missing":
        return "ok", False
    return prev, False  # error


async def check_token(db, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Vérifie le token courant auprès de GitHub.

    Retourne valid | invalid | missing | error (transport injectable pour
    les tests : MockTransport, zéro réseau).
    """
    settings = get_settings()
    token = settings.github_token or await load_db_token(db)
    if not token:
        return "missing"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as client:
            resp = await client.get(GITHUB_USER_URL, headers=headers)
    except httpx.HTTPError:
        return "error"
    if resp.status_code == 200:
        return "valid"
    if resp.status_code in (401, 403):
        return "invalid"
    return "error"


async def _get_setting(db, key: str) -> str | None:
    row = await db.get(AppSetting, key)
    return row.value if row else None


async def _set_setting(db, key: str, value: str) -> None:
    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await db.flush()


async def _notify_admins_invalid(db, admins_count: int) -> None:
    """Alerte audit (UserMessages créées par l'appelant)."""
    from app.routes.audit import log_action

    await log_action(
        db, None, "github_token_invalid", target="github",
        details=f"token refusé par GitHub (401/403) — {admins_count} admin(s) alerté(s)",
        notify_sse=False,
    )


async def run_check_once(db, *, transport: httpx.AsyncBaseTransport | None = None) -> tuple[str, bool]:
    """Un cycle complet : check → décision → persistance → alerte sur transition.

    Retourne (résultat du check, alerte émise ?).
    """
    result = await check_token(db, transport=transport)
    prev_state = await _get_setting(db, STATE_KEY)
    new_state, alert = decide_alert(prev_state, result)

    await _set_setting(db, STATE_KEY, new_state)
    await _set_setting(db, LAST_CHECK_KEY, datetime.now(timezone.utc).isoformat())
    await _set_setting(db, LAST_RESULT_KEY, result)

    if alert:
        result_admins = await db.execute(
            select(User).where(User.is_admin == True, User.is_active == True)  # noqa: E712
        )
        admins = result_admins.scalars().all()
        from app.routes.messages import _message_to_brief, _send_user_message_sse

        for admin in admins:
            msg = UserMessage(
                sender_type="system",
                target_type="user",
                target_user_id=admin.id,
                priority="warning",
                title="Token GitHub invalide",
                body=(
                    "Le token GitHub configuré dans le Hub est refusé par GitHub "
                    "(révoqué ou expiré). Les fonctions GitHub (catalogue, "
                    "vérification de mises à jour) sont dégradées. "
                    "Renouvelez le token depuis la vue Apps GitHub."
                ),
                action_url="/admin-apps-github",
                action_label="Corriger le token",
            )
            db.add(msg)
            await db.flush()
            try:
                await _send_user_message_sse(admin.id, _message_to_brief(msg))
            except Exception:
                logger.warning("SSE user_message non envoyé à %s", admin.username,
                               exc_info=True)

        await _notify_admins_invalid(db, len(admins))
        logger.warning("🚨 Token GitHub invalide — %d admin(s) alerté(s)", len(admins))

    await db.commit()
    return result, alert


async def periodic_github_token_check() -> None:
    """Boucle périodique — pattern periodic_docker_sync.

    Intervalle GITHUB_TOKEN_CHECK_INTERVAL (défaut 3600 s, <= 0 = désactivé).
    """
    interval = get_settings().github_token_check_interval
    if interval <= 0:
        logger.info("Moniteur token GitHub désactivé (intervalle <= 0)")
        return

    await asyncio.sleep(15)  # délai initial : ne pas ralentir le démarrage
    while True:
        try:
            from app.database import async_session_maker

            async with async_session_maker() as db:
                result, _ = await run_check_once(db)
                if result != "valid":
                    logger.info("Moniteur token GitHub : %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Erreur du moniteur de token GitHub")
        await asyncio.sleep(interval)
