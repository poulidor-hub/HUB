"""Chantier B — service de routage par noms (zone DNS + vhosts SNI).

Collecte depuis le registre (Application + RoutedService + PortAllocation),
rendus purs (fichier de zone dnsmasq + vhosts nginx), publication avec
reload/rollback (Task 3). La spécification fait foi :
docs/superpowers/specs/2026-11-02-routage-dns-design.md
"""

import re
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

from app import database
from app.config import get_settings

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


# ---------------------------------------------------------------- publication


class RoutingPublishError(RuntimeError):
    """La publication du routage a échoué (reload nginx/dnsmasq en erreur) —
    l'ancien contenu a été restauré ; le détail est dans routing_last_error."""


def _get_docker_client():
    import docker  # lazy : dépendance optionnelle si Docker indisponible

    return docker.from_env()  # respecte DOCKER_HOST (proxy socket — chantier A)


def _swap_dir(target: Path, new_files: dict[str, str]) -> Path | None:
    """Écrit new_files dans un dossier temporaire puis bascule atomiquement.

    Retourne le chemin de l'ancien contenu (déplacé, pour rollback) ou None."""
    tmp = target.parent / (target.name + ".new")
    old = target.parent / (target.name + ".old")
    if tmp.exists():
        _rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    for filename, content in new_files.items():
        (tmp / filename).write_text(content, encoding="utf-8")
    if old.exists():
        _rmtree(old)
    if target.exists():
        target.rename(old)
    tmp.rename(target)
    return old if old.exists() else None


def _restore_dir(target: Path, old: Path | None) -> None:
    """Rollback : supprime le nouveau dossier, remet l'ancien en place."""
    if target.exists():
        _rmtree(target)
    if old is not None and old.exists():
        old.rename(target)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _reload_containers(reloads: list[tuple[str, str]]) -> None:
    """docker exec des reloads — via le proxy socket (chantier A, EXEC=1)."""
    client = _get_docker_client()
    for name, cmd in reloads:
        result = client.containers.get(name).exec_run(cmd)
        code = result[0] if isinstance(result, tuple) else getattr(result, "exit_code", 0)
        if code not in (0, None):
            output = result[1] if isinstance(result, tuple) else getattr(result, "output", b"")
            raise RuntimeError(
                f"reload {name} (« {cmd} ») a échoué (code {code}) : "
                f"{output if isinstance(output, str) else output.decode(errors='replace')[:400]}"
            )


async def _set_last_error(session, message: str) -> None:
    from app.models import AppSetting  # évite la circularité

    row = await session.get(AppSetting, "routing_last_error")
    if row is None:
        row = AppSetting(key="routing_last_error", value=message)
        session.add(row)
    else:
        row.value = message
    await session.commit()


async def publish() -> None:
    """Publie l'état courant du registre : zone DNS + vhosts SNI.

    Ouvre sa propre session (les endpoints committent leur mutation MÉTIER puis
    appellent regenerate()). Échec de reload → rollback des fichiers + re-reload
    + routing_last_error persisté + RoutingPublishError.
    """
    from app.models import AppSetting

    settings = get_settings()
    hub_host = settings.get_hub_host()

    async with database.async_session_maker() as session:
        row = await session.get(AppSetting, "hub_dns_zone")
        zone = validate_zone(row.value if row and row.value else "")
        entries = await collect_entries(session, hub_host, zone)

    zone_content = render_zone(entries, zone)
    vhosts = render_vhosts(entries, zone, WILDCARD_CERT_DIR_NGINX)

    dns_dir = Path(settings.routing_dns_dir)
    gw_dir = Path(settings.routing_gateway_dir)
    old_dns = _swap_dir(dns_dir, {"hub.zone": zone_content} if zone_content else {})
    old_gw = _swap_dir(gw_dir, vhosts)

    reloads = [("hub-dns", "kill -HUP 1"), ("hub-frontend", "nginx -s reload")]
    try:
        _reload_containers(reloads)
    except Exception as exc:
        _restore_dir(dns_dir, old_dns)
        _restore_dir(gw_dir, old_gw)
        try:
            _reload_containers(reloads)  # re-servir l'ancien contenu
        except Exception:
            pass  # l'erreur primaire est celle qu'on remonte
        async with database.async_session_maker() as err_session:
            await _set_last_error(err_session, str(exc))
        raise RoutingPublishError(str(exc)) from exc

    async with database.async_session_maker() as err_session:
        await _set_last_error(err_session, "")


async def regenerate() -> None:
    """Hook central : régénère et publie (appelé après toute mutation du registre,
    au succès d'un déploiement et au boot). Ne lève jamais : l'échec est persisté
    dans routing_last_error et visible dans le bandeau de la vue."""
    try:
        await publish()
    except RoutingPublishError as exc:
        import logging

        logging.getLogger(__name__).warning("Publication du routage échouée : %s", exc)
    except Exception as exc:  # collecte/DB inattendue — ne casse jamais l'appelant
        import logging

        logging.getLogger(__name__).warning("Régénération du routage impossible : %s", exc)
