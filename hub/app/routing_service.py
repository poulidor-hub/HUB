"""Chantier B — service de routage par noms (zone DNS + vhosts SNI).

Collecte depuis le registre (Application + RoutedService + PortAllocation),
rendus purs (fichier de zone dnsmasq + vhosts nginx), publication avec
reload/rollback (Task 3). La spécification fait foi :
docs/superpowers/specs/2026-11-02-routage-dns-design.md
"""

import re
from urllib.parse import urlparse

from sqlalchemy import select

RESERVED_DNS_NAMES = {"www", "mail", "ns1", "ns2", "api", "hub"}

_DNS_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_DNS_NAME_RE = re.compile(rf"^{_DNS_LABEL}$")
_ZONE_RE = re.compile(rf"^{_DNS_LABEL}(\.{_DNS_LABEL})+$")

WILDCARD_CERT_DIR_NGINX = "/etc/nginx/ssl/hub-dns-wildcard"
_ZONE_FILE_HEADER = "# Généré par hub-api (routing_service) — NE PAS ÉDITER"


# ---------------------------------------------------------------- validations


def validate_dns_name(name: str) -> str:
    """Valide un nom de routage d'app (nom court, sans la zone). ValueError sinon."""
    name = (name or "").strip()
    if not _DNS_NAME_RE.match(name):
        raise ValueError(
            "Nom de routage invalide : minuscules/chiffres/tirets, "
            "ne commence ni ne finit par un tiret (63 max)."
        )
    if name in RESERVED_DNS_NAMES:
        raise ValueError(f"Nom de routage réservé : « {name} ».")
    return name


def validate_zone(zone: str) -> str:
    """Valide une zone DNS complète (ex. hub.example.lan) — ≥ 2 labels. ValueError sinon."""
    zone = (zone or "").strip().lower().rstrip(".")
    if not zone:
        return ""  # zone vide = routage par nom désactivé (état validé)
    if not _ZONE_RE.match(zone):
        raise ValueError(
            "Zone invalide : au moins 2 labels (ex. hub.example.lan), "
            "minuscules/chiffres/tirets."
        )
    return zone


# ---------------------------------------------------------------- collecte


def _host_of(url: str) -> str | None:
    return urlparse(url).hostname if url else None


def _port_of(url: str, default: int = 80) -> int:
    return urlparse(url).port or default


async def collect_entries(session, hub_host: str, zone: str) -> list[dict]:
    """Construit les entrées de routage depuis le registre.

    Chaque entrée : {kind, ref, dns_name, fqdn, in_zone, ip, proxy_pass,
    is_workshop, is_active, enabled}. Les apps inactives sont conservées
    (statut visible, liens non cassés) ; les ateliers -ws inclus et marqués ;
    les services tiers désactivés exclus ; dns_name inconnu → ignoré (défensif).
    """
    from app.models import (  # import local : évite la circularité models<->database
        Application, PortAllocation, RoutedService,
    )

    entries: list[dict] = []

    apps = (await session.execute(select(Application))).scalars().all()
    allocations = {
        a.app_slug: a
        for a in (await session.execute(select(PortAllocation))).scalars().all()
    }
    for app in apps:
        dns_name = (app.dns_name or "").strip().lower()
        if not dns_name:
            continue  # défensif : le backfill/lifespan garantit la présence
        alloc = allocations.get(app.slug)
        if app.is_remote:
            ip = _host_of(app.frontend_url) or hub_host
            proxy_pass = app.frontend_url
        else:
            ip = hub_host
            if alloc and alloc.frontend_port:
                proxy_pass = f"http://{hub_host}:{alloc.frontend_port}"
            else:
                port = _port_of(app.frontend_url or "", 80)
                proxy_pass = f"http://{hub_host}:{port}"
        entries.append({
            "kind": "app", "ref": app.slug, "dns_name": dns_name,
            "fqdn": f"{dns_name}.{zone}" if zone else dns_name,
            "in_zone": bool(zone), "ip": ip, "proxy_pass": proxy_pass,
            "is_workshop": bool(app.is_workshop),
            "is_active": bool(app.is_active), "enabled": True,
        })

    services = (await session.execute(
        select(RoutedService).where(RoutedService.is_enabled.is_(True))
    )).scalars().all()
    for svc in services:
        fqdn = svc.dns_name.strip().lower()
        in_zone = bool(zone) and (fqdn == zone or fqdn.endswith(f".{zone}"))
        entries.append({
            "kind": "service", "ref": svc.name, "dns_name": fqdn, "fqdn": fqdn,
            "in_zone": in_zone, "ip": hub_host, "proxy_pass": svc.target_url,
            "is_workshop": False, "is_active": True, "enabled": True,
        })

    return entries


# ---------------------------------------------------------------- rendus purs


def render_zone(entries: list[dict], zone: str) -> str:
    """Fichier de zone dnsmasq (format hosts) — uniquement les entrées in_zone."""
    if not zone:
        return ""
    lines = [_ZONE_FILE_HEADER]
    for e in sorted((e for e in entries if e.get("in_zone")),
                    key=lambda e: e["fqdn"]):
        lines.append(f"address=/{e['fqdn']}/{e['ip']}")
    return "\n".join(lines) + "\n"


def render_vhosts(entries: list[dict], zone: str, wildcard_dir: str) -> dict[str, str]:
    """Un vhost SNI par entrée (apps + services tiers activés) — dict {filename: contenu}."""
    if not zone:
        return {}
    vhosts: dict[str, str] = {}
    for e in sorted(entries, key=lambda e: e["fqdn"]):
        vhosts[f"{e['dns_name']}.conf"] = (
            "# Généré par hub-api (routing_service) — NE PAS ÉDITER\n"
            "server {\n"
            "    listen 443 ssl;\n"
            f"    server_name {e['fqdn']};\n"
            f"    ssl_certificate     {wildcard_dir}/server.crt;\n"
            f"    ssl_certificate_key {wildcard_dir}/server.key;\n"
            "    location / {\n"
            f"        proxy_pass {e['proxy_pass']};\n"
            "        include /etc/nginx/proxy-common.inc;\n"
            "    }\n"
            "}\n"
        )
    return vhosts
