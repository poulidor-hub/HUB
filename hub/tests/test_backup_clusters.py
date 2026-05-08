"""Tests des clusters de sauvegarde."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, BackupCluster


@pytest.mark.asyncio
async def test_admin_can_create_list_and_update_backup_cluster(
    client: AsyncClient,
    async_session: AsyncSession,
    admin_token: str,
    test_app: Application,
):
    headers = {"Authorization": f"Bearer {admin_token}"}

    create_response = await client.post(
        "/api/admin/backup-clusters/",
        headers=headers,
        json={
            "name": "Cluster Production",
            "slug": "production",
            "description": "Sauvegardes critiques",
            "color": "#198754",
            "app_ids": [test_app.id],
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "Cluster Production"
    assert created["apps_count"] == 1
    assert created["apps"][0]["app_slug"] == test_app.slug
    assert created["apps"][0]["is_system_app"] is False

    list_response = await client.get("/api/admin/backup-clusters/", headers=headers)
    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["total"] == 1
    assert listed["clusters"][0]["slug"] == "production"

    config_response = await client.put(
        f"/api/admin/backup-clusters/{created['id']}/backup/config",
        headers=headers,
        json={
            "backup_mode": "scheduled",
            "backup_cron": "0 2 * * *",
            "backup_retention": 3,
            "backup_quiesce": True,
        },
    )

    assert config_response.status_code == 200
    configured = config_response.json()
    assert configured["scheduled_apps"] == 1
    assert configured["apps"][0]["backup_mode"] == "scheduled"
    assert configured["apps"][0]["backup_cron"] == "0 2 * * *"

    await async_session.refresh(test_app)
    assert test_app.backup_mode == "scheduled"
    assert test_app.backup_cron == "0 2 * * *"
    assert test_app.backup_retention == 3
    assert test_app.backup_quiesce is True
    assert test_app.next_backup_at is not None


@pytest.mark.asyncio
async def test_backup_cluster_requires_existing_apps(
    client: AsyncClient,
    admin_token: str,
):
    response = await client.post(
        "/api/admin/backup-clusters/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Cluster Invalide",
            "slug": "cluster-invalide",
            "app_ids": [9999],
        },
    )

    assert response.status_code == 400
    assert "Applications introuvables" in response.json()["detail"]


@pytest.mark.asyncio
async def test_admin_can_delete_backup_cluster(
    client: AsyncClient,
    async_session: AsyncSession,
    admin_token: str,
):
    cluster = BackupCluster(name="Temporaire", slug="temporaire")
    async_session.add(cluster)
    await async_session.commit()
    await async_session.refresh(cluster)

    response = await client.delete(
        f"/api/admin/backup-clusters/{cluster.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    result = await async_session.execute(
        select(BackupCluster).where(BackupCluster.id == cluster.id)
    )
    assert result.scalar_one_or_none() is None
