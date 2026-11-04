# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests get_hub_host() en exécution conteneurisée (bug 2026-10-03).

En conteneur Docker, l'IP LAN de l'hôte n'est JAMAIS assignée aux interfaces
du conteneur : l'ancien check « interface locale » invalidait systématiquement
HUB_HOST et poussait l'IP interne Docker (10.42.x.x) aux apps déployées —
health check KO, hub_connected=false, pas d'auto-enregistrement.

Sémantique retenue : en conteneur, la valeur configurée (.env du poste hôte)
est conservée telle quelle ; la validation anti-déplacement garde du sens
hors conteneur uniquement (voir test_hub_host_locality.py).
"""
import pytest

from app import config as cfg


@pytest.fixture(autouse=True)
def _clean_caches():
    cfg._clear_local_ip_cache()
    cfg._stale_hub_host_warned.clear()
    yield
    cfg._clear_local_ip_cache()
    cfg._stale_hub_host_warned.clear()


class TestGetHubHostInContainer:
    def test_configured_ip_conserved_in_container(self, monkeypatch):
        """Cas nominal du parc : hub-api en conteneur, HUB_HOST=IP LAN hôte.

        L'IP n'est pas sur une interface du conteneur (pas « locale ») mais
        elle doit être conservée — c'est l'adresse à laquelle les apps joignent
        le Hub via les ports publiés.
        """
        monkeypatch.setattr(cfg, "_running_in_container", lambda: True)
        monkeypatch.setattr(cfg, "_is_local_ip", lambda ip: False)
        monkeypatch.setattr(cfg, "get_server_ip",
                            lambda: pytest.fail("pas de fallback en conteneur"))
        s = cfg.Settings(hub_host="192.0.2.10")
        assert s.get_hub_host() == "192.0.2.10"

    def test_local_ip_wins_even_in_container(self, monkeypatch):
        """IP sur interface locale (ex: 127.0.0.1 en réseau host) : inchangé."""
        monkeypatch.setattr(cfg, "_running_in_container", lambda: True)
        monkeypatch.setattr(cfg, "_is_local_ip", lambda ip: True)
        s = cfg.Settings(hub_host="192.0.2.10")
        assert s.get_hub_host() == "192.0.2.10"

    def test_hostname_untouched_in_container(self, monkeypatch):
        """Un hostname n'est jamais vérifié réseau, conteneur ou non."""
        monkeypatch.setattr(cfg, "_running_in_container", lambda: True)
        s = cfg.Settings(hub_host="hub.monlab.local")
        assert s.get_hub_host() == "hub.monlab.local"

    def test_empty_host_falls_back_to_detection(self, monkeypatch):
        """Pas de HUB_HOST configuré : auto-détection (comportement inchangé)."""
        monkeypatch.setattr(cfg, "_running_in_container", lambda: True)
        monkeypatch.setattr(cfg, "get_server_ip", lambda: "10.42.0.3")
        s = cfg.Settings(hub_host="")
        assert s.get_hub_host() == "10.42.0.3"

    def test_container_notice_logged_once(self, monkeypatch, caplog):
        """Le message « conservé en conteneur » n'est loggé qu'une fois (le
        getter est appelé à chaque requête de config)."""
        import logging
        monkeypatch.setattr(cfg, "_running_in_container", lambda: True)
        monkeypatch.setattr(cfg, "_is_local_ip", lambda ip: False)
        s = cfg.Settings(hub_host="192.0.2.10")
        with caplog.at_level(logging.INFO, logger="app.config"):
            s.get_hub_host()
            s.get_hub_host()
        notices = [r for r in caplog.records if "192.0.2.10" in r.message]
        assert len(notices) == 1


class TestRunningInContainer:
    def test_dockerenv_detecte(self, monkeypatch):
        monkeypatch.setattr(cfg.Path, "exists",
                            lambda self: str(self) == "/.dockerenv")
        assert cfg._running_in_container() is True

    def test_env_podman_detecte(self, monkeypatch):
        monkeypatch.setattr(cfg.Path, "exists", lambda self: False)
        monkeypatch.setenv("container", "podman")
        assert cfg._running_in_container() is True

    def test_hote_nu_non_detecte(self, monkeypatch):
        monkeypatch.setattr(cfg.Path, "exists", lambda self: False)
        monkeypatch.delenv("container", raising=False)
        assert cfg._running_in_container() is False
