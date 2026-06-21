"""Allocation dynamique des ports d'applications (pré-vol).

`pick_free_ports` est une fonction pure (testable sans DB).
`allocate_ports_for_app` est la couche DB idempotente par (slug, host).
"""
import logging
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, PortAllocation

logger = logging.getLogger(__name__)


class PortPoolExhausted(Exception):
    """Levée quand plus aucun port libre n'est disponible dans une plage."""


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


async def _used_ports_for_host(db: AsyncSession, host: str) -> tuple[set[int], set[int]]:
    """Retourne (ports_api_utilises, ports_frontend_utilises) pour un host.

    Source : URLs des apps enregistrées + allocations existantes.
    Les ports sont scopés par host (les apps d'autres hosts sont ignorées).
    Un même numéro ne doit jamais être réutilisé sur un host, quel que soit le
    rôle : on renvoie donc le même ensemble fusionné pour API et Frontend
    (les plages ne se chevauchant pas, c'est sûr).
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
    for a in allocs:
        used_api.add(a.api_port)
        used_frontend.add(a.frontend_port)
        used_frontend.add(a.frontend_https_port)

    all_used = used_api | used_frontend
    return all_used, all_used


async def allocate_ports_for_app(
    db: AsyncSession,
    slug: str,
    host: str,
    settings,
) -> tuple[PortAllocation, bool]:
    """Idempotent : renvoie (allocation, reused).

    Si une allocation existe pour (slug, host) → la renvoyer (reused=True).
    Sinon calculer les ports libres, persister, renvoyer (reused=False).
    """
    existing = (
        await db.execute(
            select(PortAllocation).where(
                PortAllocation.app_slug == slug,
                PortAllocation.host == host,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing, True

    used_api, used_frontend = await _used_ports_for_host(db, host)
    api_port, frontend_port, https_port = pick_free_ports(
        used_api,
        used_frontend,
        api_range=(settings.app_port_api_start, settings.app_port_api_end),
        frontend_range=(settings.app_port_frontend_start, settings.app_port_frontend_end),
    )

    alloc = PortAllocation(
        app_slug=slug,
        host=host,
        api_port=api_port,
        frontend_port=frontend_port,
        frontend_https_port=https_port,
    )
    db.add(alloc)
    await db.commit()
    await db.refresh(alloc)
    logger.info(
        "🎰 Ports alloués pour '%s' (host=%s): api=%d frontend=%d https=%d",
        slug, host, api_port, frontend_port, https_port,
    )
    return alloc, False
