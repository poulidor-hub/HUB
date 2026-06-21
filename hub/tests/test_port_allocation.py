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
