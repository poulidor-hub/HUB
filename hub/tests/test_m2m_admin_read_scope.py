"""Reads M2M admin (phase 3) : flag can_admin_read, dépendance, endpoints ouverts."""
import pytest
from httpx import AsyncClient
from app.models import ServiceAccount, User


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


from app.auth import create_access_token, get_admin_or_admin_sa, get_password_hash


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


@pytest.fixture
async def user_token_non_admin(async_session) -> str:
    """Token d'un user non-admin (doit rester 403 sur les routes admin-read)."""
    user = User(
        username="nonadmin",
        email="nonadmin@example.com",
        hashed_password=get_password_hash("nonadmin123"),
        is_active=True,
        is_admin=False
    )
    async_session.add(user)
    await async_session.commit()
    await async_session.refresh(user)
    token, _, _ = create_access_token(
        username=user.username,
        apps={}
    )
    return token


@pytest.mark.asyncio
async def test_admin_read_routes_param_sweep(client, admin_token, admin_user,
                                             user_token_non_admin, service_account,
                                             async_session, test_app):
    """10 routes : admin 200, SA flaggé 200, SA sans flag 403, user non-admin 403."""
    service_account.can_admin_read = True
    await async_session.commit()
    paths = [
        f"/api/apps/{test_app.id}",
        "/api/users/",
        f"/api/users/{admin_user.id}",
        "/api/messages/",
        "/api/messages/stats",
        "/api/admin/backups/stats",
        f"/api/apps/{test_app.slug}/backup/config",
        "/api/audit/stats",
        "/api/audit/sessions/active",
        "/api/audit/errors",
    ]
    sa_headers = {"Authorization": f"Bearer {_sa_token(service_account)}"}
    for path in paths:
        assert (await client.get(path, headers={"Authorization": f"Bearer {admin_token}"})).status_code == 200, path
        assert (await client.get(path, headers=sa_headers)).status_code == 200, path
        service_account.can_admin_read = False
        await async_session.commit()
        assert (await client.get(path, headers=sa_headers)).status_code == 403, path
        service_account.can_admin_read = True
        await async_session.commit()
        assert (await client.get(path, headers={"Authorization": f"Bearer {user_token_non_admin}"})).status_code == 403, path


@pytest.mark.asyncio
async def test_apps_list_sa_flagged_full_list(client, service_account, async_session):
    service_account.can_admin_read = True
    await async_session.commit()
    r = await client.get("/api/apps/", headers={"Authorization": f"Bearer {_sa_token(service_account)}"})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and len(body) >= 1
    assert all("slug" in app for app in body)


@pytest.mark.asyncio
async def test_apps_list_sa_unflagged_403(client, service_account, async_session):
    r = await client.get("/api/apps/", headers={"Authorization": f"Bearer {_sa_token(service_account)}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_audit_logs_sa_flagged_full(client, service_account, async_session):
    service_account.can_admin_read = True
    await async_session.commit()
    r = await client.get("/api/audit/logs", headers={"Authorization": f"Bearer {_sa_token(service_account)}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_audit_logs_non_admin_cannot_read_others(client, user_token_non_admin):
    r = await client.get("/api/audit/logs?user_id=999",
                         headers={"Authorization": f"Bearer {user_token_non_admin}"})
    assert r.status_code == 403
