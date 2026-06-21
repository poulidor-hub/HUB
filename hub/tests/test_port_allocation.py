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
