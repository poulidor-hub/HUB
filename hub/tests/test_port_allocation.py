"""Tests de l'allocation dynamique des ports."""
import pytest
from sqlalchemy import select

from app.models import PortAllocation


@pytest.mark.asyncio
async def test_port_allocation_model_persists(async_session):
    alloc = PortAllocation(
        app_slug="demo-app",
        host="192.0.2.12",
        api_port=9000,
        frontend_port=4200,
        frontend_https_port=4201,
    )
    async_session.add(alloc)
    await async_session.commit()

    result = await async_session.execute(
        select(PortAllocation).where(PortAllocation.app_slug == "demo-app")
    )
    row = result.scalar_one()
    assert row.host == "192.0.2.12"
    assert row.api_port == 9000
    assert row.frontend_port == 4200
    assert row.frontend_https_port == 4201


from app.port_allocator import pick_free_ports, PortPoolExhausted


def test_pick_free_ports_lowest_free():
    api, fe, https = pick_free_ports(
        used_api=set(), used_frontend=set(),
        api_range=(9000, 9499), frontend_range=(4200, 4699),
    )
    assert (api, fe, https) == (9000, 4200, 4201)


def test_pick_free_ports_skips_used():
    api, fe, https = pick_free_ports(
        used_api={9000, 9001}, used_frontend={4200, 4201},
        api_range=(9000, 9499), frontend_range=(4200, 4699),
    )
    assert api == 9002
    assert (fe, https) == (4202, 4203)


def test_pick_free_ports_frontend_reserves_plus_one():
    # 4202 est libre mais 4203 est pris -> on saute au pas suivant (4204)
    api, fe, https = pick_free_ports(
        used_api=set(), used_frontend={4200, 4203},
        api_range=(9000, 9499), frontend_range=(4200, 4699),
    )
    assert (fe, https) == (4204, 4205)


def test_pick_free_ports_exhausted():
    with pytest.raises(PortPoolExhausted):
        pick_free_ports(
            used_api=set(range(9000, 9500)), used_frontend=set(),
            api_range=(9000, 9499), frontend_range=(4200, 4699),
        )


