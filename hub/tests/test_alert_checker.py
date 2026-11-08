# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Logique d'évaluation des alertes admin sur seuils (app/alert_checker.py).

Audit 08/11/2026 : le Hub n'avait AUCUNE alerte poussée — tout était en
pull via le dashboard. evaluate_alerts est la partie pure (seuils +
cooldown) ; la livraison passe par le canal messages vers les admins.
"""
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
        _metrics(apps_offline_min={"exemple-app": 7}), THRESHOLDS, state, NOW
    )
    assert _keys(alerts) == ["app_offline:exemple-app"]
    assert alerts[0]["priority"] == "urgent"


def test_app_offline_below_threshold_no_alert():
    state = {}
    alerts = evaluate_alerts(
        _metrics(apps_offline_min={"exemple-app": 2}), THRESHOLDS, state, NOW
    )
    assert alerts == []


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
