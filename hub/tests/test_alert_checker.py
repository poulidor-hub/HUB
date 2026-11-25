# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Logique d'évaluation des alertes admin sur seuils (app/alert_checker.py).

Audit 08/11/2026 : le Hub n'avait AUCUNE alerte poussée — tout était en
pull via le dashboard. evaluate_alerts est la partie pure (seuils +
cooldown) ; la livraison passe par le canal messages vers les admins.
"""
import pytest

from app.alert_checker import evaluate_alerts

THRESHOLDS = {
    "error_rate_10m": 30,
    "auth_failures_10m": 20,
    "sse_auth_failures_1h": 100,
    "sse_clients_max": 500,
    "app_offline_min": 5,
    "db_write_errors_1h": 10,
    "cooldown_min": 30,
}
NOW = 1_000_000.0


def _metrics(**overrides):
    base = {
        "error_count_10m": 0,
        "auth_failures_10m": 0,
        "sse_auth_failures_1h": 0,
        "sse_clients": 0,
        "db_write_errors_1h": 0,
        "apps_offline_min": {},
    }
    base.update(overrides)
    return base


def _keys(alerts):
    return [a["key"] for a in alerts]


def test_no_alert_below_thresholds():
    state = {}
    alerts = evaluate_alerts(_metrics(), THRESHOLDS, state, NOW)
    assert alerts == []
    assert state == {}


def test_error_rate_spike_alerts_once():
    state = {}
    alerts = evaluate_alerts(_metrics(error_count_10m=31), THRESHOLDS, state, NOW)
    assert _keys(alerts) == ["error_rate"]
    assert alerts[0]["priority"] == "warning"


def test_auth_failures_alert():
    state = {}
    alerts = evaluate_alerts(_metrics(auth_failures_10m=21), THRESHOLDS, state, NOW)
    assert _keys(alerts) == ["auth_failures"]


def test_sse_auth_storm_alert():
    state = {}
    alerts = evaluate_alerts(_metrics(sse_auth_failures_1h=101), THRESHOLDS, state, NOW)
    assert _keys(alerts) == ["sse_auth_failures"]


def test_sse_capacity_alert_is_urgent():
    state = {}
    alerts = evaluate_alerts(_metrics(sse_clients=501), THRESHOLDS, state, NOW)
    assert _keys(alerts) == ["sse_capacity"]
    assert alerts[0]["priority"] == "urgent"


def test_db_write_errors_alert():
    state = {}
    alerts = evaluate_alerts(_metrics(db_write_errors_1h=11), THRESHOLDS, state, NOW)
    assert _keys(alerts) == ["db_write_errors"]
    assert alerts[0]["priority"] == "urgent"


def test_app_offline_alert_with_slug_key():
    state = {}
    alerts = evaluate_alerts(
        _metrics(apps_offline_min={"mon-app": 7}), THRESHOLDS, state, NOW
    )
    assert _keys(alerts) == ["app_offline:mon-app"]
    assert alerts[0]["priority"] == "warning"
    assert alerts[0]["title"] == "🔧 App injoignable : mon-app"


def test_app_offline_below_threshold_no_alert():
    state = {}
    alerts = evaluate_alerts(
        _metrics(apps_offline_min={"mon-app": 2}), THRESHOLDS, state, NOW
    )
    assert alerts == []


def test_app_offline_fires_once_per_episode():
    """Une SEULE alerte par épisode offline — plus de répétition post-cooldown.

    Les clés app_offline:* sont transition-only : le cooldown_min ne leur
    s'applique pas, mais elles ne re-déclenchent pas tant que l'épisode dure.
    """
    state = {}
    first = evaluate_alerts(
        _metrics(apps_offline_min={"mon-app": 7}), THRESHOLDS, state, NOW
    )
    assert _keys(first) == ["app_offline:mon-app"]
    # même épisode 1 min plus tard : silence
    second = evaluate_alerts(
        _metrics(apps_offline_min={"mon-app": 7}), THRESHOLDS, state, NOW + 60
    )
    assert second == []
    # même épisode bien après le cooldown : toujours silence
    third = evaluate_alerts(
        _metrics(apps_offline_min={"mon-app": 7}), THRESHOLDS, state,
        NOW + THRESHOLDS["cooldown_min"] * 60 + 1,
    )
    assert third == []


def test_app_offline_rearms_after_recovery():
    """Ré-armement à la récupération : slug sorti du tracking → clé purgée,
    un nouvel épisode offline re-déclenche une alerte."""
    state = {}
    first = evaluate_alerts(
        _metrics(apps_offline_min={"mon-app": 7}), THRESHOLDS, state, NOW
    )
    assert _keys(first) == ["app_offline:mon-app"]
    # récupération : plus rien d'offline → 0 alerte
    recovered = evaluate_alerts(_metrics(), THRESHOLDS, state, NOW + 60)
    assert recovered == []
    # 2e épisode : le slug re-passe offline 7 min → nouvelle alerte
    # (malgré un clock bien antérieur à l'expiration du cooldown)
    second = evaluate_alerts(
        _metrics(apps_offline_min={"mon-app": 7}), THRESHOLDS, state, NOW + 600
    )
    assert _keys(second) == ["app_offline:mon-app"]


def test_app_offline_below_threshold_does_not_arm():
    """Sous le seuil : aucune alerte ET aucun armement (la clé n'entre pas
    dans state) — l'armement ne se fait qu'au-delà du seuil."""
    state = {}
    alerts = evaluate_alerts(
        _metrics(apps_offline_min={"mon-app": 2}), THRESHOLDS, state, NOW
    )
    assert alerts == []
    assert "app_offline:mon-app" not in state


# =============================================================================
# TRACKING OFFLINE — collect_metrics n'alimente _offline_since que pour les
# statuts 'unhealthy' (les apps 'stopped' / 'healthy' en sortent — statut
# Docker classé par le health_checker)
# =============================================================================

@pytest.mark.asyncio
async def test_collect_metrics_ignores_stopped_status(monkeypatch):
    """Une app volontairement arrêtée ('stopped') ne doit PAS armer le
    tracking offline — seul 'unhealthy' (conteneur actif mais injoignable)
    alimente _offline_since ; 'healthy' purge le slug."""
    import app.alert_checker as alert_checker
    import app.health_checker as health_checker

    monkeypatch.setattr(
        health_checker,
        "get_all_health_statuses",
        lambda: {"a": {"status": "stopped"}, "b": {"status": "unhealthy"}},
    )
    alert_checker._offline_since = {}
    try:
        await alert_checker.collect_metrics()
        assert "a" not in alert_checker._offline_since  # stopped → ignorée
        assert "b" in alert_checker._offline_since      # unhealthy → trackée

        monkeypatch.setattr(
            health_checker,
            "get_all_health_statuses",
            lambda: {"a": {"status": "healthy"}, "b": {"status": "healthy"}},
        )
        await alert_checker.collect_metrics()
        assert alert_checker._offline_since == {}  # healthy → purge
    finally:
        alert_checker._offline_since = {}


def test_cooldown_suppresses_repeat_within_window():
    state = {}
    first = evaluate_alerts(_metrics(error_count_10m=31), THRESHOLDS, state, NOW)
    assert _keys(first) == ["error_rate"]
    # même alerte 1 min plus tard : silence
    second = evaluate_alerts(
        _metrics(error_count_10m=31), THRESHOLDS, state, NOW + 60
    )
    assert second == []


def test_cooldown_expires_after_window():
    state = {}
    evaluate_alerts(_metrics(error_count_10m=31), THRESHOLDS, state, NOW)
    later = NOW + THRESHOLDS["cooldown_min"] * 60 + 1
    again = evaluate_alerts(_metrics(error_count_10m=31), THRESHOLDS, state, later)
    assert _keys(again) == ["error_rate"]


def test_other_keys_fire_during_cooldown():
    state = {}
    evaluate_alerts(_metrics(error_count_10m=31), THRESHOLDS, state, NOW)
    alerts = evaluate_alerts(_metrics(sse_clients=501), THRESHOLDS, state, NOW + 60)
    assert _keys(alerts) == ["sse_capacity"]


# =============================================================================
# ACTIONS — deeplink Audit sur les alertes éligibles (notif actionnable)
# =============================================================================
# Format figé par l'intégrateur : action_url = "#audit?tab=<onglet>&f_<clé>=
# <valeur>" ; f_* deviennent les filtres de l'onglet (auditAlertDetails attend
# {"tab", "filters"}). Une alerte dont les lignes ne sont PAS visibles dans
# l'onglet ciblé n'a PAS de bouton (ex. app_offline).

def _alert_by_key(alerts, key):
    return next(a for a in alerts if a["key"] == key)


def test_error_rate_alert_links_to_audit_errors_tab():
    """Pic d'erreurs → onglet Erreurs de l'Audit, sans filtre (tous types)."""
    alerts = evaluate_alerts(_metrics(error_count_10m=31), THRESHOLDS, {}, NOW)
    a = _alert_by_key(alerts, "error_rate")
    assert a["action_url"] == "#audit?tab=errors"
    assert a["action_label"]


def test_auth_failures_alert_links_to_401_errors():
    """Rafale d'auth → Erreurs 401 (code dominant : tokens expirés, logins)."""
    alerts = evaluate_alerts(_metrics(auth_failures_10m=21), THRESHOLDS, {}, NOW)
    a = _alert_by_key(alerts, "auth_failures")
    assert a["action_url"] == "#audit?tab=errors&f_code=401"
    assert a["action_label"]


