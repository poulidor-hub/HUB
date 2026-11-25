# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Chantier B Task 2 — routing_service : collecte + rendus purs (zone + vhosts).

Rendus déterministes : le contenu attendu des fichiers est asserté ligne par
ligne (spec §4). collect_entries est testé sur la SQLite de test avec 7 profils
d'apps + 3 services (interne zone, externe, désactivé).
"""

import logging

import pytest
from sqlalchemy import select, update

from app.models import Application, ApprovalStatus, PortAllocation, RoutedService
from app.routing_service import (
    collect_entries,
    render_vhosts,
    render_zone,
    safe_proxy_pass,
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
    for bad in ("lan", "hub..lan", "-hub.lan", "hub.example.lan/x"):
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
        _app("hub", unmanaged=False),          # app système → JAMAIS routée
        # locale SANS PortAllocation → port parsé de frontend_url
        _app("no-alloc", front_url=f"http://{HUB_HOST}:4299"),
    ]
    apps[5].is_system_app = True
    async_session.add_all(apps)
    await async_session.flush()
    async_session.add_all([
        PortAllocation(app_slug="exemple-app", host=HUB_HOST, api_port=9001,
                       frontend_port=4201, frontend_https_port=4202),
        PortAllocation(app_slug="exemple-app-ws", host=HUB_HOST, api_port=9013,
                       frontend_port=5211, frontend_https_port=5212),
        RoutedService(name="PhiliaNG", dns_name="proxy.example.lan",
                      target_url="https://host.docker.internal:4443"),
        RoutedService(name="Compta interne", dns_name=f"compta.{ZONE}",
                      target_url="http://192.0.2.10:8080"),
        RoutedService(name="Vieux flux", dns_name="old.example.org",
                      target_url="http://10.0.0.9:80", is_enabled=False),
    ])
    await async_session.commit()

    entries = await collect_entries(async_session, HUB_HOST, ZONE)
    by_dns = {e["dns_name"]: e for e in entries}

    # locales → proxy_pass = frontend_url du registre (scheme conservé)
    assert by_dns["exemple-app"]["proxy_pass"] == "http://192.0.2.10:4200"
    assert by_dns["exemple-app"]["ip"] == HUB_HOST
    assert by_dns["exemple-app"]["fqdn"] == f"exemple-app.{ZONE}"
    assert by_dns["exemple-app"]["in_zone"] is True
    # distante → ip = host de la cible, proxy_pass = frontend_url telle quelle
    assert by_dns["remote-app"]["ip"] == "203.0.113.42"
    assert by_dns["remote-app"]["proxy_pass"] == "https://203.0.113.42:9443"
    # atelier publié + marqué
    assert by_dns["exemple-app-ws"]["is_workshop"] is True
    assert by_dns["exemple-app-ws"]["proxy_pass"] == "http://192.0.2.10:4200"
    # inactive conservée
    assert by_dns["app-off"]["is_active"] is False
    # unmanaged actif publié ; app système « hub » jamais routée
    assert "unmanaged-app" in by_dns
    assert "hub" not in by_dns
    # locale sans allocation → port parsé de frontend_url
    assert by_dns["no-alloc"]["proxy_pass"] == f"http://{HUB_HOST}:4299"
    # services : interne actif in_zone, externe actif hors zone, désactivé absent
    assert by_dns[f"compta.{ZONE}"]["kind"] == "service"
    assert by_dns[f"compta.{ZONE}"]["in_zone"] is True
    assert by_dns["proxy.example.lan"]["kind"] == "service"
    assert by_dns["proxy.example.lan"]["in_zone"] is False
    assert "old.example.org" not in by_dns


async def test_collect_entries_excludes_pending_but_treats_absent_status_as_approved(async_session):
    pending = _app("app-pending")
    pending.approval_status = ApprovalStatus.pending
    absent = _app("app-legacy")
    async_session.add_all([pending, absent])
    await async_session.commit()
    # Ligne antérieure à l'approbation : NULL en base. Passe par un UPDATE brut :
    # laissée à None à l'INSERT, la colonne reçoit son défaut Python (approved).
    await async_session.execute(
        update(Application).where(Application.slug == "app-legacy")
        .values(approval_status=None)
    )
    await async_session.commit()
    by_dns = {e["dns_name"]: e for e in await collect_entries(async_session, HUB_HOST, ZONE)}
    assert "app-pending" not in by_dns
    assert "app-legacy" in by_dns


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
        "192.0.2.10 compta.hub.example.lan exemple-app.hub.example.lan exemple-app-ws.hub.example.lan\n"
        "203.0.113.42 remote-app.hub.example.lan\n"
    )


def test_render_zone_excludes_out_of_zone_and_empty_zone():
    entries = _entries() + [
        {"kind": "service", "dns_name": "proxy.example.lan", "fqdn": "proxy.example.lan",
         "in_zone": False, "ip": HUB_HOST, "proxy_pass": "https://h:4443",
         "is_workshop": False, "is_active": True, "enabled": True},
    ]
    zone_file = render_zone(entries, ZONE)
    assert "proxy.example.lan" not in zone_file
    assert zone_file.count("\n") == 2 + 1  # header + 2 lignes hosts (2 IPs)
    assert render_zone(entries, "") == ""


def test_render_vhosts_exact_content():
    vhosts = render_vhosts(_entries(), ZONE,
                           wildcard_dir="/etc/nginx/ssl/server/hub-dns-wildcard")
    assert set(vhosts) == {"exemple-app.conf", "remote-app.conf",
                           "exemple-app-ws.conf", "compta.hub.example.lan.conf"}
    vh = vhosts["exemple-app.conf"]
    assert "server_name exemple-app.hub.example.lan;" in vh
    assert "ssl_certificate     /etc/nginx/ssl/server/hub-dns-wildcard/server.crt;" in vh
    assert f"proxy_pass http://{HUB_HOST}:4201;" in vh
    assert "proxy_set_header Host $host;" in vh
    assert "proxy_buffering off;" in vh
    # distante → proxy_pass = frontend_url telle quelle
    assert "proxy_pass https://203.0.113.42:9443;" in vhosts["remote-app.conf"]


def test_render_vhosts_empty_when_no_zone():
    assert render_vhosts(_entries(), "", "/x") == {}


# ---------------------------------------------------------------- proxy_pass

# Charge utile d'injection telle que /register la stockait avant validation
# de custom_host : ferme le bloc server, ouvre un location qui proxifie vers
# le socket Docker du Hub.
INJECTED_FRONTEND_URL = (
    "http://evil:8080;\n}\nlocation /x { proxy_pass "
    "http://docker-socket-proxy:2375:4200"
)
INJECTED_TARGET_URL = (
    "https://host:4443;\n}\nlocation /y { proxy_pass "
    "http://docker-socket-proxy:2375"
)
WILDCARD_DIR = "/etc/nginx/ssl/server/hub-dns-wildcard"

# Texte intégral produit pour une entrée valide — référence octet pour octet :
# le contrôle au rendu ne doit rien changer aux entrées légitimes.
_GOLDEN_VHOST = (
    "# Généré par hub-api (routing_service) — NE PAS ÉDITER\n"
    "server {\n"
    "    listen 443 ssl;\n"
    "    server_name exemple-app.hub.example.lan;\n"
    "    ssl_certificate     /etc/nginx/ssl/server/hub-dns-wildcard/server.crt;\n"
    "    ssl_certificate_key /etc/nginx/ssl/server/hub-dns-wildcard/server.key;\n"
    "    location / {\n"
    "        proxy_pass http://192.0.2.10:4201;\n"
    "        proxy_set_header Host $host;\n"
    "        proxy_set_header X-Real-IP $remote_addr;\n"
    "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
    "        proxy_set_header X-Forwarded-Proto $scheme;\n"
    "        proxy_http_version 1.1;\n"
    "        proxy_set_header Upgrade $http_upgrade;\n"
    '        proxy_set_header Connection "upgrade";\n'
    "        proxy_buffering off;\n"
    "    }\n"
    "}\n"
)


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_render_vhosts_valid_entry_is_byte_identical():
    vhosts = render_vhosts(_entries()[:1], ZONE, WILDCARD_DIR)
    assert vhosts == {"exemple-app.conf": _GOLDEN_VHOST}


@pytest.mark.asyncio
async def test_render_vhosts_skips_app_with_injected_frontend_url(async_session, caplog):
    """Une app dont frontend_url contient retours à la ligne, `;` et accolade
    ne génère AUCUN vhost : aucune directive injectée n'atteint nginx."""
    async_session.add_all([
        _app("clean-app"),
        _app("evil-app", front_url=INJECTED_FRONTEND_URL),
    ])
    await async_session.commit()

    with caplog.at_level(logging.WARNING, logger="app.routing_service"):
        entries = await collect_entries(async_session, HUB_HOST, ZONE)
        vhosts = render_vhosts(entries, ZONE, WILDCARD_DIR)

    assert set(vhosts) == {"clean-app.conf"}
    rendered = "".join(vhosts.values())
    assert "docker-socket-proxy" not in rendered
    assert "location /x" not in rendered
    assert rendered.count("proxy_pass") == 1
    # Traçabilité du refus : slug de l'application + raison ; la valeur est
    # journalisée en repr() (ses retours à la ligne ne forgent pas de ligne
    # de log).
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "evil-app" in warnings[0]
    assert "\n" not in warnings[0]


