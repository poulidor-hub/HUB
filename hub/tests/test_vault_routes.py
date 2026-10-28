"""Tests du router vault (coffre hubctl).

GET /api/admin/vault/status — proxy admin-only vers le service hôte
(`hubctl atelier serve`, même service que les ateliers) : passthrough du JSON
du service enrichi {"service": "ok"}. L'état du service est une information,
pas une erreur : ConnectError/timeout ⇒ 200 {"service": "unreachable",
"hint": …} pour que l'UI affiche l'état au lieu d'une erreur.

POST /api/admin/vault/backup — proxy admin-only (passthrough intégral du code
et du corps : 200 « backup fait », 200 {"status": "locked"}, 409 « backup déjà
en cours », 500 « dépassement hôte »), timeout ⇒ 503 (la sauvegarde continue
côté hôte), ConnectError ⇒ 503, autre HTTPError / JSON invalide ⇒ 502, audit
VAULT_BACKUP. Token partagé X-Atelier-Token lu du fichier monté :ro.
Mocké via respx (httpx intercepté au niveau httpcore).
"""
import httpx
import pytest
import respx
from sqlalchemy import select

from app.config import Settings, get_settings
from app.models import AuditLog
from tests.conftest import get_auth_header


# ── Constantes verbatim (contrat) — toute divergence est une régression ──

ATELIER_HOST = "http://test-atelier:8777"
STATUS_PATH = "/api/admin/vault/status"
BACKUP_PATH = "/api/admin/vault/backup"

SERVICE_DOWN_DETAIL = (
    "Service atelier hôte injoignable — hubctl atelier serve est-il actif ?"
)
TOKEN_MISSING_DETAIL = (
    "Token atelier absent/illisible côté Hub (secrets/atelier_token) — "
    "resynchronisez-le depuis ~/.config/hubctl/atelier_token"
)
BACKUP_TIMEOUT_DETAIL = (
    "Sauvegarde toujours en cours côté hôte — consultez "
    "journalctl -u hubctl-atelier"
)
UNREACHABLE_BODY = {
    "service": "unreachable",
    "hint": "vérifier hubctl-atelier.service (systemctl status hubctl-atelier)",
}


def _use_vault_service(monkeypatch, tmp_path, token="token-secret-123"):
    """Pointe settings vers un service hôte fictif + un fichier token."""
    token_file = tmp_path / "atelier_token"
    token_file.write_text(f"{token}\n")
    monkeypatch.setattr(get_settings(), "atelier_service_url", ATELIER_HOST)
    monkeypatch.setattr(get_settings(), "atelier_service_token_file",
                        str(token_file))


# ── Settings : timeout dédié au backup du coffre ──


def test_settings_timeout_default():
    """Un backup de coffre est borné mais peut dépasser 2 min côté hôte."""
    assert Settings.model_fields["vault_backup_timeout"].default == 120.0


# ── GET /status — sécurité : admin-only (401 sans JWT / 403 non-admin) ──


async def test_status_requires_auth(client, user_token, tmp_path, monkeypatch):
    _use_vault_service(monkeypatch, tmp_path)
    r = await client.get(STATUS_PATH)
    assert r.status_code == 401
    r = await client.get(STATUS_PATH, headers=get_auth_header(user_token))
    assert r.status_code == 403


# ── GET /status — passthrough enrichi {"service": "ok"} ──


