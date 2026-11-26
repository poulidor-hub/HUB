# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Allocation dynamique des ports d'applications (pré-vol).

`pick_free_ports` est une fonction pure (testable sans DB).
`allocate_ports_for_app` est la couche DB idempotente par (slug, host).
"""
import asyncio
import ipaddress
import logging
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.docker_scanner import docker_scanner
from app.fleet_env_scanner import read_env_claims
from app.models import Application, PortAllocation, utcnow

logger = logging.getLogger(__name__)

# Pools d'adresses PAR DÉFAUT du daemon Docker (sans default-address-pools
# surchargé) : 172.16.0.0/12 et 192.168.0.0/16. Les subnets des réseaux
# auto-créés tombent dedans — épingler une app dessus crée un chevauchement
# avec les pools de la machine hôte (F15, test PRA VM 14/11/2026 : claims
# 172.20-23/16 et 192.168.x/20 constatés en production).
_DAEMON_DEFAULT_POOLS = [ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16")]


def is_in_daemon_default_pool(cidr: str) -> bool:
    """True si le CIDR est entièrement contenu dans un pool d'adresses
    par défaut du daemon Docker. Valeurs invalides → False."""
    try:
        subnet = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return any(subnet.subnet_of(pool) for pool in _DAEMON_DEFAULT_POOLS)


class PortPoolExhausted(Exception):
    """Levée quand plus aucun port libre n'est disponible dans une plage."""


class SubnetPoolExhausted(Exception):
    """Levée quand plus aucun sous-réseau libre n'est disponible dans le pool."""


class PortAllocationConflict(Exception):
    """Levée quand des ports souhaités (sync-env : déjà figés localement)
    sont indisponibles, hors plages, ou en désaccord avec l'allocation
    existante du (slug, host)."""


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


async def fleet_env_occupied(settings, host: str) -> tuple[set[str], set[int]]:
    """Revendications APP_SUBNET/ports des .env de la flotte locale (best-effort).

    Les scans Docker ne voient que le VIVANT : une app legacy (absente du
    registre Hub) ou arrêtée au moment de l'allocation — réseau pruné,
    conteneurs down — était invisible et se faisait ré-attribuer ports et
    sous-réseau (bug 2026-11-13 : exemple-app-20 vs tiers-app-ws / exemple-app-9).
    Les `.env` de la flotte (montage `apps_host_path`) sont la source de
    vérité durable : leurs `APP_SUBNET`/`API_PORT`/`FRONTEND_PORT`/
    `FRONTEND_HTTPS_PORT` comptent comme occupés même sans conteneur vivant.

    - Désactivé si `fleet_env_scan_enabled` est faux.
    - Claims de la flotte LOCALE : ignorés pour un host distant (symétrique
      des scans Docker).
    - La lecture disque est exécutée dans un thread (comme les scans Docker).
    - Ne lève jamais : renvoie (set(), set()) en cas d'échec.
    """
    if not getattr(settings, "fleet_env_scan_enabled", True):
        return set(), set()
    if host != settings.get_hub_host():
        return set(), set()
    try:
        return await asyncio.to_thread(read_env_claims, Path(settings.apps_host_path))
    except Exception as e:
        logger.warning("Scan des claims flotte (.env) échoué (host=%s): %s", host, e)
        return set(), set()


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


async def _used_subnets(db: AsyncSession, cutoff=None) -> set[str]:
    """Sous-réseaux déjà alloués (pool Hub), TOUS hosts confondus.

    Contrairement aux ports, les sous-réseaux ne sont PAS scopés par host :
    le démon Docker est partagé par toutes les apps colocalisées, et l'host
    détecté est instable (même machine vue sous plusieurs IP selon
    l'interface/VPN). Le scoping par host a permis des collisions réelles
    (10.50.1.0/24 alloué à exemple-app-2 ET exemple-app-2-test — bug 2026-10-10).
    Retourne les CIDR en chaîne (ex: "10.50.0.0/24"). `cutoff` exclut les
    réservations expirées (sans app associée et antérieures à la date limite).
    """
    allocs = (await db.execute(select(PortAllocation))).scalars().all()
    if cutoff is not None:
        app_slugs = set((await db.execute(select(Application.slug))).scalars())
        allocs = [
            a for a in allocs
            if a.app_slug in app_slugs
            or a.allocated_at is None
            or a.allocated_at >= cutoff
        ]
    return {a.subnet for a in allocs if a.subnet}


