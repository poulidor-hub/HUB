"""Chantier B — service de routage par noms (zone DNS + vhosts SNI).

Collecte depuis le registre (Application + RoutedService + PortAllocation),
rendus purs (fichier de zone dnsmasq + vhosts nginx), publication avec
reload/rollback (Task 3). La spécification fait foi :
docs/superpowers/specs/2026-11-02-routage-dns-design.md
"""

import asyncio
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

WILDCARD_CERT_DIR_NGINX = "/etc/nginx/ssl/server/hub-dns-wildcard"
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



async def assert_dns_name_available(db, dns_name: str, zone: str,
                                    exclude_app_slug: str | None = None,
                                    exclude_service_id: int | None = None) -> None:
    """Unicité cross-table : Application (fqdn) ∪ RoutedService (dns_name).

    Lève ValueError avec un message clair — les routes la convertissent
    (422/400) selon le contexte. Le dns_name d'app est un nom court (la zone
    est ajoutée pour la comparaison), celui d'un service est un FQDN libre.
    AsyncSession async : à awaiter depuis les callers.
    """
    fqdn = _fqdn_of(dns_name, zone)
    from app.models import (  # import local : évite la circularité
        Application, RoutedService,
    )

    apps = (await db.execute(
        select(Application).where(Application.dns_name.isnot(None))
    )).scalars().all()
    for app in apps:
        if app.slug == exclude_app_slug:
            continue
        if _fqdn_of(app.dns_name, zone) == fqdn:
            raise ValueError(f"« {fqdn} » est déjà utilisé par l'app {app.slug}.")
    services = (await db.execute(select(RoutedService))).scalars().all()
    for svc in services:
        if exclude_service_id is not None and svc.id == exclude_service_id:
            continue
        if svc.dns_name.lower() == fqdn:
            raise ValueError(f"« {fqdn} » est déjà utilisé par le service {svc.name}.")


def _fqdn_of(name: str, zone: str) -> str:
    name = (name or "").strip().lower().rstrip(".")
    if zone and (name == zone or name.endswith(f".{zone}")):
        return name
    return f"{name}.{zone}" if zone else name


def _resolve_host_to_ip(host: str | None) -> str | None:
    """IP si host est déjà une IP, sinon résolution DNS — None si irrésoluble.

    Nécessaire pour les lignes `address=` de dnsmasq (IP uniquement) :
    les apps peuvent pointer host.docker.internal ou un nom LAN."""
    if not host:
        return None
    import ipaddress

    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        import socket

        return socket.gethostbyname(host)
    except OSError:
        return None


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
        if getattr(app, "is_system_app", False):
            continue  # app système du Hub (ex. « hub ») : jamais routée par nom
        alloc = allocations.get(app.slug)
        # proxy_pass : frontend_url du REGISTRE en priorité (scheme conservé —
        # http ou https selon le vrai listener de l'app) ; fallback : port HTTP
        # alloué ; sinon rien à router.
        if app.frontend_url:
            proxy_pass = app.frontend_url
        elif alloc and alloc.frontend_port:
            proxy_pass = f"http://{hub_host}:{alloc.frontend_port}"
        else:
            continue
        if app.is_remote:
            ip = await asyncio.to_thread(
                _resolve_host_to_ip, _host_of(app.frontend_url)
            ) or hub_host
        else:
            ip = await asyncio.to_thread(_resolve_host_to_ip, hub_host) or hub_host
        if ip is None:
            continue
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
            "in_zone": in_zone, "ip": await asyncio.to_thread(_resolve_host_to_ip, hub_host) or hub_host,
            "proxy_pass": svc.target_url,
            "is_workshop": False, "is_active": True, "enabled": True,
        })

    return entries


# ---------------------------------------------------------------- rendus purs


def render_zone(entries: list[dict], zone: str) -> str:
    """Fichier hosts additionnel pour dnsmasq (addn-hosts) — entrées in_zone.

    Format hosts (une ligne par IP, tous les noms de cette IP) : plus robuste
    que des directives address= (une ligne invalide n'invalide pas le fichier).
    """
    if not zone:
        return ""
    by_ip: dict[str, list[str]] = {}
    for e in entries:
        if not e.get("in_zone"):
            continue
        by_ip.setdefault(e["ip"], []).append(e["fqdn"])
    lines = [_ZONE_FILE_HEADER]
    for ip in sorted(by_ip):
        lines.append(f"{ip} " + " ".join(sorted(by_ip[ip])))
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
    return vhosts


# ---------------------------------------------------------------- publication


class RoutingPublishError(RuntimeError):
    """La publication du routage a échoué (reload nginx/dnsmasq en erreur) —
    l'ancien contenu a été restauré ; le détail est dans routing_last_error."""


def _get_docker_client():
    import docker  # lazy : dépendance optionnelle si Docker indisponible

    return docker.from_env()  # respecte DOCKER_HOST (proxy socket — chantier A)


def _apply_dir(target: Path, new_files: dict[str, str]) -> dict[str, str]:
    """Applique new_files dans target (in-place) et retourne l'ancien contenu.

    Le rename de dossier est impossible ici : /code/dns et /code/gateway sont
    des points de montage bind (EBUSY). On purge donc les fichiers puis on
    écrit les nouveaux — le reload se fait après l'écriture complète.
    """
    target.mkdir(parents=True, exist_ok=True)
    old: dict[str, str] = {}
    for f in sorted(target.iterdir()):
        if f.is_file():
            old[f.name] = f.read_text(encoding="utf-8")
            f.unlink()
    for name, content in new_files.items():
        (target / name).write_text(content, encoding="utf-8")
    return old


def _restore_dir(target: Path, old_files: dict[str, str] | None) -> None:
    """Rollback : remet exactement l'ancien contenu (in-place)."""
    if old_files is None:
        return
    target.mkdir(parents=True, exist_ok=True)
    for f in sorted(target.iterdir()):
        if f.is_file():
            f.unlink()
    for name, content in old_files.items():
        (target / name).write_text(content, encoding="utf-8")


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
    appellent regenerate()). TOUT échec — wildcard, écritures, reload — déclenche
    rollback (fichiers + re-reload) + routing_last_error persisté +
    RoutingPublishError (review I7). Les opérations bloquantes (SDK docker,
    résolutions, RSA) passent en to_thread (review I8 — jamais dans la boucle).
    """
    import asyncio
    import logging

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
    reloads = [("hub-dns", "kill -HUP 1"), ("hub-frontend", "nginx -s reload")]

    old_dns: dict[str, str] | None = None
    old_gw: dict[str, str] | None = None
    try:
        if zone:
            import pki  # hub/pki.py — racine du contexte /code

            await asyncio.to_thread(pki.ensure_wildcard_cert, zone)
        old_dns = await asyncio.to_thread(
            _apply_dir, dns_dir, {"hub.hosts": zone_content} if zone_content else {}
        )
        old_gw = await asyncio.to_thread(_apply_dir, gw_dir, vhosts)
        await asyncio.to_thread(_reload_containers, reloads)
    except Exception as exc:
        await asyncio.to_thread(_restore_dir, dns_dir, old_dns)
        await asyncio.to_thread(_restore_dir, gw_dir, old_gw)
        try:
            await asyncio.to_thread(_reload_containers, reloads)  # re-servir l'ancien
        except Exception:
            pass  # l'erreur primaire est celle qu'on remonte
        async with database.async_session_maker() as err_session:
            await _set_last_error(err_session, str(exc))
        logging.getLogger(__name__).warning(
            "Publication du routage échouée (rollback appliqué) : %s", exc)
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
