# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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


@pytest.mark.asyncio
async def test_sync_report_updates_existing_row(client: AsyncClient, test_app, service_account, async_session):
    dep = AppDependency(source_slug=test_app.slug, target_slug="exemple-app-2")
    async_session.add(dep)
    await async_session.commit()
    assert dep.last_sync_at is None

    r = await client.post(
        "/api/apps/sync-report",
        json={"target": "exemple-app-2"},
        headers={**_sa_headers(service_account), "X-Slug": test_app.slug},
    )
    assert r.status_code == 200

    await async_session.refresh(dep)
    assert dep.last_sync_at is not None


@pytest.mark.asyncio
async def test_sync_report_creates_orphan_row_if_missing(client: AsyncClient, test_app, service_account, async_session):
    r = await client.post(
        "/api/apps/sync-report",
        json={"target": "FutureApp"},
        headers={**_sa_headers(service_account), "X-Slug": test_app.slug},
    )
    assert r.status_code == 200

    from sqlalchemy import select
    rows = (await async_session.execute(
        select(AppDependency).where(
            AppDependency.source_slug == test_app.slug,
            AppDependency.target_slug == "FutureApp",
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].last_sync_at is not None
    assert rows[0].note is None


@pytest.mark.asyncio
async def test_sync_report_rejects_non_service_account(client: AsyncClient, test_user):
    from app.auth import create_access_token
    token, _, _ = create_access_token(username=test_user.username, apps={})
    r = await client.post(
        "/api/apps/sync-report",
        json={"target": "X"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def _admin_headers(admin_user) -> dict:
    from app.auth import create_access_token
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_dependencies_requires_admin(client: AsyncClient):
    r = await client.get("/api/apps/dependencies")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_get_dependencies_returns_full_graph(client: AsyncClient, admin_user, async_session):
    async_session.add_all([
        AppDependency(source_slug="A", target_slug="B", note="x"),
        AppDependency(source_slug="A", target_slug="C", last_sync_at=utcnow()),
    ])
    await async_session.commit()

    r = await client.get("/api/apps/dependencies", headers=_admin_headers(admin_user))
    assert r.status_code == 200
    body = r.json()
    pairs = {(d["source"], d["target"]) for d in body["dependencies"]}
    assert pairs == {("A", "B"), ("A", "C")}
    by_pair = {(d["source"], d["target"]): d for d in body["dependencies"]}
    assert by_pair[("A", "C")]["last_sync_at"] is not None
    assert by_pair[("A", "B")]["last_sync_at"] is None