async def reconcile_subnet_with_docker(
    db: AsyncSession, slug: str, allocation: PortAllocation, settings
) -> str | None:
    """Aligne l'allocation Hub sur le sous-réseau réel du réseau Docker vivant.

    Le compose des apps épingle l'IPAM sur ${APP_SUBNET} : si l'allocation Hub
    diverge du réseau existant (ré-allocation après purge / re-création /
    host instable, pendant que le réseau persistait), tout `make up` tente de
    recréer le réseau — refusé si des conteneurs tiers y sont attachés (exit 2,
    exemple-app-3 2026-10-10), conteneurs de l'app cassés sinon
    (« network not found », exemple-app-2-test #9).

    Règle : le réseau vivant gagne. L'allocation DB est réécrite ; l'appelant
    écrit ensuite APP_SUBNET réconcilié dans le .env (aucune divergence
    restante → Compose ne touche plus au réseau).

    - Ne s'applique qu'à l'hôte local (le scan ne reflète pas un démon distant).
    - Un seul réseau attendu pour le projet : ambiguïté (0 ou 2+) → no-op.
    - Best-effort : retourne le CIDR réconcilié, ou None si rien à faire.
    """
    if not getattr(settings, "subnet_scan_docker_enabled", True):
        return None
    if allocation.host != settings.get_hub_host():
        return None
    try:
        subnets = await asyncio.to_thread(
            docker_scanner.get_project_network_subnets, slug
        )
    except Exception as e:
        logger.warning("Scan du réseau Docker du projet '%s' échoué : %s", slug, e)
        return None
    if len(subnets) != 1:
        if len(subnets) > 1:
            logger.warning(
                "⚠️ Plusieurs réseaux Docker pour le projet '%s' (%s) : "
                "réconciliation du sous-réseau ignorée.",
                slug, ", ".join(sorted(subnets)),
            )
        return None
    real = next(iter(subnets))
    if allocation.subnet == real:
        return None
    warning_extra = ""
    if is_in_daemon_default_pool(real):
        warning_extra = (
            f" — ⚠️ {real} est dans les POOLS PAR DÉFAUT du daemon Docker "
            "(172.16/12, 192.168/16) : chevauchement avec les pools de la "
            "machine hôte (F15). Migration recommandée : ré-épingler "
            f"APP_SUBNET sur le pool dédié ({settings.app_subnet_pool}) puis "
            "`make up` pour recréer le réseau."
        )
    logger.warning(
        "🔄 Réconciliation subnet pour '%s' : allocation Hub %s → réseau Docker "
        "vivant %s (le réseau gagne, sinon Compose le recrée et casse le déploiement)%s",
        slug, allocation.subnet, real, warning_extra,
    )
    allocation.subnet = real
    await db.commit()
    await db.refresh(allocation)
    return real


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


def _ports_with_desired(
    used_api: set[int],
    used_frontend: set[int],
    *,
    api_range: tuple[int, int],
    frontend_range: tuple[int, int],
    desired_api_port: int | None,
    desired_frontend_port: int | None,
) -> tuple[int, int, int]:
    """Ports imposés (sync-env : ports déjà figés dans le profile.json).

    Chaque port souhaité est validé avec les MÊMES règles qu'un choix
    automatique (`pick_free_ports`) mais IMPOSÉ au lieu d'être choisi :
    plage respectée (api dans api_range ; frontend PAIR dans frontend_range
    avec frontend+1 pour le HTTPS), disponibilité sur l'hôte (mêmes sources :
    allocations, URLs d'apps, ports Docker occupés, claims flotte — déjà
    fusionnés dans used_api/used_frontend par l'appelant).
    Un souhait absent reste choisi librement. Lève PortAllocationConflict
    si un port souhaité est indisponible ou hors plage.
    """
    if desired_api_port is not None:
        if not (api_range[0] <= desired_api_port <= api_range[1]):
            raise PortAllocationConflict(
                f"Port API souhaité {desired_api_port} hors de la plage "
                f"autorisée {api_range[0]}-{api_range[1]}"
            )
        if desired_api_port in used_api:
            raise PortAllocationConflict(
                f"Port API souhaité {desired_api_port} déjà utilisé sur cet hôte"
            )
    if desired_frontend_port is not None:
        if desired_frontend_port % 2 != 0:
            raise PortAllocationConflict(
                f"Port frontend souhaité {desired_frontend_port} impair "
                "(attribution par pas de 2, frontend+1 réservé au HTTPS)"
            )
        if not (
            frontend_range[0] <= desired_frontend_port <= frontend_range[1]
            and desired_frontend_port + 1 <= frontend_range[1]
        ):
            raise PortAllocationConflict(
                f"Port frontend souhaité {desired_frontend_port} hors de la "
                f"plage autorisée {frontend_range[0]}-{frontend_range[1]} "
                "(frontend+1 réservé au HTTPS doit rester dedans)"
            )
        if desired_frontend_port in used_frontend or (desired_frontend_port + 1) in used_frontend:
            raise PortAllocationConflict(
                f"Port frontend souhaité {desired_frontend_port} (+1 HTTPS) "
                "déjà utilisé sur cet hôte"
            )
    if desired_api_port is not None and desired_frontend_port is not None:
        return desired_api_port, desired_frontend_port, desired_frontend_port + 1

    # Souhait partiel : l'autre port est choisi librement, sans retomber sur
    # le port imposé (ajouté aux ensembles d'utilisés).
    api, fe, https = pick_free_ports(
        used_api | ({desired_api_port} if desired_api_port is not None else set()),
        used_frontend | (
            {desired_frontend_port, desired_frontend_port + 1}
            if desired_frontend_port is not None else set()
        ),
        api_range=api_range,
        frontend_range=frontend_range,
    )
    return (
        desired_api_port if desired_api_port is not None else api,
        desired_frontend_port if desired_frontend_port is not None else fe,
        desired_frontend_port + 1 if desired_frontend_port is not None else https,
    )


