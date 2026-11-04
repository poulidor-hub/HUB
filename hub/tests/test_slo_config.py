# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests des vars de config SLO (Single Logout poste partagé)."""
from app.config import Settings


def test_slo_defaults(monkeypatch):
    """Kill-switch OFF par défaut — doit être activé explicitement via .env
    après validation e2e atelier (cf. spec SLO §10 Phase 4).

    Isolation : on neutralise .env ET les env vars du shell — ce test
    vérifie le défaut DU CODE, pas la config de la machine qui l'exécute
    (un .env local avec OIDC_SLO_ENABLED=true est légitime et ne doit pas
    faire échouer la suite)."""
    monkeypatch.delenv("OIDC_SLO_ENABLED", raising=False)
    monkeypatch.delenv("OIDC_SLO_IFRAME_TIMEOUT_MS", raising=False)
    s = Settings(_env_file=None)
    assert s.oidc_slo_enabled is False
    assert s.oidc_slo_iframe_timeout_ms == 1500


def test_slo_enabled_via_env(monkeypatch):
    """Activation via env var OIDC_SLO_ENABLED=true."""
    monkeypatch.setenv("OIDC_SLO_ENABLED", "true")
    s = Settings()
    assert s.oidc_slo_enabled is True


def test_slo_disabled_via_env(monkeypatch):
    """Désactivation explicite via env var."""
    monkeypatch.setenv("OIDC_SLO_ENABLED", "false")
    s = Settings()
    assert s.oidc_slo_enabled is False
