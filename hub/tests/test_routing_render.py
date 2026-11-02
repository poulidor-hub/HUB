"""Chantier B Task 2 — routing_service : collecte + rendus purs (zone + vhosts).

Rendus déterministes : le contenu attendu des fichiers est asserté ligne par
ligne (spec §4). collect_entries est testé sur la SQLite de test avec 7 profils
d'apps + 3 services (interne zone, externe, désactivé).
"""

import pytest
from sqlalchemy import select

from app.models import Application, PortAllocation, RoutedService
from app.routing_service import (
    collect_entries,
    render_vhosts,
    render_zone,
    validate_dns_name,
    validate_zone,
)

HUB_HOST = "192.0.2.10"
ZONE = "hub.example.lan"


def _app(slug: str, dns_name: str | None = None, *, active=True, remote=False,
         workshop=False, unmanaged=False, front_url=None) -> Application:
    return Application(
        name=slug, slug=slug, api_url=f"http://{HUB_HOST}:9000",
        frontend_url=front_url or f"http://{HUB_HOST}:4200",
        dns_name=dns_name or slug, is_active=active, is_remote=remote,
        is_workshop=workshop, is_unmanaged=unmanaged,
    )


# ---------------------------------------------------------------- validate


def test_validate_dns_name_accepts_slug():
    assert validate_dns_name("exemple-app") == "exemple-app"


def test_validate_dns_name_rejects_bad_inputs():
    for bad in ("exemple-app", "-bad", "bad-", "hub", "www", "api", "", "a" * 64, "a b"):
        with pytest.raises(ValueError):
            validate_dns_name(bad)


def test_validate_zone():
    assert validate_zone("hub.example.lan") == "hub.example.lan"
    assert validate_zone("") == ""  # vide = routage par nom désactivé (état valide)
    for bad in ("priv", "hub..priv", "-hub.priv", "hub.example.lan/x"):
        with pytest.raises(ValueError):
            validate_zone(bad)


# ---------------------------------------------------------------- collect


@pytest.mark.asyncio
async def test_collect_entries_seven_profiles(async_session):
    apps = [
        _app("exemple-app"),                       # locale standard
        _app("remote-app", remote=True,
             front_url="https://203.0.113.42:9443"),  # distante → host de frontend_url
        _app("exemple-app-ws", workshop=True),  # atelier
        _app("app-off", active=False),        # inactive → conservée
        _app("unmanaged-app", unmanaged=True),  # unmanaged actif → publié
        # locale SANS PortAllocation → port parsé de frontend_url
        _app("no-alloc", front_url=f"http://{HUB_HOST}:4299"),
    ]
    async_session.add_all(apps)
    await async_session.flush()
    async_session.add_all([
        PortAllocation(app_slug="exemple-app", host=HUB_HOST, api_port=9001,
                       frontend_port=4201, frontend_https_port=4202),
        PortAllocation(app_slug="exemple-app-ws", host=HUB_HOST, api_port=9013,
                       frontend_port=5211, frontend_https_port=5212),
        RoutedService(name="PhiliaNG", dns_name="service.example.org",
                      target_url="https://host.docker.internal:4443"),
        RoutedService(name="Compta interne", dns_name=f"compta.{ZONE}",
                      target_url="http://192.0.2.10:8080"),
        RoutedService(name="Vieux flux", dns_name="old.example.org",
                      target_url="http://10.0.0.9:80", is_enabled=False),
    ])
    await async_session.commit()

    entries = await collect_entries(async_session, HUB_HOST, ZONE)
    by_dns = {e["dns_name"]: e for e in entries}

    # locales → ip hub_host + proxy_pass port alloué
    assert by_dns["exemple-app"]["proxy_pass"] == f"http://{HUB_HOST}:4201"
    assert by_dns["exemple-app"]["ip"] == HUB_HOST
    assert by_dns["exemple-app"]["fqdn"] == f"exemple-app.{ZONE}"
    assert by_dns["exemple-app"]["in_zone"] is True
    # distante → ip = host de la cible, proxy_pass = frontend_url telle quelle
    assert by_dns["remote-app"]["ip"] == "203.0.113.42"
    assert by_dns["remote-app"]["proxy_pass"] == "https://203.0.113.42:9443"
    # atelier publié + marqué
    assert by_dns["exemple-app-ws"]["is_workshop"] is True
    assert by_dns["exemple-app-ws"]["proxy_pass"] == f"http://{HUB_HOST}:5211"
    # inactive conservée
    assert by_dns["app-off"]["is_active"] is False
    # unmanaged actif publié
    assert "unmanaged-app" in by_dns
    # locale sans allocation → port parsé de frontend_url
    assert by_dns["no-alloc"]["proxy_pass"] == f"http://{HUB_HOST}:4299"
    # services : interne actif in_zone, externe actif hors zone, désactivé absent
    assert by_dns[f"compta.{ZONE}"]["kind"] == "service"
    assert by_dns[f"compta.{ZONE}"]["in_zone"] is True
    assert by_dns["service.example.org"]["kind"] == "service"
    assert by_dns["service.example.org"]["in_zone"] is False
    assert "old.example.org" not in by_dns


