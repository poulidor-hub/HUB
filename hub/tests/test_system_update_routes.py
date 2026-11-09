# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests des routes proxy admin /api/admin/system/* (mises à jour du parc).

Le service hôte `hubctl update --serve` (settings.update_service_url,
host.docker.internal:8778) expose le check du parc (GET /update/status),
le déclenchement (POST /update/run) et l'état du job (GET /update/job).
Le Hub les proxifie pour les admins :

- GET  /api/admin/system/update-status — TOLÉRANT à la panne : service
  injoignable ⇒ 200 {"service": "unreachable", "hint": …} (recette
  vault /status) pour que le badge navbar dégrade proprement ;
- POST /api/admin/system/update — passthrough STRICT (202/409/…, la
  shape est décidée par le service hôte, jamais de 500 masqué) + audit
  SYSTEM_UPDATE_TRIGGERED à chaque déclenchement ;
- GET  /api/admin/system/update-job — tolérant (même recette).

Token partagé X-Atelier-Token réutilisé du fichier atelier (monté :ro) :
fichier absent/illisible ⇒ 503 sur les 3 routes (hint journalctl -u
hubctl-update — à resynchroniser, pas un 401 du service). Mocké via
respx (httpx intercepté au niveau httpcore).
"""
import httpx
import respx
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog
from tests.conftest import get_auth_header

UPDATE_HOST = "http://test-update:8778"
STATUS_PATH = "/api/admin/system/update-status"
RUN_PATH = "/api/admin/system/update"
JOB_PATH = "/api/admin/system/update-job"

# Hint verbatim des 503 (contrat, cf. spec §8) — toute divergence ici est
# une régression.
UPDATE_HINT = "journalctl -u hubctl-update"


def _use_update_service(monkeypatch, tmp_path, token="token-secret-123"):
    """Pointe settings vers un service hôte fictif + un fichier token."""
    token_file = tmp_path / "atelier_token"
    token_file.write_text(f"{token}\n")
    monkeypatch.setattr(get_settings(), "update_service_url", UPDATE_HOST)
    monkeypatch.setattr(get_settings(), "atelier_service_token_file",
                        str(token_file))


def _token_file_absent(monkeypatch, tmp_path):
    """Pointe le token partagé vers un fichier inexistant."""
    monkeypatch.setattr(get_settings(), "update_service_url", UPDATE_HOST)
    monkeypatch.setattr(get_settings(), "atelier_service_token_file",
                        str(tmp_path / "inexistant_token"))


async def test_update_status_passthrough_200(client, admin_token, tmp_path,
                                             monkeypatch):
    """200 du service → payload tel quel + header token transmis."""
    _use_update_service(monkeypatch, tmp_path)
    payload = {"service": "ok", "checked_at": "2026-11-09T10:00:00",
               "meta": {"version": "3.84.0", "dirty": False},
               "components": {"HUB_base": {"update_available": False}}}
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{UPDATE_HOST}/update/status").respond(
            200, json=payload)
        r = await client.get(STATUS_PATH,
                             headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json() == payload
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["X-Atelier-Token"] == "token-secret-123"


async def test_update_status_forwards_refresh_query(client, admin_token,
                                                    tmp_path, monkeypatch):
    """?refresh=1 du front est forwardé au service hôte (review finale).

    Le front ajoute ?refresh=1 quand l'onglet s'active et sur « Vérifier
    maintenant » pour forcer le re-check git côté hôte (cache TTL 60 s) :
    le proxy doit relayer ce query — et n'en ajouter AUCUN quand le front
    n'en envoie pas (le badge navbar initial doit rester servi du cache).
    """
    _use_update_service(monkeypatch, tmp_path)
    headers = get_auth_header(admin_token)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{UPDATE_HOST}/update/status").respond(
            200, json={"service": "ok"})
        r_plain = await client.get(STATUS_PATH, headers=headers)
        r_refresh = await client.get(STATUS_PATH + "?refresh=1",
                                     headers=headers)
    assert r_plain.status_code == 200
    assert r_refresh.status_code == 200
    assert route.call_count == 2
    # Sans ?refresh côté Hub : aucune query transmise au service hôte.
    assert route.calls[0].request.url.query == b""
    # Avec ?refresh=1 : exactement ce param, rien d'autre.
    assert dict(route.calls[1].request.url.params) == {"refresh": "1"}


async def test_update_status_unreachable_returns_200(client, admin_token,
                                                     tmp_path, monkeypatch):
    """Service injoignable → 200 {"service": "unreachable", "hint": …}.

    L'état du service est une information, pas une erreur : le badge
    navbar interroge cette route à l'initialisation et doit dégrader
    proprement (recette vault /status).
    """
    _use_update_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{UPDATE_HOST}/update/status").mock(
            side_effect=httpx.ConnectError("Connection refused"))
        r = await client.get(STATUS_PATH,
                             headers=get_auth_header(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "unreachable"
    assert body["hint"]


async def test_proxy_timeout_follows_setting(client, admin_token, tmp_path,
                                             monkeypatch):
    """Timeout des proxys branché sur settings.update_timeout (régression).

    Les 2 GETs tolérants utilisaient un 10.0 s codé en dur : à froid
    (cache TTL 60 s expiré) la collecte de statut côté hôte (git fetch
    des dépôts) prend ~10.2 s sur le parc réel — le premier appel
    répondait « unreachable » (timeout proxy) puis le suivant « ok » :
    badge instable à chaque cycle TTL. Sentinelle 12.34 passée à la
    setting + capture du kwarg timeout du httpx.AsyncClient (respx ne
    simule pas les timeouts sur transport mocké) : prouve le
    branchement à la setting, pas seulement la valeur par défaut.
    """
    captured = []
    real_client = httpx.AsyncClient

    class SpyAsyncClient(real_client):
        def __init__(self, *args, **kwargs):
            captured.append(kwargs.get("timeout"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", SpyAsyncClient)
    monkeypatch.setattr(get_settings(), "update_timeout", 12.34)
    _use_update_service(monkeypatch, tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        status_route = mock.get(f"{UPDATE_HOST}/update/status").respond(
            200, json={"service": "ok"})
        job_route = mock.get(f"{UPDATE_HOST}/update/job").respond(
            200, json={"state": "idle"})
        run_route = mock.post(f"{UPDATE_HOST}/update/run").respond(
            202, json={"job": {"state": "running"}})
        r1 = await client.get(STATUS_PATH,
                              headers=get_auth_header(admin_token))
        r2 = await client.get(JOB_PATH, headers=get_auth_header(admin_token))
        r3 = await client.post(RUN_PATH, headers=get_auth_header(admin_token))
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 202
    assert status_route.called and job_route.called and run_route.called
    assert captured, "aucun httpx.AsyncClient construit par les proxys"
    assert all(t == 12.34 for t in captured), captured


async def test_update_token_missing_returns_503(client, admin_token,
                                                tmp_path, monkeypatch):
    """Token partagé absent/illisible → 503 avec hint (les 3 routes)."""
    _token_file_absent(monkeypatch, tmp_path)
    admin = get_auth_header(admin_token)
    for method, path in ((client.get, STATUS_PATH), (client.get, JOB_PATH),
                         (client.post, RUN_PATH)):
        r = await method(path, headers=admin)
        assert r.status_code == 503, path
        assert UPDATE_HINT in r.json()["detail"], path


async def test_run_passthrough_202_and_409(client, admin_token, admin_user,
                                           async_session, tmp_path,
                                           monkeypatch):
    """202 (job soumis) puis 409 (déjà en cours) → passthrough strict.

    Chaque déclenchement est audité (SYSTEM_UPDATE_TRIGGERED), y compris
    le 409 — l'audit porte le résultat du POST (spec §7).
    """
    _use_update_service(monkeypatch, tmp_path)
    submitted = {"job": {"state": "running",
                         "started_at": "2026-11-09T10:00:00"}}
    # Payload verbatim du service hôte (update_serve.py, hubctl) : SANS
    # accent — le front matche ce detail avec une regex insensible aux
    # accents pour rattacher l'onglet au job en cours.
    conflict = {"detail": "update deja en cours",
                "job": {"state": "running"}}
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{UPDATE_HOST}/update/run").mock(side_effect=[
            httpx.Response(202, json=submitted),
            httpx.Response(409, json=conflict),
        ])
        r1 = await client.post(RUN_PATH,
                               headers=get_auth_header(admin_token))
        r2 = await client.post(RUN_PATH,
                               headers=get_auth_header(admin_token))
    assert r1.status_code == 202
    assert r1.json() == submitted
    assert r2.status_code == 409
    assert r2.json() == conflict
    assert route.call_count == 2
    sent = route.calls.last.request
    assert sent.headers["X-Atelier-Token"] == "token-secret-123"

    rows = (await async_session.execute(
        select(AuditLog).where(AuditLog.action == "SYSTEM_UPDATE_TRIGGERED")
    )).scalars().all()
    assert len(rows) == 2
    assert sorted(row.response_status for row in rows) == [202, 409]
    assert all(row.user_id == admin_user.id for row in rows)


async def test_update_job_passthrough_200(client, admin_token, tmp_path,
                                          monkeypatch):
    """GET /update/job → passthrough strict du payload du service."""
    _use_update_service(monkeypatch, tmp_path)
    payload = {"state": "idle"}
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{UPDATE_HOST}/update/job").respond(
            200, json=payload)
        r = await client.get(JOB_PATH, headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json() == payload
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["X-Atelier-Token"] == "token-secret-123"


async def test_update_routes_require_auth(client, tmp_path, monkeypatch):
    """Sans token Hub → 401 sur les 3 routes."""
    _use_update_service(monkeypatch, tmp_path)
    assert (await client.get(STATUS_PATH)).status_code == 401
    assert (await client.get(JOB_PATH)).status_code == 401
    assert (await client.post(RUN_PATH)).status_code == 401


async def test_update_routes_admin_only(client, user_token, tmp_path,
                                        monkeypatch):
    """User non-admin → 403 sur les 3 routes."""
    _use_update_service(monkeypatch, tmp_path)
    headers = get_auth_header(user_token)
    assert (await client.get(STATUS_PATH,
                             headers=headers)).status_code == 403
    assert (await client.get(JOB_PATH, headers=headers)).status_code == 403
    assert (await client.post(RUN_PATH, headers=headers)).status_code == 403