from unittest.mock import patch
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_allocate_ports_rejected_without_whitelist(client: AsyncClient):
    response = await client.post("/api/apps/allocate-ports", json={"slug": "demo-app", "host": "192.0.2.12"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_allocate_ports_allocates_and_is_idempotent(client: AsyncClient):
    with patch('app.routes.apps.is_docker_network_ip', return_value=True):
        r1 = await client.post("/api/apps/allocate-ports", json={"slug": "demo-app", "host": "192.0.2.12"})
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["api_port"] == 9000
        assert body1["frontend_port"] == 4200
        assert body1["frontend_https_port"] == 4201
        assert body1["reused"] is False

        r2 = await client.post("/api/apps/allocate-ports", json={"slug": "demo-app", "host": "192.0.2.12"})
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["api_port"] == 9000
        assert body2["frontend_port"] == 4200
        assert body2["reused"] is True


@pytest.mark.asyncio
async def test_allocate_ports_distinct_apps_get_distinct_ports(client: AsyncClient):
    with patch('app.routes.apps.is_docker_network_ip', return_value=True):
        r1 = await client.post("/api/apps/allocate-ports", json={"slug": "app-one", "host": "10.0.0.1"})
        r2 = await client.post("/api/apps/allocate-ports", json={"slug": "app-two", "host": "10.0.0.1"})
        p1, p2 = r1.json(), r2.json()
        assert p1["api_port"] != p2["api_port"]
        assert p1["frontend_port"] != p2["frontend_port"]


from app.auth import create_access_token
from tests.conftest import get_auth_header


@pytest.mark.asyncio
async def test_ports_status_requires_admin(client: AsyncClient):
    response = await client.get("/api/apps/admin/ports/status")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_ports_status_returns_pool_state(client: AsyncClient, admin_user):
    with patch('app.routes.apps.is_docker_network_ip', return_value=True):
        await client.post("/api/apps/allocate-ports", json={"slug": "demo-app", "host": "192.0.2.12"})

    token, _, _ = create_access_token(username=admin_user.username, apps={})
    response = await client.get("/api/apps/admin/ports/status", headers=get_auth_header(token))
    assert response.status_code == 200
    body = response.json()
    assert body["api_range"] == [9000, 9499]
    assert body["frontend_range"] == [4200, 4699]
    assert 9000 in body["used_api_ports"]
    assert any(a["app_slug"] == "demo-app" for a in body["allocations"])


# ===== Exclusion des ports Docker occupés (scan hôte local) =====

from app.port_allocator import allocate_ports_for_app
from app.docker_scanner import DockerScanner
from app.config import get_settings

settings = get_settings()


def test_get_used_host_ports_empty_when_unavailable():
    """get_used_host_ports est best-effort : set() si Docker indisponible."""
    s = DockerScanner()
    s._available = False
    s._client = False
    assert s.get_used_host_ports() == set()


@pytest.mark.asyncio
async def test_allocate_ports_excludes_docker_occupied(async_session):
    """Les host_port Docker occupés sont exclus de la plage d'attribution."""
    hub_host = settings.get_hub_host()
    with patch('app.port_allocator.docker_scanner.get_used_host_ports', return_value={9000, 4200, 4201}):
        alloc, reused = await allocate_ports_for_app(async_session, "scan-app", hub_host, settings)
    assert reused is False
    assert alloc.api_port == 9001          # 9000 occupé par Docker -> sauté
    assert alloc.frontend_port == 4202     # 4200/4201 occupés -> pair suivante


@pytest.mark.asyncio
async def test_allocate_ports_scan_not_applied_for_remote_host(async_session):
    """Le scan ne reflète que l'hôte local : ignoré pour un host distant."""
    remote = "10.20.30.40"
    assert remote != settings.get_hub_host()
    with patch('app.port_allocator.docker_scanner.get_used_host_ports', return_value={9000}):
        alloc, _ = await allocate_ports_for_app(async_session, "remote-app", remote, settings)
    assert alloc.api_port == 9000


@pytest.mark.asyncio
async def test_allocate_ports_scan_disabled_by_flag(async_session, monkeypatch):
    """Feature flag désactivé : aucun scan n'est déclenché."""
    hub_host = settings.get_hub_host()
    monkeypatch.setattr(settings, "port_scan_docker_enabled", False, raising=False)
    with patch('app.port_allocator.docker_scanner.get_used_host_ports', return_value={9000}) as mocked:
        alloc, _ = await allocate_ports_for_app(async_session, "flag-app", hub_host, settings)
    assert alloc.api_port == 9000
    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_allocate_ports_scan_failure_falls_back(async_session):
    """Échec du scan : on retombe sur le comportement DB seul (gracieux)."""
    hub_host = settings.get_hub_host()
    with patch('app.port_allocator.docker_scanner.get_used_host_ports', side_effect=RuntimeError("docker down")):
        alloc, _ = await allocate_ports_for_app(async_session, "fail-app", hub_host, settings)
    assert alloc.api_port == 9000


@pytest.mark.asyncio
async def test_ports_status_exposes_host_occupied_ports(client: AsyncClient, admin_user):
    """Le statut admin expose les ports Docker occupés dans les plages."""
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    with patch('app.port_allocator.docker_scanner.get_used_host_ports', return_value={9100, 4300, 22}):
        response = await client.get("/api/apps/admin/ports/status", headers=get_auth_header(token))
    assert response.status_code == 200
    body = response.json()
    assert 9100 in body["host_occupied_ports"]
    assert 4300 in body["host_occupied_ports"]
    assert 22 not in body["host_occupied_ports"]   # hors plages -> ignoré


# ===== Exclusion des sous-réseaux Docker occupés (scan hôte local) =====
# Bug constaté : le Hub n'allouait que selon sa table port_allocations et a
# proposé 10.50.5.0/24 alors que exemple-app-network l'occupe déjà (épinglage
# manuel). Les sous-réseaux Docker réellement présents doivent être exclus,
# comme les ports le sont via get_used_host_ports.


def test_get_used_network_subnets_empty_when_unavailable():
    """get_used_network_subnets est best-effort : set() si Docker indisponible."""
    s = DockerScanner()
    s._available = False
    s._client = False
    assert s.get_used_network_subnets() == set()


@pytest.mark.asyncio
async def test_allocate_ports_excludes_docker_occupied_subnets(async_session):
    """Un sous-réseau Docker réellement occupé est exclu de l'attribution."""
    hub_host = settings.get_hub_host()
    with patch('app.port_allocator.docker_scanner.get_used_network_subnets',
               return_value={"10.50.0.0/24", "172.17.0.0/16"}):
        alloc, reused = await allocate_ports_for_app(async_session, "subnet-app", hub_host, settings)
    assert reused is False
    assert alloc.subnet == "10.50.1.0/24"   # 10.50.0.0/24 occupé par Docker -> sauté


@pytest.mark.asyncio
async def test_allocate_ports_subnet_scan_not_applied_for_remote_host(async_session):
    """Le scan ne reflète que l'hôte local : ignoré pour un host distant."""
    remote = "10.20.30.40"
    assert remote != settings.get_hub_host()
    with patch('app.port_allocator.docker_scanner.get_used_network_subnets',
               return_value={"10.50.0.0/24"}) as mocked:
        alloc, _ = await allocate_ports_for_app(async_session, "subnet-remote-app", remote, settings)
    assert alloc.subnet == "10.50.0.0/24"
    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_allocate_ports_subnet_scan_disabled_by_flag(async_session, monkeypatch):
    """Feature flag désactivé : aucun scan de sous-réseaux n'est déclenché."""
    hub_host = settings.get_hub_host()
    monkeypatch.setattr(settings, "subnet_scan_docker_enabled", False, raising=False)
    with patch('app.port_allocator.docker_scanner.get_used_network_subnets',
               return_value={"10.50.0.0/24"}) as mocked:
        alloc, _ = await allocate_ports_for_app(async_session, "subnet-flag-app", hub_host, settings)
    assert alloc.subnet == "10.50.0.0/24"
    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_allocate_ports_subnet_scan_failure_falls_back(async_session):
    """Échec du scan : on retombe sur le comportement DB seul (gracieux)."""
    hub_host = settings.get_hub_host()
    with patch('app.port_allocator.docker_scanner.get_used_network_subnets',
               side_effect=RuntimeError("docker down")):
        alloc, _ = await allocate_ports_for_app(async_session, "subnet-fail-app", hub_host, settings)
    assert alloc.subnet == "10.50.0.0/24"


@pytest.mark.asyncio
async def test_backfill_subnet_excludes_docker_occupied_subnets(async_session):
    """Le remplissage paresseux d'une allocation ancienne (sans subnet) évite
    aussi les sous-réseaux Docker occupés."""
    hub_host = settings.get_hub_host()
    async_session.add(PortAllocation(
        app_slug="legacy-app", host=hub_host,
        api_port=9000, frontend_port=4200, frontend_https_port=4201,
    ))
    await async_session.commit()

    with patch('app.port_allocator.docker_scanner.get_used_network_subnets',
               return_value={"10.50.0.0/24"}):
        alloc, reused = await allocate_ports_for_app(async_session, "legacy-app", hub_host, settings)
    assert reused is True
    assert alloc.subnet == "10.50.1.0/24"


# ===== Libération des allocations (apps supprimées / nettoyage) =====

from app.port_allocator import release_ports_for_app


@pytest.mark.asyncio
async def test_release_ports_for_app_deletes_rows(async_session):
    """release_ports_for_app supprime les allocations d'un slug, pas des autres."""
    async_session.add(PortAllocation(app_slug="gone", host="h1", api_port=9000, frontend_port=4200, frontend_https_port=4201))
    async_session.add(PortAllocation(app_slug="gone", host="h2", api_port=9001, frontend_port=4202, frontend_https_port=4203))
    async_session.add(PortAllocation(app_slug="stay", host="h1", api_port=9002, frontend_port=4204, frontend_https_port=4205))
    await async_session.commit()

    removed = await release_ports_for_app(async_session, "gone")
    await async_session.commit()

    assert removed == 2
    remaining = (await async_session.execute(select(PortAllocation))).scalars().all()
    assert {r.app_slug for r in remaining} == {"stay"}


@pytest.mark.asyncio
async def test_deleting_app_releases_its_ports(client: AsyncClient, admin_user, test_app, async_session):
    """Supprimer une application libère ses ports dans le pool."""
    async_session.add(PortAllocation(
        app_slug=test_app.slug, host="172.23.0.3",
        api_port=9000, frontend_port=4200, frontend_https_port=4201,
    ))
    await async_session.commit()

    token, _, _ = create_access_token(username=admin_user.username, apps={})
    resp = await client.delete(f"/api/apps/{test_app.id}", headers=get_auth_header(token))
    assert resp.status_code == 204

    rows = (await async_session.execute(
        select(PortAllocation).where(PortAllocation.app_slug == test_app.slug)
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_delete_allocation_requires_admin(client: AsyncClient):
    response = await client.delete("/api/apps/admin/ports/allocations/1")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_allocation_endpoint(client: AsyncClient, admin_user, async_session):
    """Un admin peut supprimer manuellement une allocation orpheline."""
    alloc = PortAllocation(app_slug="orphan", host="172.23.0.3", api_port=9000, frontend_port=4200, frontend_https_port=4201)
    async_session.add(alloc)
    await async_session.commit()
    await async_session.refresh(alloc)

    token, _, _ = create_access_token(username=admin_user.username, apps={})
    resp = await client.delete(f"/api/apps/admin/ports/allocations/{alloc.id}", headers=get_auth_header(token))
    assert resp.status_code == 204

    rows = (await async_session.execute(select(PortAllocation))).scalars().all()
    assert rows == []

    # Suppression d'un id inexistant -> 404
    missing = await client.delete("/api/apps/admin/ports/allocations/99999", headers=get_auth_header(token))
    assert missing.status_code == 404


# ===== Réservations TTL (fuite de ports du prepare catalogue — 2026-10-05) =====
# Une allocation sans application associée est une RÉSERVATION (prepare du
# catalogue GitHub, /allocate-ports orphelin). Elle expire après
# PORT_RESERVATION_TTL_MINUTES : ports/sous-réseau redeviennent attribuables
# et la ligne est purgée paresseusement à l'allocation suivante. N'expirent
# JAMAIS : les allocations adossées à une app existante, ni les lignes legacy
# sans allocated_at, ni (TTL=0) — escape hatch ops.

REMOTE_TEST_HOST = "10.20.30.40"  # ≠ hub host : les scans Docker locaux sont ignorés

from datetime import timedelta

from app.models import Application, utcnow

TTL = 30  # minutes — valeur du test, posée explicitement via monkeypatch


def _alloc_row(slug, host, api_port, fe_port, allocated_at="__default__"):
    kwargs = dict(
        app_slug=slug, host=host,
        api_port=api_port, frontend_port=fe_port, frontend_https_port=fe_port + 1,
    )
    if allocated_at != "__default__":
        kwargs["allocated_at"] = allocated_at
    return PortAllocation(**kwargs)


@pytest.fixture
def ttl_settings(monkeypatch):
    """Settings avec un TTL explicite (indépendant du .env local)."""
    monkeypatch.setattr(settings, "port_reservation_ttl_minutes", TTL, raising=False)
    return settings


@pytest.mark.asyncio
async def test_expired_reservation_frees_ports_and_is_swept(async_session, ttl_settings):
    """Réservation prepare (> TTL) sans app : ports réattribuables + purge."""
    hub_host = REMOTE_TEST_HOST  # host distant : scan Docker ignoré
    old = utcnow() - timedelta(minutes=TTL + 10)
    async_session.add(_alloc_row("ghost", hub_host, 9000, 4200, allocated_at=old))
    await async_session.commit()

    alloc, reused = await allocate_ports_for_app(async_session, "new-app", hub_host, settings)
    assert reused is False
    assert (alloc.api_port, alloc.frontend_port) == (9000, 4200)  # ports libérés

    ghosts = (await async_session.execute(
        select(PortAllocation).where(PortAllocation.app_slug == "ghost")
    )).scalars().all()
    assert ghosts == []  # réservation expirée purgée


@pytest.mark.asyncio
async def test_fresh_reservation_still_blocks_ports(async_session, ttl_settings):
    """Réservation récente (sans app) : les ports restent bloqués pour les autres."""
    hub_host = REMOTE_TEST_HOST  # host distant : scan Docker ignoré
    fresh = utcnow() - timedelta(minutes=5)
    async_session.add(_alloc_row("hold", hub_host, 9000, 4200, allocated_at=fresh))
    await async_session.commit()

    alloc, _ = await allocate_ports_for_app(async_session, "other", hub_host, settings)
    assert alloc.api_port == 9001  # 9000 encore réservé


@pytest.mark.asyncio
async def test_app_backed_allocation_never_expires(async_session, ttl_settings):
    """Une allocation adossée à une app existante n'expire jamais."""
    hub_host = REMOTE_TEST_HOST  # host distant : scan Docker ignoré
    ancient = utcnow() - timedelta(days=30)
    async_session.add(Application(
        name="Stable", slug="stable-app", api_url=f"http://{hub_host}:9000",
    ))
    async_session.add(_alloc_row("stable-app", hub_host, 9000, 4200, allocated_at=ancient))
    await async_session.commit()

    alloc, _ = await allocate_ports_for_app(async_session, "other-app", hub_host, settings)
    assert alloc.api_port == 9001  # ports de stable-app toujours occupés

    stable = (await async_session.execute(
        select(PortAllocation).where(PortAllocation.app_slug == "stable-app")
    )).scalars().all()
    assert len(stable) == 1  # pas purgée par le sweep


@pytest.mark.asyncio
async def test_ttl_zero_disables_expiry(async_session, monkeypatch):
    """TTL=0 : comportement historique (aucune expiration)."""
    monkeypatch.setattr(settings, "port_reservation_ttl_minutes", 0, raising=False)
    hub_host = REMOTE_TEST_HOST  # host distant : scan Docker ignoré
    ancient = utcnow() - timedelta(days=365)
    async_session.add(_alloc_row("ancient", hub_host, 9000, 4200, allocated_at=ancient))
    await async_session.commit()

    alloc, _ = await allocate_ports_for_app(async_session, "ttl0-app", hub_host, settings)
    assert alloc.api_port == 9001  # réservation ancienne toujours bloquante

    rows = (await async_session.execute(
        select(PortAllocation).where(PortAllocation.app_slug == "ancient")
    )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_legacy_row_without_timestamp_never_expires(async_session, ttl_settings):
    """Ligne legacy (allocated_at NULL, hors modèle) : conservée, ports bloqués."""
    hub_host = REMOTE_TEST_HOST  # host distant : scan Docker ignoré
    async_session.add(_alloc_row("legacy", hub_host, 9000, 4200, allocated_at=None))
    await async_session.commit()

    alloc, _ = await allocate_ports_for_app(async_session, "legacy-other", hub_host, settings)
    assert alloc.api_port == 9001

    rows = (await async_session.execute(
        select(PortAllocation).where(PortAllocation.app_slug == "legacy")
    )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_expired_reservation_same_slug_gets_clean_realloc(async_session, ttl_settings):
    """Même slug après expiration : la ligne est recréée proprement (une seule)."""
    hub_host = REMOTE_TEST_HOST  # host distant : scan Docker ignoré
    old = utcnow() - timedelta(minutes=TTL + 10)
    async_session.add(_alloc_row("back-later", hub_host, 9000, 4200, allocated_at=old))
    await async_session.commit()

    alloc, reused = await allocate_ports_for_app(async_session, "back-later", hub_host, settings)
    assert reused is False
    assert (alloc.api_port, alloc.frontend_port) == (9000, 4200)

    rows = (await async_session.execute(
        select(PortAllocation).where(PortAllocation.app_slug == "back-later")
    )).scalars().all()
    assert len(rows) == 1
