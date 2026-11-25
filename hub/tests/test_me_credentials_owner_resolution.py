# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
AN-1 (banc 2026-09-10) — `/api/apps/me/credentials` doit résoudre l'app
propriétaire du service account, jamais un élément arbitraire de la
relation N-N non ordonnée (`sa.applications[0]`).

Repro banc : le SA de `controle-socle` (« SA - Contrôle Socle ») a accès à
13 apps ; l'endpoint retournait `app_slug: exemple-app-8-ws` (app
arbitraire) au lieu de l'app propriétaire du SA. L'identité d'app étant
falsifiée, le risque est la rotation/le secret livrés au mauvais slug.

Contrat : l'app retournée est celle DONT le SA est propriétaire
(convention existante `_is_owner_service_account` : nom du SA contenant le
slug/nom de l'app). Pour un SA mono-app sans convention de nom, l'unique
app liée est retournée. Pour un SA multi-apps sans propriétaire
identifiable, l'endpoint refuse (409) au lieu de livrer une app arbitraire.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch

from app.auth import create_access_token
from app.models import Application, ServiceAccount, service_account_app_access


async def _create_app(
    session: AsyncSession,
    slug: str,
    name: str,
    *,
    is_shared: bool = False,
    is_workshop: bool = False,
) -> Application:
    app = Application(
        name=name,
        slug=slug,
        api_url=f"http://192.0.2.10:9100",
        is_active=True,
        approval_status="approved",
        is_shared_service=is_shared,
        is_workshop=is_workshop,
    )
    session.add(app)
    await session.commit()
    await session.refresh(app)
    return app


async def _link(session: AsyncSession, sa: ServiceAccount, app: Application) -> None:
    await session.execute(
        service_account_app_access.insert().values(
            service_account_id=sa.id, app_id=app.id
        )
    )
    await session.commit()


def _m2m_token(sa: ServiceAccount, apps: dict) -> str:
    token, _, _ = create_access_token(
        username=sa.client_id, apps=apps, is_service_account=True
    )
    return token


class TestMeCredentialsResolvesOwnerApp:
    @pytest.mark.asyncio
    async def test_multi_app_sa_returns_owner_app(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """Repro banc : SA multi-apps → l'app retournée est celle dont il est
        propriétaire (par nommage), pas le premier élément arbitraire."""
        # L'app « autre » est liée EN PREMIER : avec le bug, c'est elle que
        # `sa.applications[0]` retournait (ordre d'insertion = rowid sqlite).
        other_app = await _create_app(
            async_session, "exemple-app-8-ws", "Exemple App 8",
            is_shared=True, is_workshop=True,
        )
        my_app = await _create_app(async_session, "controle-socle", "Controle Socle")

        sa = ServiceAccount(
            name="SA - Controle Socle",
            client_id="sa_bench_multi_app",
            client_secret_hash="x",
            description="Service account auto-créé pour Controle Socle",
            is_active=True,
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)
        await _link(async_session, sa, other_app)  # insérée en premier
        await _link(async_session, sa, my_app)

        token = _m2m_token(sa, {
            "exemple-app-8-ws": "service",
            "controle-socle": "service",
        })
        response = await client.get(
            "/api/apps/me/credentials",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["app_slug"] == "controle-socle", (
            f"AN-1 non corrigé : app arbitraire retournée ({data['app_slug']})"
        )
        assert data["app_name"] == "Controle Socle"
        assert data["client_id"] == "sa_bench_multi_app"

    @pytest.mark.asyncio
    async def test_multi_app_sa_without_identifiable_owner_is_refused(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """SA multi-apps sans propriétaire identifiable → refus (pas d'app
        arbitraire livrée)."""
        app_a = await _create_app(async_session, "app-a", "App A", is_shared=True)
        app_b = await _create_app(async_session, "app-b", "App B", is_shared=True)

        sa = ServiceAccount(
            name="SA partagé manuel",  # ne contient aucun slug/nom d'app
            client_id="sa_manual_multi",
            client_secret_hash="x",
            is_active=True,
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)
        await _link(async_session, sa, app_a)
        await _link(async_session, sa, app_b)

        token = _m2m_token(sa, {"app-a": "service", "app-b": "service"})
        response = await client.get(
            "/api/apps/me/credentials",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 409, (
            f"Attendu 409 (ambiguïté), reçu {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    async def test_single_app_sa_without_naming_convention_returns_its_app(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """SA mono-app sans convention de nommage → son unique app est
        retournée (déterministe, pas d'ambiguïté)."""
        app = await _create_app(async_session, "solo-app", "Solo App")
        sa = ServiceAccount(
            name="SA custom",
            client_id="sa_solo_custom",
            client_secret_hash="x",
            is_active=True,
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)
        await _link(async_session, sa, app)

        token = _m2m_token(sa, {"solo-app": "service"})
        response = await client.get(
            "/api/apps/me/credentials",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["app_slug"] == "solo-app"

    @pytest.mark.asyncio
    async def test_deprecated_client_id_path_also_resolves_owner_app(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """Path déprécié (IP Docker + X-Client-ID) : même contrat que le path
        token (la ligne 2368 avait le même bug `[0]`)."""
        other_app = await _create_app(
            async_session, "exemple-app-8-ws", "Exemple App 8",
            is_shared=True, is_workshop=True,
        )
        my_app = await _create_app(async_session, "controle-socle", "Controle Socle")

        sa = ServiceAccount(
            name="SA - Controle Socle",
            client_id="sa_bench_deprecated",
            client_secret_hash="x",
            is_active=True,
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)
        await _link(async_session, sa, other_app)
        await _link(async_session, sa, my_app)

        with patch("app.routes.apps.is_docker_network_ip", return_value=True):
            response = await client.get(
                "/api/apps/me/credentials",
                headers={"X-Client-ID": "sa_bench_deprecated"},
            )

        assert response.status_code == 200, response.text
        assert response.json()["app_slug"] == "controle-socle"
