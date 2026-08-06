"""Tests des vars de config SLO (Single Logout poste partagé)."""
from app.config import Settings


def test_slo_defaults():
    s = Settings()
    assert s.oidc_slo_enabled is True
    assert s.oidc_slo_iframe_timeout_ms == 1500


def test_slo_disabled_via_env(monkeypatch):
    monkeypatch.setenv("OIDC_SLO_ENABLED", "false")
    # pydantic_settings lit l'env à l'init ; on force le rechargement
    s = Settings()
    assert s.oidc_slo_enabled is False
