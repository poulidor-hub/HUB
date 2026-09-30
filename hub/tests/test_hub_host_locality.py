"""Robustesse du hub-host face au déplacement du Hub.

Un HUB_HOST configuré (env ou DB app_settings) hérité d'un clone, d'un
déplacement de poste ou d'une restauration PRA peut pointer une IP qui
n'existe plus sur le poste courant. Le Hub ne doit jamais servir cette
ancienne IP aux apps (elles la resynchronisent via /api/config/hub-host) :
il retombe sur l'auto-détection de l'IP locale.
"""
import logging

import pytest

from app.config import (
    _clear_local_ip_cache,
    _is_local_ip,
    get_settings,
)
import app.config as cfg


@pytest.fixture(autouse=True)
def _restore_settings_and_cache():
    """Les tests mutent le singleton get_settings() — restauration systématique."""
    settings = get_settings()
    saved = settings.hub_host
    _clear_local_ip_cache()
    cfg._stale_hub_host_warned.clear()
    yield
    settings.hub_host = saved
    _clear_local_ip_cache()
    cfg._stale_hub_host_warned.clear()


def _frozen_detection(monkeypatch, ip: str = "10.99.0.42") -> str:
    monkeypatch.setattr("app.config.get_server_ip", lambda: ip)
    return ip


# --- IP configurée absente du poste → auto-détection ---


def test_stale_configured_ip_falls_back_to_detection(monkeypatch):
    detected = _frozen_detection(monkeypatch)
    monkeypatch.setattr("app.config._is_local_ip", lambda ip: False)
    settings = get_settings()
    settings.hub_host = "203.0.113.9"  # IP héritée d'un autre poste
    assert settings.get_hub_host() == detected


def test_stale_configured_ip_logs_warning_once(monkeypatch, caplog):
    _frozen_detection(monkeypatch)
    monkeypatch.setattr("app.config._is_local_ip", lambda ip: False)
    settings = get_settings()
    settings.hub_host = "203.0.113.9"
    with caplog.at_level(logging.WARNING, logger="app.config"):
        settings.get_hub_host()
        settings.get_hub_host()
    warnings = [r for r in caplog.records if "203.0.113.9" in r.message]
    assert len(warnings) == 1  # get_hub_host est appelé à chaque requête : 1 warning max


def test_local_configured_ip_is_kept(monkeypatch):
    monkeypatch.setattr("app.config._is_local_ip", lambda ip: True)
    settings = get_settings()
    settings.hub_host = "192.0.2.10"
    assert settings.get_hub_host() == "192.0.2.10"


def test_configured_hostname_is_kept_untouched(monkeypatch):
    """Un hostname (DNS) reste valable quel que soit le poste : aucun check réseau."""
    monkeypatch.setattr(
        "app.config._is_local_ip",
        lambda ip: pytest.fail("un hostname ne doit pas être vérifié en réseau"),
    )
    settings = get_settings()
    settings.hub_host = "hub.monlab.local"
    assert settings.get_hub_host() == "hub.monlab.local"


def test_empty_host_falls_back_to_detection(monkeypatch):
    detected = _frozen_detection(monkeypatch)
    settings = get_settings()
    settings.hub_host = ""
    assert settings.get_hub_host() == detected


# --- _is_local_ip : comportement réel (UDP connect, aucun paquet émis) ---


def test_is_local_ip_true_for_loopback():
    assert _is_local_ip("127.0.0.1") is True


def test_is_local_ip_false_for_unassigned_address():
    # IP de documentation (RFC 5737) : ne peut pas être une interface locale
    assert _is_local_ip("203.0.113.9") is False