async def test_status_passthrough_200(client, admin_token, tmp_path,
                                      monkeypatch):
    """200 du service → 200, clés hubctl préservées + {"service": "ok"}.

    Déverrouillé (clés entries/divergences) comme verrouillé (sans ces clés) :
    le passthrough ne doit rien supposer sur la shape.
    """
    _use_vault_service(monkeypatch, tmp_path)
    unlocked = {
        "status": "ok", "locked": False,
        "last_backup": "2026-10-28T06:00:00+00:00", "vault_size": 1234,
        "entries": {"env": 12, "files": 3, "apps": 2, "db_dump": 1},
        "divergences": [], "pra": {"configured": False, "location": None},
    }
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{ATELIER_HOST}/vault/status").respond(
            200, json=unlocked)
        r = await client.get(STATUS_PATH,
                             headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert route.called
    assert r.json() == {**unlocked, "service": "ok"}
    # Verrouillé : pas de clés entries/divergences — passthrough intact.
    locked = {
        "status": "locked", "locked": True, "last_backup": None,
        "vault_size": 1234, "pra": {"configured": False, "location": None},
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ATELIER_HOST}/vault/status").respond(200, json=locked)
        r = await client.get(STATUS_PATH,
                             headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json() == {**locked, "service": "ok"}
    assert "entries" not in r.json()


async def test_status_unreachable_200_flag(client, admin_token, tmp_path,
                                           monkeypatch):
    """ConnectError → 200 {"service": "unreachable", "hint": …} (information)."""
    _use_vault_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ATELIER_HOST}/vault/status").mock(
            side_effect=httpx.ConnectError("Connection refused"))
        r = await client.get(STATUS_PATH,
                             headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json() == UNREACHABLE_BODY


async def test_status_timeout_200_flag(client, admin_token, tmp_path,
                                       monkeypatch):
    """Timeout → 200 {"service": "unreachable", "hint": …} (même sémantique)."""
    _use_vault_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ATELIER_HOST}/vault/status").mock(
            side_effect=httpx.ReadTimeout("timed out"))
        r = await client.get(STATUS_PATH,
                             headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json() == UNREACHABLE_BODY


# ── POST /backup — proxy avec passthrough intégral ──


async def test_backup_passthrough_200(client, admin_token, tmp_path,
                                      monkeypatch):
    """200 du service → 200 + corps identique + header token transmis."""
    _use_vault_service(monkeypatch, tmp_path)
    payload = {"status": "ok", "env": "coffre.env", "files": 3, "db": 1,
               "pra": False}
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{ATELIER_HOST}/vault/backup").respond(
            200, json=payload)
        r = await client.post(BACKUP_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json() == payload
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["X-Atelier-Token"] == "token-secret-123"


async def test_backup_passthrough_409(client, admin_token, tmp_path,
                                      monkeypatch):
    """409 (backup déjà en cours) → 409 passthrough, shape hubctl intacte."""
    _use_vault_service(monkeypatch, tmp_path)
    payload = {"erreur": "backup déjà en cours"}
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{ATELIER_HOST}/vault/backup").respond(409, json=payload)
        r = await client.post(BACKUP_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 409
    assert r.json() == payload  # passthrough : on ne déduit pas la structure


async def test_backup_timeout_503(client, admin_token, tmp_path,
                                  monkeypatch):
    """Timeout → 503 dédié : côté hôte la sauvegarde continue, on ne ment pas."""
    _use_vault_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{ATELIER_HOST}/vault/backup").mock(
            side_effect=httpx.ReadTimeout("timed out"))
        r = await client.post(BACKUP_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 503
    assert r.json()["detail"] == BACKUP_TIMEOUT_DETAIL


async def test_backup_connect_error_503(client, admin_token, tmp_path,
                                        monkeypatch):
    """ConnectError → 503 avec le message verbalisable verbatim."""
    _use_vault_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{ATELIER_HOST}/vault/backup").mock(
            side_effect=httpx.ConnectError("Connection refused"))
        r = await client.post(BACKUP_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 503
    # NB : le handler global du Hub enrichit le corps avec "request_id" —
    # le contrat verbatim porte sur le message "detail".
    assert r.json()["detail"] == SERVICE_DOWN_DETAIL


async def test_backup_http_error_502(client, admin_token, tmp_path,
                                     monkeypatch):
    """Autre HTTPError (ReadError, ni timeout ni ConnectError) → 502."""
    _use_vault_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{ATELIER_HOST}/vault/backup").mock(
            side_effect=httpx.ReadError("connection reset"))
        r = await client.post(BACKUP_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 502


async def test_backup_audit_logged(client, admin_token, admin_user,
                                   async_session, tmp_path, monkeypatch):
    """L'action sensible est tracée dans l'audit Hub (comme routes admin)."""
    _use_vault_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{ATELIER_HOST}/vault/backup").respond(
            200, json={"status": "ok", "env": "coffre.env", "files": 3,
                       "db": 1, "pra": False})
        r = await client.post(BACKUP_PATH,
                              headers=get_auth_header(admin_token))
    assert r.status_code == 200
    rows = (await async_session.execute(
        select(AuditLog).where(AuditLog.action == "VAULT_BACKUP")
    )).scalars().all()
    assert len(rows) == 1
    entry = rows[0]
    assert entry.user_id == admin_user.id
    assert entry.target == "vault"
    assert entry.response_status == 200


async def test_backup_token_file_missing_503(client, admin_token, tmp_path,
                                             monkeypatch):
    """Fichier token inexistant → 503 dédié (pas un 401 générique)."""
    monkeypatch.setattr(get_settings(), "atelier_service_url", ATELIER_HOST)
    monkeypatch.setattr(get_settings(), "atelier_service_token_file",
                        str(tmp_path / "atelier_token_absent"))
    r = await client.post(BACKUP_PATH, headers=get_auth_header(admin_token))
    assert r.status_code == 503
    assert r.json()["detail"] == TOKEN_MISSING_DETAIL
