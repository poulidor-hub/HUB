# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Classification Docker des échecs de ping du health checker (app/health_checker.py).

Une app volontairement arrêtée (conteneur exited) ne doit plus être signalée
comme une panne : check_app_health distingue désormais « stopped » (état Docker
fiable, non-running) d'« unhealthy » (conteneur running, Docker indisponible,
ou app distante). Jamais de « stopped » sur incertitude.
"""
import pytest

import app.health_checker as hc
from app.health_checker import (
    check_app_health, get_container_state,
    find_containers_for_app, count_containers_batch,
)

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
    def __init__(self, status, attrs=None, labels=None, name=""):
        self.status = status
        self.attrs = attrs or {}
        self.labels = labels or {}
        self.name = name


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


def _app_container(name, status="running", slug=None, project=None):
    """Conteneur fake : env APP_SLUG + label projet compose (+ hub.slug option)."""
    labels = {}
    if project:
        labels["com.docker.compose.project"] = project
    return _FakeContainer(
        status,
        attrs={"Config": {"Env": ([f"APP_SLUG={slug}"] if slug else [])}},
        labels=labels,
        name=name,
    )


def _patch_containers(monkeypatch, containers, is_available=True):
    """Injecte la liste docker-py au niveau de _list_all_containers.

    Même cible que la neutralisation autouse du conftest
    (_health_checker_docker_neutralized) : le setattr du test écrase celui de
    la fixture, jamais le Docker réel de l'hôte.
    """
    if is_available:
        monkeypatch.setattr(hc, "_list_all_containers", lambda: containers)
    else:
        monkeypatch.setattr(hc, "_list_all_containers", lambda: None)


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
    _patch_containers(monkeypatch, [found])
    assert get_container_state("a") == "exited"

    # 2. Docker joignable mais aucun conteneur trouvé → 'absent'
    _patch_containers(monkeypatch, [])
    assert get_container_state("a") == "absent"

    # 3. Docker indisponible → None (incertitude, jamais 'stopped')
    _patch_containers(monkeypatch, [], is_available=False)
    assert get_container_state("a") is None


# ─── Groupes compose : find_containers_for_app ────────────────────────

def test_find_containers_groups_by_compose_project(monkeypatch):
    """Groupe complet dérivé des projets compose : 2 conteneurs APP_SLUG +
    1 postgres (projet seul, sans APP_SLUG) — l'autre app et le Hub exclus."""
    containers = [
        _app_container("zz-autre-api", slug="autre", project="autre"),
        _app_container("exemple-app-11-api", slug="exemple-app-11", project="exemple_app_11"),
        _app_container("exemple-app-11-frontend", slug="exemple-app-11", project="exemple_app_11"),
        # postgres : PAS d'APP_SLUG, seulement le label projet compose
        _app_container("exemple-app-11-postgres", status="exited", project="exemple_app_11"),
        _app_container("hub-api", project="hub_base"),
    ]
    _patch_containers(monkeypatch, containers)

    group = find_containers_for_app("exemple-app-11")
    assert [c.name for c in group] == [
        "exemple-app-11-api", "exemple-app-11-frontend", "exemple-app-11-postgres",
    ]


def test_find_containers_excludes_hub_base_even_when_matched(monkeypatch):
    """Un conteneur du projet hub_base matché directement (env/label) est
    exclu, et n'entraîne pas les autres conteneurs de hub_base."""
    containers = [
        _app_container("hub-api", slug="exemple-app-11", project="hub_base"),
        _app_container("hub-frontend", project="hub_base"),
        _app_container("exemple-app-11-api", slug="exemple-app-11", project="exemple_app_11"),
    ]
    _patch_containers(monkeypatch, containers)

    group = find_containers_for_app("exemple-app-11")
    assert [c.name for c in group] == ["exemple-app-11-api"]


def test_find_containers_matches_label_hub_slug(monkeypatch):
    """Match par label hub.slug : le projet compose dérivé ramène le groupe."""
    containers = [
        _FakeContainer(
            "running",
            attrs={"Config": {"Env": []}},
            labels={"hub.slug": "exemple-app-9", "com.docker.compose.project": "exemple_app_9"},
            name="exemple-app-9-api",
        ),
        _app_container("exemple-app-9-postgres", project="exemple_app_9"),
    ]
    _patch_containers(monkeypatch, containers)

    group = find_containers_for_app("exemple-app-9")
    assert [c.name for c in group] == ["exemple-app-9-api", "exemple-app-9-postgres"]


def test_find_containers_deduplicates(monkeypatch):
    """Un conteneur matché ET présent via son projet compose n'apparaît qu'une fois."""
    containers = [
        _app_container("app-api", slug="mon-app", project="mon_app"),
        _app_container("app-front", slug="mon-app", project="mon_app"),
    ]
    _patch_containers(monkeypatch, containers)

    names = [c.name for c in find_containers_for_app("mon-app")]
    assert len(names) == len(set(names)) == 2


def test_find_containers_empty_group(monkeypatch):
    """Aucun match → liste vide (groupe vide)."""
    _patch_containers(monkeypatch, [_app_container("autre-api", slug="autre", project="autre")])
    assert find_containers_for_app("inconnue") == []


def test_find_containers_docker_unavailable(monkeypatch):
    """Docker indisponible → liste vide (pas d'exception)."""
    _patch_containers(monkeypatch, [], is_available=False)
    assert find_containers_for_app("mon-app") == []