def test_sse_auth_storm_alert_has_no_action():
    """Storm 401 SSE : PAS de bouton.

    Les 401 sur /api/events/stream sont explicitement exclus de error_logs
    (http_exception_handler, main.py) et seulement comptés en mémoire →
    l'onglet Erreurs de l'Audit n'afficherait aucune ligne correspondante.
    """
    alerts = evaluate_alerts(_metrics(sse_auth_failures_1h=101), THRESHOLDS, {}, NOW)
    a = _alert_by_key(alerts, "sse_auth_failures")
    assert "action_url" not in a
    assert "action_label" not in a


def test_sse_capacity_alert_has_no_action():
    """Charge SSE : compteur mémoire de clients connectés, rien dans l'Audit."""
    alerts = evaluate_alerts(_metrics(sse_clients=501), THRESHOLDS, {}, NOW)
    a = _alert_by_key(alerts, "sse_capacity")
    assert "action_url" not in a
    assert "action_label" not in a


def test_db_write_errors_alert_has_no_action():
    """Erreurs d'écriture DB : compteur mémoire d'échecs sur chemins SSE —
    ces lignes ne peuvent pas figurer dans error_logs (la DB a échoué)."""
    alerts = evaluate_alerts(_metrics(db_write_errors_1h=11), THRESHOLDS, {}, NOW)
    a = _alert_by_key(alerts, "db_write_errors")
    assert "action_url" not in a
    assert "action_label" not in a


