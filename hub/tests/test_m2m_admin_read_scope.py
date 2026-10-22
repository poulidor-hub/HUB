"""Reads M2M admin (phase 3) : flag can_admin_read, dépendance, endpoints ouverts."""
import pytest
from httpx import AsyncClient
from app.models import ServiceAccount


@pytest.mark.asyncio
async def test_sa_can_admin_read_default_false(async_session):
    sa = ServiceAccount(name="SA", client_id="sa_defaultflag",
                        client_secret_hash="x", is_active=True)
    async_session.add(sa)
    await async_session.commit()
    await async_session.refresh(sa)
    assert sa.can_admin_read is False


@pytest.mark.asyncio
async def test_sa_can_admin_read_persist_true(async_session):
    sa = ServiceAccount(name="SA2", client_id="sa_flagged",
                        client_secret_hash="x", is_active=True, can_admin_read=True)
    async_session.add(sa)
    await async_session.commit()
    await async_session.refresh(sa)
    from app.m2m_scope import sa_can_admin_read
    assert sa_can_admin_read(sa) is True