def test_render_vhosts_skips_service_with_injected_target_url(caplog):
    """Même garde pour un service tiers (target_url du registre)."""
    injected = dict(
        kind="service", ref="Service injecté",
        dns_name="evil.example.org", fqdn="evil.example.org",
        in_zone=True, ip=HUB_HOST, proxy_pass=INJECTED_TARGET_URL,
        is_workshop=False, is_active=True, enabled=True,
    )
    with caplog.at_level(logging.WARNING, logger="app.routing_service"):
        vhosts = render_vhosts(_entries() + [injected], ZONE, WILDCARD_DIR)

    assert set(vhosts) == {"exemple-app.conf", "remote-app.conf",
                           "exemple-app-ws.conf", "compta.hub.example.lan.conf"}
    rendered = "".join(vhosts.values())
    assert "docker-socket-proxy" not in rendered
    assert "location /y" not in rendered
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "Service injecté" in warnings[0]


# ---------------------------------------------------------------- safe_proxy_pass


@pytest.mark.parametrize("url", [
    pytest.param(f"http://{HUB_HOST}:4201", id="ipv4-port"),
    pytest.param("https://203.0.113.42:9443", id="https-ipv4-port"),
    pytest.param("https://host.docker.internal:4443", id="nom-port"),
    pytest.param("https://h:4443", id="nom-court-port"),
    pytest.param("http://example.com", id="sans-port"),
    pytest.param("http://example.com/", id="racine"),
    pytest.param("https://example.com/sous/chemin/app", id="chemin"),
    pytest.param("https://example.com/v1/items?limit=10&sort=name",
                 id="chemin-et-query"),
    pytest.param("http://[2001:db8::1]:4200", id="ipv6-entre-crochets"),
    # nginx compare le schéma sans tenir compte de la casse
    pytest.param("HTTP://Host.Example:8080", id="schema-sans-casse"),
])
def test_safe_proxy_pass_accepts(url):
    """Une URL légitime ressort telle quelle : le rendu des entrées valides
    reste identique à l'octet près."""
    assert safe_proxy_pass(url) == url


