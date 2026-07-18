import pytest
from sqlalchemy import select
from app.models import AppDependency, utcnow


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
