"""Tests de la route native GET /api/admin/atelier (liste des ateliers).

Découverte par marqueurs : tout dossier de apps_host_path contenant un
.workshop.env (contrat hubctl v0.6.1, cf. workshop_service.WS_ENV_KEYS) est un
atelier. L'état des conteneurs (projet compose = ws_slug) est lu via le client
Docker socket :ro de docker_scanner — Docker injoignable ⇒
containers_state="inconnu", la route ne lève jamais 500.

Shape de sortie (contrat consommé par l'agent exemple-app et l'UI admin) :
{"ateliers": [{app, ws_slug, api_port, frontend_port, containers_state}]}
"""
import pytest

import app.routes.atelier as route_mod
from app.auth import get_password_hash
from app.config import get_settings
from app.models import User
from tests.conftest import get_auth_header


def _write_ws_marker(app_dir, ws_slug="monapp-ws", api="10000", fe="5200"):
    """Écrit un .workshop.env fidèle au contrat 7 clés (workshop_service)."""
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / ".workshop.env").write_text(
        f"APP_SLUG={ws_slug}\n"
        f"API_PORT={api}\n"
        f"FRONTEND_PORT={fe}\n"
        "FRONTEND_HTTPS_PORT=5201\n"
        "WS_REF=main\n"
        "WS_COMMIT=abc1234\n"
        "APP_SUBNET=10.210.0.0/24\n"
    )


def _use_apps_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "apps_host_path", str(tmp_path))


def _docker_unavailable(monkeypatch):
    class _Unavailable:
        is_available = False
        client = None
    monkeypatch.setattr(route_mod, "docker_scanner", _Unavailable())


class _FakeContainer:
    def __init__(self, labels, status):
        self.labels = labels
        self.status = status


def _fake_docker(monkeypatch, containers=None, raise_error=False):
    class _Containers:
        def list(self, all=False):
            if raise_error:
                raise RuntimeError("socket down")
            return containers or []

    class _Scanner:
        is_available = True
        client = type("C", (), {"containers": _Containers()})()

    monkeypatch.setattr(route_mod, "docker_scanner", _Scanner())


# ── Sécurité : admin-only (401 sans JWT / 403 non-admin) ──


async def test_atelier_list_requires_auth(client):
    r = await client.get("/api/admin/atelier")
    assert r.status_code == 401


async def test_atelier_list_admin_only(client, user_token):
    r = await client.get("/api/admin/atelier",
                         headers=get_auth_header(user_token))
    assert r.status_code == 403


# ── Liste vide (tmp_path sans marker) ──


async def test_atelier_list_empty(client, admin_token, tmp_path, monkeypatch):
    _use_apps_dir(monkeypatch, tmp_path)
    _docker_unavailable(monkeypatch)
    r = await client.get("/api/admin/atelier",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json() == {"ateliers": []}


# ── Un marker → 1 entrée, champs exacts ──


async def test_atelier_list_with_marker(client, admin_token, tmp_path,
                                        monkeypatch):
    _use_apps_dir(monkeypatch, tmp_path)
    _docker_unavailable(monkeypatch)
    _write_ws_marker(tmp_path / "monapp")
    (tmp_path / "autre-app").mkdir()  # dossier sans marker → ignoré
    hidden = tmp_path / ".cache"; hidden.mkdir()
    _write_ws_marker(hidden)          # dossier caché → ignoré
    r = await client.get("/api/admin/atelier",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert list(data.keys()) == ["ateliers"]
    assert data["ateliers"] == [{
        "app": "monapp",
        "ws_slug": "monapp-ws",
        "api_port": 10000,
        "frontend_port": 5200,
        "containers_state": "inconnu",  # Docker neutralisé dans le test
    }]


# ── État conteneurs via le client Docker (socket :ro) ──


async def test_containers_state_running(client, admin_token, tmp_path,
                                        monkeypatch):
    _use_apps_dir(monkeypatch, tmp_path)
    _fake_docker(monkeypatch, [
        _FakeContainer({"com.docker.compose.project": "monapp-ws"}, "running"),
        _FakeContainer({"com.docker.compose.project": "monapp-ws"}, "running"),
        _FakeContainer({"com.docker.compose.project": "autre-ws"}, "running"),
    ])
    _write_ws_marker(tmp_path / "monapp")
    r = await client.get("/api/admin/atelier",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json()["ateliers"][0]["containers_state"] == "running"


async def test_containers_state_stopped(client, admin_token, tmp_path,
                                        monkeypatch):
    _use_apps_dir(monkeypatch, tmp_path)
    _fake_docker(monkeypatch, [
        _FakeContainer({"com.docker.compose.project": "monapp-ws"}, "exited"),
    ])
    _write_ws_marker(tmp_path / "monapp")
    r = await client.get("/api/admin/atelier",
                         headers=get_auth_header(admin_token))
    assert r.json()["ateliers"][0]["containers_state"] == "stopped"


async def test_containers_state_partiel(client, admin_token, tmp_path,
                                        monkeypatch):
    _use_apps_dir(monkeypatch, tmp_path)
    _fake_docker(monkeypatch, [
        _FakeContainer({"com.docker.compose.project": "monapp-ws"}, "running"),
        _FakeContainer({"com.docker.compose.project": "monapp-ws"}, "exited"),
    ])
    _write_ws_marker(tmp_path / "monapp")
    r = await client.get("/api/admin/atelier",
                         headers=get_auth_header(admin_token))
    assert r.json()["ateliers"][0]["containers_state"] == "partiel"


async def test_containers_state_absent(client, admin_token, tmp_path,
                                       monkeypatch):
    _use_apps_dir(monkeypatch, tmp_path)
    _fake_docker(monkeypatch, [])  # aucun conteneur pour ce projet compose
    _write_ws_marker(tmp_path / "monapp")
    r = await client.get("/api/admin/atelier",
                         headers=get_auth_header(admin_token))
    assert r.json()["ateliers"][0]["containers_state"] == "absent"


async def test_docker_error_never_500(client, admin_token, tmp_path,
                                      monkeypatch):
    """Docker injoignable en cours de scan ⇒ "inconnu", JAMAIS 500 (P0)."""
    _use_apps_dir(monkeypatch, tmp_path)
    _fake_docker(monkeypatch, raise_error=True)
    _write_ws_marker(tmp_path / "monapp")
    r = await client.get("/api/admin/atelier",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json()["ateliers"][0]["containers_state"] == "inconnu"


# ── Helper scan_workshops (unitaire) ──


def test_scan_workshops_sorted_by_app(tmp_path):
    _write_ws_marker(tmp_path / "b-app")
    _write_ws_marker(tmp_path / "a-app")
    result = route_mod.scan_workshops(tmp_path, None)
    assert [a["app"] for a in result] == ["a-app", "b-app"]


def test_scan_workshops_missing_slug_and_bad_ports(tmp_path):
    """APP_SLUG absent → repli <app>-ws ; ports non numériques → None."""
    _write_ws_marker(tmp_path / "sansslug", ws_slug="", api="abc", fe="")
    result = route_mod.scan_workshops(tmp_path, None)
    assert result == [{
        "app": "sansslug",
        "ws_slug": "sansslug-ws",
        "api_port": None,
        "frontend_port": None,
        "containers_state": "inconnu",
    }]
