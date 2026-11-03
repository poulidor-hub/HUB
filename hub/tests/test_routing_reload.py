"""Chantier B hotfix — retry des reloads quand les conteneurs ne sont pas prêts.

Au boot, hub-api peut finir son lifespan avant que hub-frontend/hub-dns soient
running (409 « is not running » sur exec) : au lieu de poser une erreur dans le
bandeau, on retente. Une commande qui RÉPOND en échec reste fatale (pas de
retry — rollback géré par publish()).
"""

import pytest

import app.routing_service as rs
from app.routing_service import _reload_containers


class FakeContainers:
    def __init__(self, script: dict[str, list]):
        self.script = script  # name → liste de comportements par appel

    def get(self, name):
        seq = self.script.get(name, ["ok"])
        behavior = seq.pop(0) if len(seq) > 1 else seq[0]

        class C:
            def exec_run(self, cmd):
                if behavior == "notrunning":
                    raise RuntimeError("409 Conflict: container is not running")
                if behavior == "absent":
                    raise RuntimeError("no such container")
                if isinstance(behavior, int):
                    return (behavior, b"err")
                return (0, b"ok")

        return C()


class FakeClient:
    def __init__(self, script):
        self.containers = FakeContainers(script)


RELOADS = [("hub-dns", "kill -HUP 1"), ("hub-frontend", "nginx -s reload")]


def test_reload_success_immediate():
    client = FakeClient({"hub-dns": ["ok"], "hub-frontend": ["ok"]})
    _reload_containers(RELOADS, attempts=2, delay=0.0, client=client)


def test_reload_command_failure_is_fatal_no_retry():
    client = FakeClient({"hub-dns": ["ok"], "hub-frontend": [1]})
    with pytest.raises(rs._ReloadCommandError):
        _reload_containers(RELOADS, attempts=3, delay=0.0, client=client)


def test_reload_not_ready_is_retried_then_succeeds():
    script = {"hub-dns": ["ok"],
              "hub-frontend": ["notrunning", "notrunning", "ok"]}
    client = FakeClient(script)
    _reload_containers(RELOADS, attempts=4, delay=0.0, client=client)
    assert script["hub-frontend"][0] == "ok"  # consommé après retry


def test_reload_not_ready_exhausted_raises():
    client = FakeClient({"hub-dns": ["ok"],
                         "hub-frontend": ["notrunning", "notrunning", "notrunning"]})
    with pytest.raises(RuntimeError, match="tentatives"):
        _reload_containers(RELOADS, attempts=3, delay=0.0, client=client)
