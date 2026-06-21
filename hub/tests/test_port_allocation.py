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