async def allocate_ports_for_app(
    db: AsyncSession,
    slug: str,
    host: str,
    settings,
    desired_api_port: int | None = None,
    desired_frontend_port: int | None = None,
) -> tuple[PortAllocation, bool]:
    """Idempotent : renvoie (allocation, reused).

    Si une allocation existe pour (slug, host) → la renvoyer (reused=True).
    Sinon calculer les ports libres, persister, renvoyer (reused=False).

    Ports souhaités (desired_api_port / desired_frontend_port, sync-env) :
    - identiques à l'allocation existante → réutilisation normale ;
    - différents → PortAllocationConflict (l'app a déjà d'autres ports
      localement : le Hub ne doit ni les contredire ni changer les siens) ;
    - sur allocation fraîche → imposés après validation (plages, frontend
      pair +1 HTTPS, disponibilité — mêmes sources que le choix auto).
    Aucun souhait fourni → comportement historique inchangé.

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
        if (
            (desired_api_port is not None and desired_api_port != existing.api_port)
            or (desired_frontend_port is not None and desired_frontend_port != existing.frontend_port)
        ):
            raise PortAllocationConflict(
                f"Allocation existante slug/host = {slug}/{host} : "
                f"api={existing.api_port}, frontend={existing.frontend_port}, "
                f"https={existing.frontend_https_port} ; ports souhaités = "
                f"api={desired_api_port}, frontend={desired_frontend_port}"
            )
        if not existing.subnet:
            # Allocation antérieure à l'introduction du pool : remplir
            # paresseusement le sous-réseau (idempotent à partir d'ici).
            used_subnets = await _used_subnets(db, cutoff=cutoff)
            used_subnets |= await docker_occupied_subnets(settings, host)
            env_subnets, _ = await fleet_env_occupied(settings, host)
            used_subnets |= env_subnets
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

    env_subnets, env_ports = await fleet_env_occupied(settings, host)
    extra_used = await docker_occupied_ports(settings, host) | env_ports
    used_api, used_frontend = await _used_ports_for_host(
        db, host, extra_used=extra_used, cutoff=cutoff
    )
    if desired_api_port is not None or desired_frontend_port is not None:
        api_port, frontend_port, https_port = _ports_with_desired(
            used_api,
            used_frontend,
            api_range=(settings.app_port_api_start, settings.app_port_api_end),
            frontend_range=(settings.app_port_frontend_start, settings.app_port_frontend_end),
            desired_api_port=desired_api_port,
            desired_frontend_port=desired_frontend_port,
        )
    else:
        api_port, frontend_port, https_port = pick_free_ports(
            used_api,
            used_frontend,
            api_range=(settings.app_port_api_start, settings.app_port_api_end),
            frontend_range=(settings.app_port_frontend_start, settings.app_port_frontend_end),
        )
    used_subnets = await _used_subnets(db, cutoff=cutoff)
    used_subnets |= await docker_occupied_subnets(settings, host)
    used_subnets |= env_subnets
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