@pytest.mark.parametrize("url", [
    pytest.param(INJECTED_FRONTEND_URL, id="injection-nginx"),
    pytest.param("http://evil:8080;\n}\nlocation /x {",
                 id="retour-ligne-point-virgule-accolade"),
    pytest.param("http://host:8080;x", id="point-virgule"),
    pytest.param("http://host:8080 {", id="accolade-et-espace"),
    pytest.param("http://host\n:8080", id="retour-ligne"),
    pytest.param("http://host:8080\t", id="tabulation-finale"),
    pytest.param("http://host/path#frag", id="fragment"),
    pytest.param("http://host/path\\x", id="antislash"),
    pytest.param('http://host/path"x', id="guillemet"),
    pytest.param("http://host/path$x", id="variable-nginx"),
    pytest.param("http://$upstream:8080", id="hote-variable-nginx"),
    pytest.param("http://user:pass@host:8080", id="userinfo"),
    pytest.param("ftp://host:21", id="schema-autre"),
    pytest.param("unix:/run/app.sock:", id="socket-unix"),
    pytest.param("http://", id="sans-hote"),
    pytest.param("http://host:99999", id="port-hors-plage"),
    pytest.param("http://host:0", id="port-zero"),
    pytest.param("http://host:8080:9090", id="deux-ports"),
    pytest.param("http://host:abc", id="port-non-numerique"),
    pytest.param("http://2001:db8::1:4200", id="ipv6-nu"),
    pytest.param("http://host_name:8080", id="hote-underscore"),
    pytest.param("http://host/\x00", id="caractere-nul"),
    pytest.param("", id="vide"),
    pytest.param(None, id="absente"),
])
def test_safe_proxy_pass_refuses(url):
    assert safe_proxy_pass(url) is None
