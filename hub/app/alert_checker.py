# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Alertes admin sur seuils — poussées via le canal messages Hub.

Audit 08/11/2026 : le Hub n'avait AUCUNE alerte poussée (tout en pull via
le dashboard). Ce vérificateur périodique collecte des métriques légères
(compteurs mémoire + error_logs DB), évalue des seuils configurables et
livre les alertes aux admins via user_messages (priorités) + SSE.

Partie pure testable : evaluate_alerts(metrics, thresholds, state, now).
Seuils/cooldown : Settings (alert_*), defaults conservateurs.
"""
import asyncio
import logging
import time
from typing import Dict, List

from app.config import get_settings

logger = logging.getLogger(__name__)

# État en mémoire : {alert_key: dernier envoi} et {slug: première observation offline}
_alert_state: Dict[str, float] = {}
_offline_since: Dict[str, float] = {}

_alert_task = None


# ---------------------------------------------------------------------------
# Partie pure (testée) : seuils + cooldown
# ---------------------------------------------------------------------------

def evaluate_alerts(
    metrics: dict,
    thresholds: dict,
    state: Dict[str, float],
    now: float,
) -> List[dict]:
    """Évalue les seuils et retourne les alertes à livrer.

    - `state` est muté : chaque alerte envoyée y enregistre son horodatage.
    - Une même clé ne re-déclenche pas avant `cooldown_min` minutes.
    """
    alerts: List[dict] = []
    cooldown_s = thresholds["cooldown_min"] * 60

    def fire(key: str, title: str, body: str, priority: str) -> None:
        last = state.get(key)
        if last is not None and now - last < cooldown_s:
            return
        state[key] = now
        alerts.append({"key": key, "title": title, "body": body, "priority": priority})

    if metrics["error_count_10m"] > thresholds["error_rate_10m"]:
        fire(
            "error_rate",
            "⚡ Pic d'erreurs Hub",
            f"{metrics['error_count_10m']} erreurs enregistrées en 10 min "
            f"(seuil {thresholds['error_rate_10m']}). Voir dashboard Audit → Erreurs.",
            "warning",
        )

    if metrics["auth_failures_10m"] > thresholds["auth_failures_10m"]:
        fire(
            "auth_failures",
            "🔐 Échecs d'authentification en rafale",
            f"{metrics['auth_failures_10m']} échecs 401/403 API en 10 min "
            f"(seuil {thresholds['auth_failures_10m']}).",
            "warning",
        )

    if metrics["sse_auth_failures_1h"] > thresholds["sse_auth_failures_1h"]:
        fire(
            "sse_auth_failures",
            "🔐 Storm 401 sur le flux SSE",
            f"{metrics['sse_auth_failures_1h']} échecs d'auth SSE en 1 h "
            f"(seuil {thresholds['sse_auth_failures_1h']}). Typique post-boot "
            f"(beacon régénéré) ou tokens expirés d'apps legacy.",
            "warning",
        )

    if metrics["sse_clients"] > thresholds["sse_clients_max"]:
        fire(
            "sse_capacity",
            "🚨 Charge SSE élevée",
            f"{metrics['sse_clients']} clients SSE connectés "
            f"(seuil {thresholds['sse_clients_max']}).",
            "urgent",
        )

    if metrics["db_write_errors_1h"] > thresholds["db_write_errors_1h"]:
        fire(
            "db_write_errors",
            "🚨 Erreurs d'écriture DB",
            f"{metrics['db_write_errors_1h']} erreurs d'écriture DB en 1 h "
            f"(deadlocks ?). Vérifier les logs hub-api.",
            "urgent",
        )

    for slug, minutes in metrics["apps_offline_min"].items():
        if minutes > thresholds["app_offline_min"]:
            fire(
                f"app_offline:{slug}",
                f"🚨 App offline : {slug}",
                f"L'app {slug} est injoignable depuis ~{int(minutes)} min.",
                "urgent",
            )

    return alerts


def thresholds_from_settings() -> dict:
    s = get_settings()
    return {
        "error_rate_10m": s.alert_error_rate_10m,
        "auth_failures_10m": s.alert_auth_failures_10m,
        "sse_auth_failures_1h": s.alert_sse_auth_failures_1h,
        "sse_clients_max": s.alert_sse_clients_max,
        "app_offline_min": s.alert_app_offline_min,
        "db_write_errors_1h": s.alert_db_write_errors_1h,
        "cooldown_min": s.alert_cooldown_min,
    }


# ---------------------------------------------------------------------------
# Collecte + livraison
# ---------------------------------------------------------------------------

async def collect_metrics() -> dict:
    """Collecte légère : 2 comptes error_logs + compteurs mémoire + santé apps."""
    from datetime import timedelta

    from sqlalchemy import func, select

    from app.database import async_session_maker
    from app.health_checker import get_all_health_statuses
    from app.models import ErrorLog, utcnow
    from app.routes.events import (
        _connected_clients,
        db_write_errors_last_hour,
        sse_auth_failures_last_hour,
    )

    cutoff = utcnow() - timedelta(minutes=10)
    async with async_session_maker() as db:
        error_count = (
            await db.execute(
                select(func.count()).select_from(ErrorLog).where(ErrorLog.created_at > cutoff)
            )
        ).scalar() or 0
        auth_failures = (
            await db.execute(
                select(func.count()).select_from(ErrorLog).where(
                    ErrorLog.created_at > cutoff,
                    ErrorLog.error_code.in_([401, 403]),
                )
            )
        ).scalar() or 0

    now = time.time()
    for slug, info in get_all_health_statuses().items():
        status = (info or {}).get("status", "healthy")
        if status != "healthy":
            _offline_since.setdefault(slug, now)
        else:
            _offline_since.pop(slug, None)

    apps_offline_min = {
        slug: (now - since) / 60 for slug, since in _offline_since.items()
    }

    return {
        "error_count_10m": int(error_count),
        "auth_failures_10m": int(auth_failures),
        "sse_auth_failures_1h": sse_auth_failures_last_hour(),
        "sse_clients": len(_connected_clients),
        "db_write_errors_1h": db_write_errors_last_hour(),
        "apps_offline_min": apps_offline_min,
    }


async def deliver_alerts(alerts: List[dict]) -> int:
    """Livre les alertes aux admins : UserMessage persistée + SSE temps réel."""
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import User, UserMessage
    from app.routes.events import send_to_user

    if not alerts:
        return 0

    async with async_session_maker() as db:
        admins = (
            await db.execute(
                select(User).where(User.is_admin == True, User.is_active == True)  # noqa: E712
            )
        ).scalars().all()
        if not admins:
            logger.warning("⚠️ Alerte(s) à livrer mais aucun admin actif trouvé")
            return 0
        for alert in alerts:
            for admin in admins:
                db.add(UserMessage(
                    sender_type="system",
                    sender_id=None,
                    target_type="user",
                    target_user_id=admin.id,
                    target_username=admin.username,
                    priority=alert["priority"],
                    title=alert["title"][:200],
                    body=alert["body"],
                    total_targets=1,
                ))
        await db.commit()

    # Temps réel (ne casse jamais la boucle d'alerte)
    for alert in alerts:
        for admin in admins:
            try:
                await send_to_user(
                    admin.id,
                    "user_message",
                    {"title": alert["title"], "priority": alert["priority"],
                     "key": alert["key"]},
                )
            except Exception:
                pass
    return len(alerts)


async def run_alert_check() -> int:
    metrics = await collect_metrics()
    alerts = evaluate_alerts(metrics, thresholds_from_settings(), _alert_state, time.time())
    if alerts:
        delivered = await deliver_alerts(alerts)
        logger.warning(f"🔔 {delivered} alerte(s) admin livrée(s): {[a['key'] for a in alerts]}")
        return delivered
    return 0


async def periodic_alert_check():
    """Boucle périodique — démarre via le lifespan (alert_check_interval_s)."""
    interval = get_settings().alert_check_interval_s
    if interval <= 0:
        logger.info("🔔 Alertes sur seuils désactivées (ALERT_CHECK_INTERVAL_S=0)")
        return
    logger.info(f"🔔 Alertes sur seuils activées (vérification toutes les {interval}s)")
    while True:
        try:
            await asyncio.sleep(interval)
            await run_alert_check()
        except asyncio.CancelledError:
            logger.info("🛑 Vérificateur d'alertes arrêté")
            break
        except Exception as e:
            logger.warning(f"⚠️ Erreur vérification alertes: {e}")


def start_alert_checker_task():
    global _alert_task
    if _alert_task is None or _alert_task.done():
        try:
            loop = asyncio.get_running_loop()
            _alert_task = loop.create_task(periodic_alert_check())
        except RuntimeError:
            pass


def stop_alert_checker_task():
    global _alert_task
    if _alert_task is not None and not _alert_task.done():
        _alert_task.cancel()
    _alert_task = None
