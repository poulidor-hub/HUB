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


from app.auth import create_access_token, get_admin_or_admin_sa


def _sa_token(sa):
    token, _, _ = create_access_token(
        username=sa.client_id,
        apps={a.slug: "service" for a in sa.applications},
        is_service_account=True,
    )
    return token


@pytest.mark.asyncio
async def test_dep_sa_without_flag_403(service_account, async_session):
    with pytest.raises(Exception) as e:
        await get_admin_or_admin_sa(token=_sa_token(service_account), db=async_session)
    assert getattr(e.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_dep_sa_with_flag_ok(service_account, async_session):
    service_account.can_admin_read = True
    await async_session.commit()
    entity = await get_admin_or_admin_sa(token=_sa_token(service_account), db=async_session)
    assert entity is service_account


@pytest.mark.asyncio
async def test_dep_hot_revocation(service_account, async_session):
    service_account.can_admin_read = True
    await async_session.commit()
    token = _sa_token(service_account)
    assert await get_admin_or_admin_sa(token=token, db=async_session)
    service_account.can_admin_read = False
    await async_session.commit()
    with pytest.raises(Exception) as e:
        await get_admin_or_admin_sa(token=token, db=async_session)
    assert e.value.status_code == 403
