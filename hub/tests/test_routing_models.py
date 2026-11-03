"""Chantier B Task 1 — modèle de routage : dns_name + RoutedService + backfill.

Le backfill dns_name=slug (idempotent) est appelé au lifespan (pattern
_backfill_user_uuids) ; le conftest redirige async_session_maker vers la
SQLite de test.
"""

import pytest
from sqlalchemy import select

from app.database import backfill_dns_names
from app.models import Application, RoutedService


def _make_app(slug: str, dns_name: str | None = None) -> Application:
    return Application(
        name=slug,
        slug=slug,
        api_url=f"http://192.0.2.10:9000",
        frontend_url=f"http://192.0.2.10:4200",
        dns_name=dns_name,
    )


def test_routed_service_table_exists():
    assert RoutedService.__tablename__ == "routed_service"
    cols = {c.name for c in RoutedService.__table__.columns}
    assert {"id", "name", "dns_name", "target_url", "is_enabled", "created_at"} <= cols


def test_application_has_dns_name_nullable():
    col = Application.__table__.c.dns_name
    assert col.nullable is True
    assert "dns_name" in {c.name for c in Application.__table__.columns}


@pytest.mark.asyncio
async def test_backfill_dns_names_fills_slugs_and_is_idempotent(async_session):
    app_a = _make_app("exemple-app")  # sans dns_name → backfillé avec son slug
    app_b = _make_app("exemple-app", dns_name="ls-custom")  # déjà posé → inchangé
    async_session.add_all([app_a, app_b])
    await async_session.commit()

    changed = await backfill_dns_names()
    assert changed >= 1

    async_session.expire_all()  # backfill = autre session → invalider le cache d'identité
    rows = (await async_session.execute(select(Application))).scalars().all()
    by_slug = {a.slug: a.dns_name for a in rows}
    assert by_slug["exemple-app"] == "exemple-app"
    assert by_slug["exemple-app"] == "ls-custom"

    # Idempotence : deuxième appel ne modifie rien
    assert await backfill_dns_names() == 0