# ─── Groupes compose : count_containers_batch ─────────────────────────

def _patch_counting_containers(monkeypatch, containers):
    """Injecte la liste docker-py en comptant les appels (UN par batch)."""
    calls = {"n": 0}

    def _list():
        calls["n"] += 1
        return containers

    monkeypatch.setattr(hc, "_list_all_containers", _list)
    return calls


def test_count_containers_batch_one_docker_list_for_all_slugs(monkeypatch):
    """Le batch compte tous les slugs avec UNE SEULE liste Docker."""
    containers = [
        _app_container("a-api", slug="app-a", project="app_a"),
        _app_container("a-db", status="exited", project="app_a"),
        _app_container("b-api", slug="app-b", project="app_b"),
    ]
    list_calls = _patch_counting_containers(monkeypatch, containers)

    counts = count_containers_batch(["app-a", "app-b", "app-a", "inconnue"])

    assert list_calls["n"] == 1  # un seul list(), pas un par app
    assert counts == {"app-a": 2, "app-b": 1, "inconnue": 0}


def test_count_containers_batch_docker_unavailable(monkeypatch):
    """Docker indisponible → None par slug (incertitude, pas de zéro mensonger)."""
    _patch_containers(monkeypatch, [], is_available=False)
    assert count_containers_batch(["app-a", "app-b"]) == {"app-a": None, "app-b": None}


# ─── get_container_state : group-aware ────────────────────────────────

def test_get_container_state_all_off_returns_first_state(monkeypatch):
    """Tous les conteneurs du groupe off → état du premier (tri par nom)."""
    containers = [
        _app_container("app-api", status="exited", slug="mon-app", project="mon_app"),
        _app_container("app-frontend", status="exited", slug="mon-app", project="mon_app"),
        _app_container("app-postgres", status="exited", project="mon_app"),
    ]
    _patch_containers(monkeypatch, containers)
    assert get_container_state("mon-app") == "exited"


def test_get_container_state_mixed_off_states(monkeypatch):
    """Aucun running mais états mixtes → premier état du groupe trié."""
    containers = [
        _app_container("app-api", status="paused", slug="mon-app", project="mon_app"),
        _app_container("app-frontend", status="exited", slug="mon-app", project="mon_app"),
    ]
    _patch_containers(monkeypatch, containers)
    assert get_container_state("mon-app") == "paused"


def test_get_container_state_any_running_returns_running(monkeypatch):
    """Un seul conteneur running (l'api) suffit → 'running' même si le
    frontend est exited (ne plus reporter 'stopped' à tort)."""
    containers = [
        _app_container("app-api", status="running", slug="mon-app", project="mon_app"),
        _app_container("app-frontend", status="exited", slug="mon-app", project="mon_app"),
        _app_container("app-postgres", status="exited", project="mon_app"),
    ]
    _patch_containers(monkeypatch, containers)
    assert get_container_state("mon-app") == "running"


def test_get_container_state_group_empty_absent(monkeypatch):
    """Docker joignable, groupe vide → 'absent' (contrat inchangé)."""
    _patch_containers(monkeypatch, [_app_container("autre-api", slug="autre", project="autre")])
    assert get_container_state("inconnue") == "absent"


def test_get_container_state_docker_unavailable_none(monkeypatch):
    """Docker indisponible → None (incertitude, contrat inchangé)."""
    _patch_containers(monkeypatch, [], is_available=False)
    assert get_container_state("mon-app") is None


# ─── get_container_states_batch (fix gradient de latence) ─────────────
# Le Docker-first v1 appelait get_container_state (SYNCHRONE, docker-py) au
# début de CHAQUE probe : N appels séquentiels bloquaient l'event loop au
# démarrage des sondes, et les réponses HTTP des premières apps étaient
# traitées en retard -> latences mesurées gonflées en cascade (gradient
# 1277 -> 36 ms dans l'ordre alphabétique). Le batch fait UNE liste Docker
# pour tous les slugs, appelée hors event loop par la route.

def test_container_states_batch_mixed(monkeypatch):
    """Batch : running prend le dessus, tous-off -> état du 1er non-running,
    slug sans conteneur -> 'absent'. Une seule liste Docker."""
    calls = {"n": 0}

    def _fake_list():
        calls["n"] += 1
        return [
            _FakeContainer("running", name="ws-api",
                           attrs={"Config": {"Env": ["APP_SLUG=app-running"]}}),
            _FakeContainer("exited", name="off-api",
                           attrs={"Config": {"Env": ["APP_SLUG=app-off"]}}),
        ]

    monkeypatch.setattr(hc, "_list_all_containers", _fake_list)
    states = hc.get_container_states_batch(["app-running", "app-off", "app-absent"])

    assert states == {
        "app-running": "running",
        "app-off": "exited",
        "app-absent": "absent",
    }
    assert calls["n"] == 1  # UNE liste Docker pour tous les slugs


def test_container_states_batch_docker_unavailable(monkeypatch):
    """Docker indisponible -> None pour tous (incertitude, jamais stopped)."""
    monkeypatch.setattr(hc, "_list_all_containers", lambda: None)
    states = hc.get_container_states_batch(["a", "b"])
    assert states == {"a": None, "b": None}
