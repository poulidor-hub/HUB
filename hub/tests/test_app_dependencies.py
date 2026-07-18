import pytest
from httpx import AsyncClient
from sqlalchemy import select
from app.models import AppDependency, utcnow, ServiceAccount
from app.auth import create_access_token


def _sa_headers(service_account) -> dict:
    """Mint un token service account pour les tests."""
    token, _, _ = create_access_token(
        username=service_account.client_id,
        apps={service_account.applications[0].slug: "service"} if service_account.applications else {},
        is_service_account=True,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_app_dependency_model_persists(async_session):
    dep = AppDependency(
        source_slug="exemple-apps",
        target_slug="exemple-app-2",
        note="Catalogue articles",
    )
    async_session.add(dep)
    await async_session.commit()

    result = await async_session.execute(
        select(AppDependency).where(AppDependency.source_slug == "exemple-apps")
    )
    row = result.scalar_one()
    assert row.target_slug == "exemple-app-2"
    assert row.note == "Catalogue articles"
    assert row.declared_at is not None
    assert row.last_sync_at is None


@pytest.mark.asyncio
async def test_post_dependencies_rejects_non_service_account(client: AsyncClient, test_user):
    """Un user JWT (non SA) reçoit 403."""
    from app.auth import create_access_token
    token, _, _ = create_access_token(username=test_user.username, apps={})
    r = await client.post(
        "/api/apps/dependencies",
        json={"depends_on": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_post_dependencies_creates_rows(client: AsyncClient, test_app, service_account, async_session):
    r = await client.post(
        "/api/apps/dependencies",
        json={"depends_on": [
            {"slug": "exemple-app-2", "note": "articles"},
            {"slug": "exemple-app"},
        ]},
        headers={**_sa_headers(service_account), "X-Slug": test_app.slug},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] == 2

    # Vérifier en DB
    rows = (await async_session.execute(
        select(AppDependency).where(AppDependency.source_slug == test_app.slug)
    )).scalars().all()
    targets = {r.target_slug: r for r in rows}
    assert set(targets.keys()) == {"exemple-app-2", "exemple-app"}
    assert targets["exemple-app-2"].note == "articles"
    assert targets["exemple-app"].note is None


@pytest.mark.asyncio
async def test_post_dependencies_replaces_existing_and_preserves_last_sync(
    client: AsyncClient, test_app, service_account, async_session
):
    # Pré-existant avec last_sync_at défini
    from datetime import timedelta
    old_sync = utcnow() - timedelta(hours=2)
    existing = AppDependency(
        source_slug=test_app.slug, target_slug="exemple-app-2",
        note="old note", last_sync_at=old_sync,
    )
    async_session.add(existing)
    await async_session.commit()

    # Remplace par une nouvelle liste qui contient encore exemple-app-2
    r = await client.post(
        "/api/apps/dependencies",
        json={"depends_on": [{"slug": "exemple-app-2", "note": "new note"}]},
        headers={**_sa_headers(service_account), "X-Slug": test_app.slug},
    )
    assert r.status_code == 200

    # Vérifier : 1 seule ligne (remplacement), note mise à jour, last_sync_at préservé
    rows = (await async_session.execute(
        select(AppDependency).where(AppDependency.source_slug == test_app.slug)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].note == "new note"
    assert rows[0].last_sync_at == old_sync  # préservé !


@pytest.mark.asyncio
async def test_post_dependencies_clears_when_empty_list(client: AsyncClient, test_app, service_account, async_session):
    # Pré-existant
    async_session.add(AppDependency(source_slug=test_app.slug, target_slug="X"))
    await async_session.commit()

    r = await client.post(
        "/api/apps/dependencies",
        json={"depends_on": []},
        headers={**_sa_headers(service_account), "X-Slug": test_app.slug},
    )
    assert r.status_code == 200
    assert r.json()["stored"] == 0

    rows = (await async_session.execute(
        select(AppDependency).where(AppDependency.source_slug == test_app.slug)
    )).scalars().all()
    assert rows == []
