"""Tests pour le garde M2M SA/slug et le mode d'application (compat/enforced)."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from app.models import Application, ServiceAccount, AuditLog
from app.auth import create_access_token


def _sa_headers_for_sa(service_account, slug_override=None):
    token, _, _ = create_access_token(
        username=service_account.client_id,
        apps={app.slug: "service" for app in service_account.applications},
        is_service_account=True,
    )
    headers = {"Authorization": f"Bearer {token}"}
    if slug_override:
        headers["X-Slug"] = slug_override
    return headers


@pytest.mark.asyncio
async def test_owned_x_slug_always_accepted(client: AsyncClient, test_app, service_account, async_session):
    """Un SA qui revendique un slug qui lui appartient est toujours accepte."""
    r = await client.post(
        "/api/apps/dependencies",
        json={"depends_on": []},
        headers=_sa_headers_for_sa(service_account, slug_override=test_app.slug),
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_unowned_x_slug_allowed_and_audited_in_compat(
    client: AsyncClient, test_app, other_app, service_account, async_session
):
    """En mode compat (defaut), un X-Slug non-possede est accepte avec audit."""
    other_app.m2m_enforcement_mode = "compat"
    await async_session.flush()

    r = await client.post(
        "/api/apps/dependencies",
        json={"depends_on": []},
        headers=_sa_headers_for_sa(service_account, slug_override=other_app.slug),
    )
    assert r.status_code == 200

    audit = await async_session.execute(
        select(AuditLog).where(AuditLog.action == "m2m_slug_mismatch_compat")
    )
    assert audit.scalar_one_or_none() is not None, "un audit doit etre emis en compat"


@pytest.mark.asyncio
async def test_unowned_x_slug_rejected_in_enforced_mode(
    client: AsyncClient, test_app, other_app, service_account, async_session
):
    """En mode enforced, un X-Slug non-possede est refuse (403)."""
    other_app.m2m_enforcement_mode = "enforced"
    await async_session.flush()

    r = await client.post(
        "/api/apps/dependencies",
        json={"depends_on": []},
        headers=_sa_headers_for_sa(service_account, slug_override=other_app.slug),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_missing_x_slug_falls_back_to_first_owned_app(
    client: AsyncClient, test_app, service_account
):
    """Sans X-Slug, le premier slug du SA est utilise implicitement."""
    r = await client.post(
        "/api/apps/dependencies",
        json={"depends_on": []},
        headers=_sa_headers_for_sa(service_account),
    )
    assert r.status_code == 200