def test_app_offline_alert_has_no_action():
    """App offline : aucune ligne Audit correspondante (état santé, pas d'erreur HTTP)."""
    alerts = evaluate_alerts(
        _metrics(apps_offline_min={"mon-app": 7}), THRESHOLDS, {}, NOW
    )
    a = _alert_by_key(alerts, "app_offline:mon-app")
    assert "action_url" not in a
    assert "action_label" not in a


# =============================================================================
# LIVRAISON — deliver_alerts (persisted UserMessage + SSE temps réel)
# =============================================================================

@pytest.mark.asyncio
async def test_deliver_alerts_sse_payload_includes_message_id(
    monkeypatch, async_session, admin_user
):
    """Le payload SSE d'une alerte transporte l'id du UserMessage persisté.

    Incident 09/11/2026 : deliver_alerts émettait un payload SANS id → le front
    (messages-inbox.js) retombait sur Date.now() comme id et rappelait
    POST /api/messages/<timestamp>/read → overflow int32 côté asyncpg (500 en
    boucle sur /read, pic d'erreurs Hub).
    """
    import app.routes.events as events

    sent = []

    async def fake_send_to_user(user_id, event, payload):
        sent.append((user_id, event, payload))

    monkeypatch.setattr(events, "send_to_user", fake_send_to_user)

    from app.alert_checker import deliver_alerts

    alerts = [{
        "key": "error_rate",
        "priority": "warning",
        "title": "Pic d'erreurs Hub",
        "body": "42 erreurs enregistrées en 10 min",
    }]
    delivered = await deliver_alerts(alerts)
    assert delivered == 1

    # L'alerte est persistée en UserMessage...
    from sqlalchemy import select
    from app.models import UserMessage
    msg = (await async_session.execute(select(UserMessage))).scalars().first()
    assert msg is not None

    # ...et le SSE transporte CE même id (sinon le front marque un id inventé en lu)
    assert len(sent) == 1
    user_id, event, payload = sent[0]
    assert user_id == admin_user.id
    assert event == "user_message"
    assert payload["id"] == msg.id
    assert payload["title"] == "Pic d'erreurs Hub"
    assert payload["priority"] == "warning"
    assert payload["key"] == "error_rate"


