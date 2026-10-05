"""Allocation dynamique des ports d'applications (pré-vol).

`pick_free_ports` est une fonction pure (testable sans DB).
`allocate_ports_for_app` est la couche DB idempotente par (slug, host).
"""
import asyncio
import ipaddress
import logging
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.docker_scanner import docker_scanner
from app.models import Application, PortAllocation, utcnow

logger = logging.getLogger(__name__)


class PortPoolExhausted(Exception):
    """Levée quand plus aucun port libre n'est disponible dans une plage."""


class SubnetPoolExhausted(Exception):
    """Levée quand plus aucun sous-réseau libre n'est disponible dans le pool."""


async def docker_occupied_ports(settings, host: str) -> set[int]:
    """Ports liés sur l'hôte LOCAL par des conteneurs Docker (best-effort).

    - Désactivé si `port_scan_docker_enabled` est faux.
    - Le scan ne reflète que l'hôte du Hub : ignoré (set()) pour un host distant.
    - Le scan Docker étant bloquant, il est exécuté dans un thread.
    - Ne lève jamais : renvoie set() en cas d'échec (fallback gracieux).
    """
    if not getattr(settings, "port_scan_docker_enabled", True):
        return set()
    if host != settings.get_hub_host():
        return set()
    try:
        return await asyncio.to_thread(docker_scanner.get_used_host_ports)
    except Exception as e:
        logger.warning("Scan Docker des ports occupés échoué (host=%s): %s", host, e)
        return set()


async def docker_occupied_subnets(settings, host: str) -> set[str]:
    """Sous-réseaux réellement présents sur l'hôte LOCAL (best-effort).

    Symétrique de `docker_occupied_ports` : capte les épinglages manuels
    (réseaux Docker créés hors allocation Hub) pour ne jamais ré-attribuer
    un sous-réseau occupé (« Pool overlaps with other one »).

    - Désactivé si `subnet_scan_docker_enabled` est faux.
    - Le scan ne reflète que l'hôte du Hub : ignoré (set()) pour un host distant.
    - Le scan Docker étant bloquant, il est exécuté dans un thread.
    - Ne lève jamais : renvoie set() en cas d'échec (fallback gracieux).
    """
    if not getattr(settings, "subnet_scan_docker_enabled", True):
        return set()
    if host != settings.get_hub_host():
        return set()
    try:
        return await asyncio.to_thread(docker_scanner.get_used_network_subnets)
    except Exception as e:
        logger.warning("Scan Docker des sous-réseaux occupés échoué (host=%s): %s", host, e)
        return set()


def pick_free_ports(
    used_api: set[int],
    used_frontend: set[int],
    *,
    api_range: tuple[int, int],
    frontend_range: tuple[int, int],
) -> tuple[int, int, int]:
    """Retourne (api_port, frontend_port, frontend_https_port).

    - api_port : plus petit port libre dans api_range.
    - frontend_port : plus petit port libre (pas de 2) dans frontend_range tel
      que frontend_port et frontend_port+1 soient tous deux libres.
    - frontend_https_port = frontend_port + 1.
    Lève PortPoolExhausted si une plage est saturée.
    """
    api_port = next(
        (p for p in range(api_range[0], api_range[1] + 1) if p not in used_api),
        None,
    )
    if api_port is None:
        raise PortPoolExhausted(f"Plage API {api_range} saturée")

    frontend_port = None
    for p in range(frontend_range[0], frontend_range[1], 2):
        if p not in used_frontend and (p + 1) not in used_frontend:
            frontend_port = p
            break
    if frontend_port is None:
        raise PortPoolExhausted(f"Plage Frontend {frontend_range} saturée")

    return api_port, frontend_port, frontend_port + 1


def _ports_from_url(url: str | None) -> set[int]:
    if not url:
        return set()
    try:
        port = urlparse(url).port
        return {port} if port else set()
    except ValueError:
        return set()


def _reservation_cutoff(settings):
    """Date limite d'une réservation de ports (None si TTL désactivé).

    Une RÉSERVATION est une allocation sans application associée : ligne créée
    par le prepare du catalogue GitHub (ou un /allocate-ports orphelin) avant
    même que le formulaire ne soit soumis. Passé le TTL, ports et sous-réseau
    redeviennent attribuables — sinon chaque « Ajouter » abandonné fuyait
    définitivement ports + sous-réseau (bug 2026-10-05).
    """
    minutes = getattr(settings, "port_reservation_ttl_minutes", 0) or 0
    if minutes <= 0:
        return None
    return utcnow() - timedelta(minutes=minutes)


