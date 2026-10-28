"""Tests des routes admin atelier.

GET /api/admin/atelier — découverte par marqueurs : tout dossier de
apps_host_path contenant un .workshop.env (contrat hubctl v0.6.1, cf.
workshop_service.WS_ENV_KEYS) est un atelier. L'état des conteneurs (projet
compose = ws_slug) est lu via le client Docker socket :ro de docker_scanner —
Docker injoignable ⇒ containers_state="inconnu", la route ne lève jamais 500.

POST /api/admin/atelier/close-all — proxy admin vers le service atelier hôte
(`hubctl atelier serve`, 127.0.0.1:8777) : passthrough strict de la réponse
(200 ou 207 — la shape est decided par hubctl), 503 verbalisable si le service
est injoignable (ConnectError/timeout), token partagé X-Atelier-Token lu du
fichier monté :ro. Mocké via respx (httpx intercepté au niveau httpcore).

Shape de sortie du GET (contrat consommé par l'agent exemple-app et l'UI admin) :
{"ateliers": [{app, ws_slug, api_port, frontend_port, containers_state}]}
"""
import httpx
import pytest
import respx
from sqlalchemy import select

import app.routes.atelier as route_mod
from app.auth import get_password_hash
from app.config import Settings, get_settings
from app.models import AuditLog, User
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


# ── POST /close-all — proxy vers hubctl atelier serve (127.0.0.1:8777) ──

ATELIER_HOST = "http://test-atelier:8777"
CLOSE_ALL_PATH = "/api/admin/atelier/close-all"

# Messages 503 verbatim (contrat) — toute divergence ici est une régression.
SERVICE_DOWN_DETAIL = (
    "Service atelier hôte injoignable — hubctl atelier serve est-il actif ?"
)
TOKEN_MISSING_DETAIL = (
    "Token atelier absent/illisible côté Hub (secrets/atelier_token) — "
    "resynchronisez-le depuis ~/.config/hubctl/atelier_token"
)
CLOSE_TIMEOUT_DETAIL = (
    "Fermeture toujours en cours côté hôte — consultez "
    "journalctl -u hubctl-atelier avant de relancer"
)


def _use_atelier_service(monkeypatch, tmp_path, token="token-secret-123"):
    """Pointe settings vers un service hôte fictif + un fichier token."""
    token_file = tmp_path / "atelier_token"
    token_file.write_text(f"{token}\n")
    monkeypatch.setattr(get_settings(), "atelier_service_url", ATELIER_HOST)
    monkeypatch.setattr(get_settings(), "atelier_service_token_file",
                        str(token_file))


def test_close_timeout_default_is_600s():
    """Un `down -v` peut être long : le timeout proxy doit valoir 600 s."""
    assert Settings.model_fields["atelier_close_timeout"].default == 600.0


async def test_close_all_requires_auth(client, tmp_path, monkeypatch):
    _use_atelier_service(monkeypatch, tmp_path)
    r = await client.post(CLOSE_ALL_PATH)
    assert r.status_code == 401


async def test_close_all_admin_only(client, user_token, tmp_path, monkeypatch):
    _use_atelier_service(monkeypatch, tmp_path)
    r = await client.post(CLOSE_ALL_PATH, headers=get_auth_header(user_token))
    assert r.status_code == 403


async def test_close_all_passthrough_200(client, admin_token, tmp_path,
                                         monkeypatch):
    """200 du service → 200 + corps identique + header token transmis."""
    _use_atelier_service(monkeypatch, tmp_path)
    payload = {"closed": [{"slug": "a-ws"}, {"slug": "b-ws"}], "failed": []}
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{ATELIER_HOST}/atelier/close-all").respond(
            200, json=payload)
        r = await client.post(CLOSE_ALL_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json() == payload
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["X-Atelier-Token"] == "token-secret-123"


async def test_close_all_passthrough_207(client, admin_token, tmp_path,
                                         monkeypatch):
    """207 (échec partiel) → 207 passthrough, shape hubctl intacte."""
    _use_atelier_service(monkeypatch, tmp_path)
    payload = {"closed": [{"slug": "a-ws"}],
               "failed": [{"slug": "b-ws", "rc": 1, "output": "boom"}]}
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{ATELIER_HOST}/atelier/close-all").respond(
            207, json=payload)
        r = await client.post(CLOSE_ALL_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 207
    assert r.json() == payload  # passthrough : on ne déduit pas la structure
    assert route.called


async def test_close_all_token_missing_503(client, admin_token, tmp_path,
                                           monkeypatch):
    """Fichier token inexistant → 503 dédié (pas un 401 générique)."""
    monkeypatch.setattr(get_settings(), "atelier_service_url", ATELIER_HOST)
    monkeypatch.setattr(get_settings(), "atelier_service_token_file",
                        str(tmp_path / "atelier_token_absent"))
    r = await client.post(CLOSE_ALL_PATH,
                          headers=get_auth_header(admin_token))
    assert r.status_code == 503
    assert r.json()["detail"] == TOKEN_MISSING_DETAIL


async def test_close_all_service_down_503(client, admin_token, tmp_path,
                                          monkeypatch):
    """ConnectError → 503 avec le message verbalisable verbatim."""
    _use_atelier_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{ATELIER_HOST}/atelier/close-all").mock(
            side_effect=httpx.ConnectError("Connection refused"))
        r = await client.post(CLOSE_ALL_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 503
    # NB : le handler global du Hub enrichit le corps avec "request_id" —
    # le contrat verbatim porte sur le message "detail".
    assert r.json()["detail"] == SERVICE_DOWN_DETAIL


async def test_close_all_timeout_503(client, admin_token, tmp_path,
                                     monkeypatch):
    """Timeout (un down -v trop long) → 503 dédié, distinct du ConnectError."""
    _use_atelier_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{ATELIER_HOST}/atelier/close-all").mock(
            side_effect=httpx.ReadTimeout("timed out"))
        r = await client.post(CLOSE_ALL_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 503
    assert r.json()["detail"] == CLOSE_TIMEOUT_DETAIL


async def test_close_all_other_service_error_passthrough(
        client, admin_token, tmp_path, monkeypatch):
    """Autre code service (ex. 401 token rejeté) → passthrough du code+corps."""
    _use_atelier_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{ATELIER_HOST}/atelier/close-all").respond(
            401, json={"detail": "unauthorized"})
        r = await client.post(CLOSE_ALL_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthorized"}


async def test_close_all_audit_emitted(client, admin_token, admin_user,
                                       async_session, tmp_path, monkeypatch):
    """L'action destructive est tracée dans l'audit Hub (comme routes admin)."""
    _use_atelier_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{ATELIER_HOST}/atelier/close-all").respond(
            200, json={"closed": [{"slug": "a-ws"}], "failed": []})
        r = await client.post(CLOSE_ALL_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 200
    rows = (await async_session.execute(
        select(AuditLog).where(AuditLog.action == "ATELIER_CLOSE_ALL")
    )).scalars().all()
    assert len(rows) == 1
    entry = rows[0]
    assert entry.user_id == admin_user.id
    assert entry.target == "atelier"
    assert entry.response_status == 200