@pytest.mark.asyncio
async def test_deliver_alerts_persists_action_fields(monkeypatch, async_session, admin_user):
    """deliver_alerts copie action_url/action_label de l'alerte vers le UserMessage.

    Notifs actionnables : l'admin doit pouvoir ouvrir l'onglet Audit concerné
    directement depuis la notification (bouton rendu par le front).
    """
    import app.routes.events as events

    async def fake_send_to_user(user_id, event, payload):
        pass

    monkeypatch.setattr(events, "send_to_user", fake_send_to_user)

    from app.alert_checker import deliver_alerts

    alerts = [{
        "key": "auth_failures",
        "priority": "warning",
        "title": "🔐 Échecs d'authentification en rafale",
        "body": "25 échecs 401/403 API en 10 min",
        "action_url": "#audit?tab=errors&f_code=401",
        "action_label": "Ouvrir dans l'Audit",
    }]
    delivered = await deliver_alerts(alerts)
    assert delivered == 1

    from sqlalchemy import select
    from app.models import UserMessage
    msg = (await async_session.execute(select(UserMessage))).scalars().first()
    assert msg is not None
    assert msg.action_url == "#audit?tab=errors&f_code=401"
    assert msg.action_label == "Ouvrir dans l'Audit"


@pytest.mark.asyncio
async def test_deliver_alerts_without_action_persists_null_fields(
    monkeypatch, async_session, admin_user
):
    """Alerte sans action (ex. app_offline) : champs action laissés NULL."""
    import app.routes.events as events

    async def fake_send_to_user(user_id, event, payload):
        pass

    monkeypatch.setattr(events, "send_to_user", fake_send_to_user)

    from app.alert_checker import deliver_alerts

    alerts = [{
        "key": "app_offline:mon-app",
        "priority": "urgent",
        "title": "🚨 App offline : mon-app",
        "body": "L'app mon-app est injoignable depuis ~9 min.",
    }]
    delivered = await deliver_alerts(alerts)
    assert delivered == 1

    from sqlalchemy import select
    from app.models import UserMessage
    msg = (await async_session.execute(select(UserMessage))).scalars().first()
    assert msg is not None
    assert msg.action_url is None
    assert msg.action_label is None
