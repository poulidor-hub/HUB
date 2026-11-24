# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests de la configuration réseau du Hub (changement d'IP + persistance).

Couvre :
- GET  /api/config/network        : état complet (source db/env/auto, ports, instance)
- PUT  /api/config/network/hub-host : changement d'IP (mutation à chaud + persistance DB)
- DELETE /api/config/network/hub-host : retour à l'env / auto-détection
- apply_persisted_hub_host()      : override au boot depuis app_settings
"""
import pytest
from sqlalchemy import select

from app.models import AppSetting
from tests.conftest import get_auth_header


HUB_HOST_KEY = "hub_host"


@pytest.fixture(autouse=True)
def _restore_hub_host_singleton():
    """Les tests mutent le singleton get_settings() — restauration systématique.

    (La neutralisation de la localité — _is_local_ip=True — est globale au
    conftest : ce fichier teste la mécanique env/DB/mutation, pas la localité.)
    """
    from app.config import get_settings
    settings = get_settings()
    saved = settings.hub_host
    yield
    settings.hub_host = saved


async def _db_has_hub_host(async_session, value: str | None):
    result = await async_session.execute(
        select(AppSetting).where(AppSetting.key == HUB_HOST_KEY)
    )
    setting = result.scalar_one_or_none()
    if value is None:
        return setting is None
    return setting is not None and setting.value == value


# --- Auth ---

async def test_network_state_requires_admin(client):
    r = await client.get("/api/config/network")
    assert r.status_code == 401


async def test_put_hub_host_requires_admin(client):
    r = await client.put("/api/config/network/hub-host", json={"hub_host": "192.0.2.50"})
    assert r.status_code == 401


async def test_delete_hub_host_requires_admin(client):
    r = await client.delete("/api/config/network/hub-host")
    assert r.status_code == 401


# --- GET état ---

async def test_network_state_shape(client, admin_token, monkeypatch):
    monkeypatch.delenv("HUB_HOST", raising=False)
    r = await client.get("/api/config/network",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {
        "hub_host", "source", "db_value", "env_value",
        "detected_ip", "api_port", "frontend_port", "instance_id",
    }
    assert data["source"] == "auto"          # pas d'env, pas de DB
    assert data["env_value"] is None
    assert data["db_value"] is None
    assert data["hub_host"]                  # auto-détecté, non vide
    assert isinstance(data["api_port"], int)
    assert isinstance(data["frontend_port"], int)
    assert data["instance_id"]


async def test_network_state_source_env(client, admin_token, monkeypatch):
    monkeypatch.setenv("HUB_HOST", "203.0.113.9")
    from app.config import get_settings
    get_settings().hub_host = "203.0.113.9"
    r = await client.get("/api/config/network",
                         headers=get_auth_header(admin_token))
    data = r.json()
    assert data["source"] == "env"
    assert data["hub_host"] == "203.0.113.9"
    assert data["env_value"] == "203.0.113.9"


# --- PUT : changement d'IP (mutation à chaud + persistance) ---

async def test_put_hub_host_muts_settings_and_persists(
        client, admin_token, async_session, monkeypatch):
    monkeypatch.delenv("HUB_HOST", raising=False)
    r = await client.put("/api/config/network/hub-host",
                         json={"hub_host": "192.0.2.50"},
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200

    # État : source db, valeur effective mise à jour
    data = r.json()
    assert data["source"] == "db"
    assert data["hub_host"] == "192.0.2.50"
    assert data["db_value"] == "192.0.2.50"

    # Mutation à chaud : l'endpoint public hub-host reflète la nouvelle IP
    r2 = await client.get("/api/config/hub-host")
    assert r2.json()["host"] == "192.0.2.50"
    assert r2.json()["configured"] is True

    # Persistance en DB
    assert await _db_has_hub_host(async_session, "192.0.2.50")


async def test_put_hub_host_accepts_hostname(client, admin_token):
    r = await client.put("/api/config/network/hub-host",
                         json={"hub_host": "hub.exemple.lan"},
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json()["hub_host"] == "hub.exemple.lan"


async def test_put_hub_host_invalid_rejected(client, admin_token):
    for bad in ["", "   ", "bad host!", "a" * 300, "-lead-dash.lan"]:
        r = await client.put("/api/config/network/hub-host",
                             json={"hub_host": bad},
                             headers=get_auth_header(admin_token))
        assert r.status_code == 400, f"'{bad}' devrait être rejeté"


async def test_put_hub_host_strips_whitespace(client, admin_token, monkeypatch):
    monkeypatch.delenv("HUB_HOST", raising=False)
    r = await client.put("/api/config/network/hub-host",
                         json={"hub_host": "  192.0.2.51  \n"},
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json()["hub_host"] == "192.0.2.51"


async def test_put_hub_host_keeps_400_contract(client, admin_token, no_publish):
    """Le contrat de l'endpoint est inchangé : champ absent ou non textuel →
    400 « requis », valeur refusée → 400 « invalide » (jamais un 500)."""
    headers = get_auth_header(admin_token)
    for payload in ({}, {"hub_host": None}, {"hub_host": 123}, {"hub_host": "x\n;y"}):
        r = await client.put("/api/config/network/hub-host", json=payload,
                             headers=headers)
        assert r.status_code == 400, f"{payload!r}: {r.status_code}"
        assert isinstance(r.json()["detail"], str)


async def test_put_hub_host_follows_unique_validator_for_ipv6(
        client, admin_token, no_publish):
    """hub_host suit le validateur unique. L'IPv6 n'est pas géré de bout en bout
    pour le hôte du Hub (fichier de zone) : les deux formes sont refusées —
    l'IPv6 nu, qui produisait des URL `http://::1:4200` invalides, et la forme
    entre crochets, refusée avant la délégation et qui casserait le fichier de
    zone."""
    headers = get_auth_header(admin_token)
    for value in ("::1", "[::1]", "2001:db8::1", "[2001:db8::1]"):
        r = await client.put("/api/config/network/hub-host",
                             json={"hub_host": value}, headers=headers)
        assert r.status_code == 400, f"{value!r}: {r.status_code}"
        assert r.json()["detail"] == "IP ou hostname invalide"


# --- DELETE : retour env / auto-détection ---

async def test_delete_hub_host_restores_env(client, admin_token, async_session,
                                            monkeypatch):
    monkeypatch.setenv("HUB_HOST", "203.0.113.9")
    # état initial : on PUT une IP admin
    r = await client.put("/api/config/network/hub-host",
                         json={"hub_host": "192.0.2.50"},
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200

    # DELETE : retour à la valeur env (pas à vide)
    r = await client.delete("/api/config/network/hub-host",
                            headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "env"
    assert data["hub_host"] == "203.0.113.9"
    assert data["db_value"] is None
    assert await _db_has_hub_host(async_session, None)


async def test_delete_hub_host_without_db_key_is_noop(client, admin_token,
                                                      async_session):
    r = await client.delete("/api/config/network/hub-host",
                            headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert await _db_has_hub_host(async_session, None)


# --- Override au boot ---

async def test_apply_persisted_hub_host_applies_db_value(async_session):
    from app.config import get_settings
    from app.routes.network import apply_persisted_hub_host

    async_session.add(AppSetting(key=HUB_HOST_KEY, value="198.51.100.7"))
    await async_session.commit()
    try:
        applied = await apply_persisted_hub_host(async_session)
        assert applied is True
        assert get_settings().hub_host == "198.51.100.7"
    finally:
        result = await async_session.execute(
            select(AppSetting).where(AppSetting.key == HUB_HOST_KEY)
        )
        for row in result.scalars():
            await async_session.delete(row)
        await async_session.commit()


async def test_apply_persisted_hub_host_noop_without_key(async_session):
    from app.config import get_settings
    from app.routes.network import apply_persisted_hub_host

    saved = get_settings().hub_host
    applied = await apply_persisted_hub_host(async_session)
    assert applied is False
    assert get_settings().hub_host == saved


# --- Détection de l'IP LAN du HOST (via docker run --network host) ---

async def test_detect_host_ip_requires_admin(client):
    assert (await client.get(
        "/api/config/network/detect-host-ip")).status_code == 401


async def test_detect_host_ip_endpoint(client, admin_token, monkeypatch):
    from app.routes import network

    async def _fake_detect():
        return "192.0.2.10"

    monkeypatch.setattr(network, "_detect_host_ip_via_docker", _fake_detect)
    r = await client.get("/api/config/network/detect-host-ip",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json() == {"ip": "192.0.2.10"}


async def test_detect_host_ip_failure(client, admin_token, monkeypatch):
    from app.routes import network

    async def _fake_detect():
        raise RuntimeError("docker: impossible de joindre le daemon")

    monkeypatch.setattr(network, "_detect_host_ip_via_docker", _fake_detect)
    r = await client.get("/api/config/network/detect-host-ip",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 502
    assert "host" in r.json()["detail"].lower()


def test_detect_host_ip_parses_src_route(monkeypatch):
    """La commande docker run --network host renvoie la route LAN du host
    (src) — le helper extrait et valide l'IP."""
    from app.routes import network
    import asyncio

    captured = {}

    async def _fake_run_cmd(cmd, cwd, timeout, env_extra=None):
        captured["cmd"] = cmd
        return "8.8.8.8 via 192.168.1.1 dev eth0 src 192.0.2.10 \n"

    monkeypatch.setattr(network, "_run_cmd", _fake_run_cmd)
    ip = asyncio.run(network._detect_host_ip_via_docker())
    assert ip == "192.0.2.10"
    cmd = captured["cmd"]
    assert cmd[:2] == ["docker", "run"]
    assert "--network" in cmd and "host" in cmd
    assert "route get" in " ".join(cmd)


def test_detect_host_ip_no_ip_in_output(monkeypatch):
    from app.routes import network
    import asyncio

    async def _fake_run_cmd(cmd, cwd, timeout, env_extra=None):
        return "aucune route\n"

    monkeypatch.setattr(network, "_run_cmd", _fake_run_cmd)
    with pytest.raises(RuntimeError, match="non trouv"):
        asyncio.run(network._detect_host_ip_via_docker())
