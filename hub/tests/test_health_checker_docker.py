# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Classification Docker des échecs de ping du health checker (app/health_checker.py).

Une app volontairement arrêtée (conteneur exited) ne doit plus être signalée
comme une panne : check_app_health distingue désormais « stopped » (état Docker
fiable, non-running) d'« unhealthy » (conteneur running, Docker indisponible,
ou app distante). Jamais de « stopped » sur incertitude.
"""
from types import SimpleNamespace

import pytest

import app.health_checker as hc
from app.health_checker import check_app_health, get_container_state

# http://127.0.0.1:1 → connexion refusée immédiate (aucun service sur le port 1)
DEAD_URL = "http://127.0.0.1:1"


# ─── Fakes ────────────────────────────────────────────────────────────

class _FakeResponse:
    status_code = 200


class _FakeClient:
    """Client httpx minimal : répond 200 à tout GET."""

    async def get(self, url):
        return _FakeResponse()


class _FakeContainer:
    def __init__(self, status, attrs=None, labels=None):
        self.status = status
        self.attrs = attrs or {}
        self.labels = labels or {}


class _FakeDockerScanner:
    """Double du singleton docker_scanner (interface is_available / client)."""

    def __init__(self, is_available=True, containers=()):
        self._is_available = is_available
        self._containers = list(containers)

    @property
    def is_available(self):
        return self._is_available

    @property
    def client(self):
        if not self._is_available:
            return None
        return SimpleNamespace(
            containers=SimpleNamespace(list=lambda all=True: self._containers)
        )


def _healthy_ping(monkeypatch):
    """Chemin healthy : GET → 200 immédiat, sans réseau."""
    monkeypatch.setattr(hc, "_get_http_client", lambda: _FakeClient())


def _tracked_state(monkeypatch, state):
    """Patch get_container_state en traçant les appels."""
    calls = []

    def _state(slug):
        calls.append(slug)
        return state

    monkeypatch.setattr(hc, "get_container_state", _state)
    return calls


# ─── check_app_health : classification sur échec de ping ─────────────

async def test_ping_fail_container_exited_returns_stopped(monkeypatch):
    monkeypatch.setattr(hc, "get_container_state", lambda slug: "exited")
    result = await check_app_health("a", DEAD_URL)
    assert result["status"] == "stopped"
    assert result["docker_status"] == "exited"


@pytest.mark.parametrize(
    "docker_state", ["created", "paused", "restarting", "dead", "absent"]
)
async def test_ping_fail_all_non_running_states(monkeypatch, docker_state):
    monkeypatch.setattr(hc, "get_container_state", lambda slug: docker_state)
    result = await check_app_health("a", DEAD_URL)
    assert result["status"] == "stopped"
    assert result["docker_status"] == docker_state


async def test_ping_fail_container_running_returns_unhealthy(monkeypatch):
    monkeypatch.setattr(hc, "get_container_state", lambda slug: "running")
    result = await check_app_health("a", DEAD_URL)
    assert result["status"] == "unhealthy"
    assert result["docker_status"] == "running"


async def test_ping_fail_docker_unavailable_returns_unhealthy(monkeypatch):
    monkeypatch.setattr(hc, "get_container_state", lambda slug: None)
    result = await check_app_health("a", DEAD_URL)
    assert result["status"] == "unhealthy"
    assert result["docker_status"] is None


async def test_ping_fail_remote_app_not_classified_stopped(monkeypatch):
    calls = _tracked_state(monkeypatch, "exited")
    result = await check_app_health("a", DEAD_URL, docker_detectable=False)
    assert result["status"] == "unhealthy"  # panne réelle d'une app distante
    assert result["docker_status"] is None
    assert calls == []  # app distante : aucune consultation Docker


async def test_ping_ok_skips_docker_lookup(monkeypatch):
    calls = _tracked_state(monkeypatch, "exited")
    _healthy_ping(monkeypatch)
    result = await check_app_health("a", "http://fake-local/api")
    assert result["status"] == "healthy"
    assert result["docker_status"] is None
    assert calls == []  # chemin healthy : aucun appel Docker


# ─── get_container_state : mapping docker-py ─────────────────────────

def test_get_container_state_maps_docker(monkeypatch):
    # 1. Conteneur trouvé (env APP_SLUG) → statut docker-py tel quel
    found = _FakeContainer(
        "exited", attrs={"Config": {"Env": ["APP_SLUG=a", "FOO=bar"]}}
    )
    monkeypatch.setattr(
        "app.docker_scanner.docker_scanner", _FakeDockerScanner(containers=[found])
    )
    assert get_container_state("a") == "exited"

    # 2. Docker joignable mais aucun conteneur trouvé → 'absent'
    monkeypatch.setattr(
        "app.docker_scanner.docker_scanner", _FakeDockerScanner(containers=[])
    )
    assert get_container_state("a") == "absent"

    # 3. Docker indisponible → None (incertitude, jamais 'stopped')
    monkeypatch.setattr(
        "app.docker_scanner.docker_scanner", _FakeDockerScanner(is_available=False)
    )
    assert get_container_state("a") is None