def _expired_reservations(allocs, app_slugs: set[str], cutoff) -> list:
    """Lignes à purger : réservations (sans app) antérieures au cutoff.

    N'expirent JAMAIS : une allocation adossée à une app existante, une ligne
    legacy sans allocated_at (insérée hors modèle), et tout l'ensemble si
    cutoff est None (TTL=0).
    """
    if cutoff is None:
        return []
    return [
        a for a in allocs
        if a.app_slug not in app_slugs
        and a.allocated_at is not None
        and a.allocated_at < cutoff
    ]


async def sweep_expired_reservations(db: AsyncSession, settings) -> int:
    """Purge les réservations de ports expirées (sans app associée).

    Appelé paresseusement à chaque allocation. Committe lui-même : la purge
    est indépendante de la transaction de l'appelant. Renvoie le nombre de
    lignes supprimées.
    """
    cutoff = _reservation_cutoff(settings)
    if cutoff is None:
        return 0
    app_slugs = set((await db.execute(select(Application.slug))).scalars())
    allocs = (await db.execute(select(PortAllocation))).scalars().all()
    expired = _expired_reservations(allocs, app_slugs, cutoff)
    for row in expired:
        await db.delete(row)
    if expired:
        await db.commit()
        logger.info(
            "🧹 %d réservation(s) de ports expirée(s) purgée(s) (TTL=%s min) : %s",
            len(expired), getattr(settings, "port_reservation_ttl_minutes", 0),
            ", ".join(sorted(a.app_slug for a in expired)),
        )
    return len(expired)


async def _used_ports_for_host(
    db: AsyncSession, host: str, extra_used: set[int] | None = None, cutoff=None
) -> tuple[set[int], set[int]]:
    """Retourne (ports_api_utilises, ports_frontend_utilises) pour un host.

    Source : URLs des apps enregistrées + allocations existantes + `extra_used`
    (ex: ports Docker occupés sur l'hôte local).
    Les ports sont scopés par host (les apps d'autres hosts sont ignorées).
    Un même numéro ne doit jamais être réutilisé sur un host, quel que soit le
    rôle : on renvoie donc le même ensemble fusionné pour API et Frontend
    (les plages ne se chevauchant pas, c'est sûr).
    `cutoff` (optionnel) exclut les réservations expirées — sans app associée
    et antérieures à la date limite — qui redeviennent attribuables.
    """
    used_api: set[int] = set()
    used_frontend: set[int] = set()

    apps = (await db.execute(select(Application))).scalars().all()
    for app in apps:
        app_host = urlparse(app.api_url).hostname if app.api_url else None
        if not app_host:
            app_host = urlparse(app.frontend_url).hostname if app.frontend_url else None
        if app_host != host:
            continue
        used_api |= _ports_from_url(app.api_url)
        used_frontend |= _ports_from_url(app.frontend_url)
        if app.frontend_https_port:
            used_frontend.add(app.frontend_https_port)

    allocs = (
        await db.execute(select(PortAllocation).where(PortAllocation.host == host))
    ).scalars().all()
    if cutoff is not None:
        app_slugs = set((await db.execute(select(Application.slug))).scalars())
        allocs = [
            a for a in allocs
            if a.app_slug in app_slugs
            or a.allocated_at is None
            or a.allocated_at >= cutoff
        ]
    for a in allocs:
        used_api.add(a.api_port)
        used_frontend.add(a.frontend_port)
        used_frontend.add(a.frontend_https_port)

    all_used = used_api | used_frontend
    if extra_used:
        all_used = all_used | set(extra_used)
    return all_used, all_used


async def _used_subnets_for_host(db: AsyncSession, host: str, cutoff=None) -> set[str]:
    """Sous-réseaux déjà alloués (pool Hub) pour un host donné.

    Scopé par host comme les ports : deux apps sur des machines différentes
    peuvent réutiliser le même sous-réseau. Retourne les CIDR en chaîne
    (ex: "10.50.0.0/24"). `cutoff` exclut les réservations expirées (sans app
    associée et antérieures à la date limite).
    """
    allocs = (
        await db.execute(select(PortAllocation).where(PortAllocation.host == host))
    ).scalars().all()
    if cutoff is not None:
        app_slugs = set((await db.execute(select(Application.slug))).scalars())
        allocs = [
            a for a in allocs
            if a.app_slug in app_slugs
            or a.allocated_at is None
            or a.allocated_at >= cutoff
        ]
    return {a.subnet for a in allocs if a.subnet}