# ---------------------------------------------------------------- render zone


def _entries() -> list[dict]:
    return [
        {"kind": "app", "dns_name": "exemple-app", "fqdn": f"exemple-app.{ZONE}",
         "in_zone": True, "ip": HUB_HOST, "proxy_pass": f"http://{HUB_HOST}:4201",
         "is_workshop": False, "is_active": True, "enabled": True},
        {"kind": "app", "dns_name": "remote-app", "fqdn": f"remote-app.{ZONE}",
         "in_zone": True, "ip": "203.0.113.42",
         "proxy_pass": "https://203.0.113.42:9443", "is_workshop": False,
         "is_active": True, "enabled": True},
        {"kind": "app", "dns_name": "exemple-app-ws", "fqdn": f"exemple-app-ws.{ZONE}",
         "in_zone": True, "ip": HUB_HOST, "proxy_pass": f"http://{HUB_HOST}:5211",
         "is_workshop": True, "is_active": True, "enabled": True},
        {"kind": "service", "dns_name": f"compta.{ZONE}", "fqdn": f"compta.{ZONE}",
         "in_zone": True, "ip": HUB_HOST, "proxy_pass": "http://192.0.2.10:8080",
         "is_workshop": False, "is_active": True, "enabled": True},
    ]


def test_render_zone_exact_content():
    zone_file = render_zone(_entries(), ZONE)
    assert zone_file == (
        "# Généré par hub-api (routing_service) — NE PAS ÉDITER\n"
        f"address=/compta.{ZONE}/{HUB_HOST}\n"
        f"address=/exemple-app.{ZONE}/{HUB_HOST}\n"
        f"address=/remote-app.{ZONE}/203.0.113.42\n"
        f"address=/exemple-app-ws.{ZONE}/{HUB_HOST}\n"
    )


def test_render_zone_excludes_out_of_zone_and_empty_zone():
    entries = _entries() + [
        {"kind": "service", "dns_name": "service.example.org", "fqdn": "service.example.org",
         "in_zone": False, "ip": HUB_HOST, "proxy_pass": "https://h:4443",
         "is_workshop": False, "is_active": True, "enabled": True},
    ]
    zone_file = render_zone(entries, ZONE)
    assert "pr177" not in zone_file
    assert zone_file.count("address=") == 4
    assert render_zone(entries, "") == ""


def test_render_vhosts_exact_content():
    vhosts = render_vhosts(_entries(), ZONE,
                           wildcard_dir="/etc/nginx/ssl/hub-dns-wildcard")
    assert set(vhosts) == {"exemple-app.conf", "remote-app.conf",
                           "exemple-app-ws.conf", "compta.hub.example.lan.conf"}
    vh = vhosts["exemple-app.conf"]
    assert "server_name exemple-app.hub.example.lan;" in vh
    assert "ssl_certificate     /etc/nginx/ssl/hub-dns-wildcard/server.crt;" in vh
    assert f"proxy_pass http://{HUB_HOST}:4201;" in vh
    assert "include /etc/nginx/proxy-common.inc;" in vh
    # distante → proxy_pass = frontend_url telle quelle
    assert "proxy_pass https://203.0.113.42:9443;" in vhosts["remote-app.conf"]


def test_render_vhosts_empty_when_no_zone():
    assert render_vhosts(_entries(), "", "/x") == {}
