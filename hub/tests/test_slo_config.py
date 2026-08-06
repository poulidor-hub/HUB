"""Tests des vars de config SLO (Single Logout poste partagé)."""
from app.config import Settings


def test_slo_defaults():
    """Kill-switch OFF par défaut — doit être activé explicitement via .env
    après validation e2e atelier (cf. spec SLO §10 Phase 4)."""
    s = Settings()
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