def pick_free_subnet(
    used: set[str],
    *,
    pool: str,
    size: int,
) -> str:
    """Retourne le premier sous-réseau libre de taille `size` dans `pool`.

    `used` contient les CIDR déjà attribués (le pool est disjoint du reste du
    parc par convention, voir APP_SUBNET_POOL dans la config).
    Lève SubnetPoolExhausted si le pool est saturé.
    """
    try:
        network = ipaddress.ip_network(pool, strict=False)
        subnets = network.subnets(new_prefix=size)
    except (ValueError, TypeError):
        raise SubnetPoolExhausted(f"Pool de sous-réseaux invalide : {pool}/{size}")

    used_networks = set()
    for cidr in used:
        try:
            used_networks.add(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning("CIDR invalide ignoré : %s", cidr)

    for subnet in subnets:
        if not any(subnet.overlaps(u) for u in used_networks):
            return str(subnet)
    raise SubnetPoolExhausted(f"Pool de sous-réseaux saturé : {pool}/{size}")


async def allocate_ports_for_app(
    db: AsyncSession,
    slug: str,
    host: str,
    settings,
) -> tuple[PortAllocation, bool]:
    """Idempotent : renvoie (allocation, reused).

    Si une allocation existe pour (slug, host) → la renvoyer (reused=True).
    Sinon calculer les ports libres, persister, renvoyer (reused=False).

    Purge d'abord les réservations expirées (allocations sans application
    associée, au-delà de port_reservation_ttl_minutes) : les ports et
    sous-réseaux des « Ajouter » jamais finalisés redeviennent attribuables.
    """
    await sweep_expired_reservations(db, settings)
    cutoff = _reservation_cutoff(settings)

    existing = (
        await db.execute(
            select(PortAllocation).where(
                PortAllocation.app_slug == slug,
                PortAllocation.host == host,
            )
        )
    ).scalar_one_or_none()
    if existing:
        if not existing.subnet:
            # Allocation antérieure à l'introduction du pool : remplir
            # paresseusement le sous-réseau (idempotent à partir d'ici).
            used_subnets = await _used_subnets_for_host(db, host, cutoff=cutoff)
            used_subnets |= await docker_occupied_subnets(settings, host)
            existing.subnet = pick_free_subnet(
                used_subnets,
                pool=settings.app_subnet_pool,
                size=settings.app_subnet_size,
            )
            await db.commit()
            await db.refresh(existing)
            logger.info(
                "🌐 Sous-réseau attribué rétroactivement pour '%s' (host=%s): %s",
                slug, host, existing.subnet,
            )
        return existing, True

    extra_used = await docker_occupied_ports(settings, host)
    used_api, used_frontend = await _used_ports_for_host(
        db, host, extra_used=extra_used, cutoff=cutoff
    )
    api_port, frontend_port, https_port = pick_free_ports(
        used_api,
        used_frontend,
        api_range=(settings.app_port_api_start, settings.app_port_api_end),
        frontend_range=(settings.app_port_frontend_start, settings.app_port_frontend_end),
    )
    used_subnets = await _used_subnets_for_host(db, host, cutoff=cutoff)
    used_subnets |= await docker_occupied_subnets(settings, host)
    subnet = pick_free_subnet(
        used_subnets,
        pool=settings.app_subnet_pool,
        size=settings.app_subnet_size,
    )

    alloc = PortAllocation(
        app_slug=slug,
        host=host,
        api_port=api_port,
        frontend_port=frontend_port,
        frontend_https_port=https_port,
        subnet=subnet,
    )
    db.add(alloc)
    await db.commit()
    await db.refresh(alloc)
    logger.info(
        "🎰 Ports alloués pour '%s' (host=%s): api=%d frontend=%d https=%d subnet=%s",
        slug, host, api_port, frontend_port, https_port, subnet,
    )
    return alloc, False


async def release_ports_for_app(db: AsyncSession, slug: str) -> int:
    """Libère (supprime) toutes les allocations de ports d'un slug.

    Appelée lors de la suppression d'une application pour rendre ses ports au
    pool. Ne committe pas : l'appelant gère la transaction. Renvoie le nombre
    de lignes supprimées.
    """
    rows = (
        await db.execute(select(PortAllocation).where(PortAllocation.app_slug == slug))
    ).scalars().all()
    for row in rows:
        await db.delete(row)
    if rows:
        logger.info("🧹 %d allocation(s) de ports libérée(s) pour '%s'", len(rows), slug)
    return len(rows)
