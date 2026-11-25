# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
AN-2 (banc 2026-09-10) — Périmètre M2M des service accounts (Option B validée) :

1. Parité de contexte atelier : un SA ne reçoit les apps partagées
   (`is_shared_service=True`) que du MÊME contexte que lui (prod ↔ prod,
   atelier ↔ atelier). Harmonisation de tous les sites de copie via le
   helper central `m2m_scope.grant_shared_apps_to_sa`.
2. Filtrage du claim JWT `apps` : seules les apps `is_active` et
   `approval_status=approved` y figurent (plus de fuite via lignes N-N
   obsolètes : app désactivée/déapprouvée/atelier).
3. Purge one-shot : `m2m_scope.purge_workshop_leaks` retire les liens N-N
   croisés atelier↔prod existants, sans toucher aux liens same-context ni
   aux SA ambigus (propriétaire non identifiable).

Repro banc : le SA de `controle-socle` (prod) portait 13 apps dont
l'atelier `exemple-app-8-ws` (rôle service) — compromission de l'atelier
en cas de fuite du secret prod.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, verify_token, get_password_hash
from app.models import (
    Application,
    ServiceAccount,
    User,
    ApprovalStatus,
    service_account_app_access,
)
from tests.conftest import get_auth_header


async def _make_app(
    session: AsyncSession,
    slug: str,
    name: str,
    *,
    is_shared: bool = False,
    is_workshop: bool = False,
    approved: bool = True,
    active: bool = True,
) -> Application:
    app = Application(
        name=name,
        slug=slug,
        api_url=f"http://192.0.2.10:9300",
        is_active=active,
        approval_status=ApprovalStatus.approved if approved else ApprovalStatus.pending,
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


async def _sa_app_ids(session: AsyncSession, sa_id: int) -> set[str]:
    result = await session.execute(
        select(Application.slug)
        .join(service_account_app_access, service_account_app_access.c.app_id == Application.id)
        .where(service_account_app_access.c.service_account_id == sa_id)
    )
    return set(result.scalars().all())


async def _make_shared_context(session: AsyncSession) -> tuple[Application, Application]:
    """Une app partagée prod (approuvée, active) + une app partagée atelier
    (approuvée, active) — la situation du banc."""
    prod_shared = await _make_app(
        session, "exemple-app-4", "Exemple App 4", is_shared=True
    )
    ws_shared = await _make_app(
        session, "exemple-app-8-ws", "Exemple App 8",
        is_shared=True, is_workshop=True,
    )
    return prod_shared, ws_shared


class TestApproveGrantsSameContextOnly:
    """Source du banc : l'approbation copiait TOUTES les shared apps, atelier compris."""

    @pytest.mark.asyncio
    async def test_approve_prod_app_excludes_workshop_shared(
        self, client: AsyncClient, async_session: AsyncSession, admin_user: User
    ):
        prod_shared, ws_shared = await _make_shared_context(async_session)

        # App prod en attente + SA inactif lié (comme après /register)
        pending_app = await _make_app(
            async_session, "controle-socle", "Controle Socle",
            approved=False, active=False,
        )
        sa = ServiceAccount(
            name="SA - Controle Socle",
            client_id="sa_an2_approve",
            client_secret_hash="x",
            is_active=False,
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)
        await _link(async_session, sa, pending_app)

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        response = await client.post(
            "/api/apps/controle-socle/approve",
            json={"approve": True},
            headers=get_auth_header(token),
        )
        assert response.status_code == 200, response.text

        slugs = await _sa_app_ids(async_session, sa.id)
        assert "controle-socle" in slugs
        assert "exemple-app-4" in slugs, "le service partagé prod doit être accordé"
        assert "exemple-app-8-ws" not in slugs, (
            "AN-2 : l'atelier ne doit PAS être accordé au SA d'une app prod"
        )


class TestAdminCreateGrantsSameContextOnly:
    @pytest.mark.asyncio
    async def test_create_app_sa_excludes_workshop_shared(
        self, client: AsyncClient, async_session: AsyncSession, admin_user: User
    ):
        prod_shared, ws_shared = await _make_shared_context(async_session)

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        response = await client.post(
            "/api/apps/",
            json={
                "name": "Nouvelle Prod",
                "slug": "nouvelle-prod",
                "api_port": 18110,
                "create_service_account": True,
            },
            headers=get_auth_header(token),
        )
        assert response.status_code == 201, response.text

        result = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.name == "SA - Nouvelle Prod")
        )
        sa = result.scalar_one()
        slugs = await _sa_app_ids(async_session, sa.id)
        assert "nouvelle-prod" in slugs
        assert "exemple-app-4" in slugs
        assert "exemple-app-8-ws" not in slugs, (
            "AN-2 : création admin → pas d'atelier dans le SA d'une app prod"
        )


class TestAutoRegisterGrantsSameContextOnly:
    @pytest.mark.asyncio
    async def test_auto_register_sa_excludes_workshop_shared(
        self, client: AsyncClient, async_session: AsyncSession, admin_user: User
    ):
        prod_shared, ws_shared = await _make_shared_context(async_session)

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        response = await client.post(
            "/api/apps/auto-register",
            json={
                "slug": "auto-prod-an2",
                "name": "Auto Prod AN2",
                "api_port": 18111,
            },
            headers=get_auth_header(token),
        )
        assert response.status_code == 200, response.text

        result = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.name == "SA - Auto Prod AN2")
        )
        sa = result.scalar_one()
        slugs = await _sa_app_ids(async_session, sa.id)
        assert "auto-prod-an2" in slugs
        assert "exemple-app-4" in slugs
        assert "exemple-app-8-ws" not in slugs, (
            "AN-2 : auto-register → pas d'atelier dans le SA d'une app prod"
        )


class TestManualSACreationParity:
    """Régression : la création manuelle admin (déjà paritaire) le reste."""

    @pytest.mark.asyncio
    async def test_manual_sa_on_prod_app_excludes_workshop_shared(
        self, client: AsyncClient, async_session: AsyncSession, admin_user: User
    ):
        prod_shared, ws_shared = await _make_shared_context(async_session)
        prod_app = await _make_app(async_session, "manual-prod", "Manual Prod")

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        response = await client.post(
            "/api/service-accounts/",
            json={"name": "SA manuel prod", "app_ids": [prod_app.id]},
            headers=get_auth_header(token),
        )
        assert response.status_code == 201, response.text

        result = await async_session.execute(
            select(ServiceAccount).where(ServiceAccount.name == "SA manuel prod")
        )
        sa = result.scalar_one()
        slugs = await _sa_app_ids(async_session, sa.id)
        assert "manual-prod" in slugs
        assert "exemple-app-4" in slugs
        assert "exemple-app-8-ws" not in slugs


class TestRegenerateSecretSyncsSameContextOnly:
    @pytest.mark.asyncio
    async def test_regenerate_resync_excludes_workshop_shared(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        prod_shared, ws_shared = await _make_shared_context(async_session)
        my_app = await _make_app(async_session, "regen-prod", "Regen Prod")

        sa = ServiceAccount(
            name="SA - Regen Prod",
            client_id="sa_an2_regen",
            client_secret_hash="x",
            is_active=True,
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)
        await _link(async_session, sa, my_app)

        token, _, _ = create_access_token(
            username="sa_an2_regen",
            apps={"regen-prod": "service"},
            is_service_account=True,
        )
        response = await client.post(
            "/api/apps/me/regenerate-secret",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text

        slugs = await _sa_app_ids(async_session, sa.id)
        assert "exemple-app-4" in slugs, "resync prod attendu"
        assert "exemple-app-8-ws" not in slugs, (
            "AN-2 : resync ne doit pas réintroduire l'atelier"
        )


class TestJWTAppsClaimFiltered:
    """Le claim `apps` du JWT M2M ne contient plus que les apps actives et
    approuvées (fuite du banc : 13 apps, ateliers et obsolètes inclus)."""

    async def _get_claim(self, client: AsyncClient, async_session: AsyncSession) -> dict:
        """SA type banc : son app + shared prod + atelier (fuite) + app
        désactivée + app non approuvée."""
        prod_shared, ws_shared = await _make_shared_context(async_session)
        inactive_shared = await _make_app(
            async_session, "service-hors-ligne", "Hors Ligne",
            is_shared=True, active=False,
        )
        pending_shared = await _make_app(
            async_session, "service-pending", "Pending",
            is_shared=True, approved=False,
        )
        my_app = await _make_app(async_session, "controle-socle", "Controle Socle")

        secret = "secret-an2-jwt"
        sa = ServiceAccount(
            name="SA - Controle Socle",
            client_id="sa_an2_jwt",
            client_secret_hash=get_password_hash(secret),
            is_active=True,
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)
        for linked in (my_app, prod_shared, ws_shared, inactive_shared, pending_shared):
            await _link(async_session, sa, linked)

        from urllib.parse import urlencode
        response = await client.post(
            "/api/auth/token",
            content=urlencode({
                "grant_type": "client_credentials",
                "client_id": "sa_an2_jwt",
                "client_secret": secret,
            }),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200, response.text
        jwt_token = response.json()["access_token"]
        payload = verify_token(jwt_token)
        return payload["apps"]

    @pytest.mark.asyncio
    async def test_claim_contains_owner_and_active_approved_shared_only(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        apps = await self._get_claim(client, async_session)

        assert apps.get("controle-socle") == "service", "l'app propriétaire doit y être"
        assert apps.get("exemple-app-4") == "service", "shared prod active/approuvée attendue"

    @pytest.mark.asyncio
    async def test_claim_excludes_workshop_leak(self, client: AsyncClient, async_session: AsyncSession):
        apps = await self._get_claim(client, async_session)
        assert "exemple-app-8-ws" not in apps, (
            "AN-2 : l'atelier ne doit plus figurer dans le JWT d'un SA prod"
        )

    @pytest.mark.asyncio
    async def test_claim_excludes_inactive_and_pending_apps(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        apps = await self._get_claim(client, async_session)
        assert "service-hors-ligne" not in apps, "app désactivée : hors token"
        assert "service-pending" not in apps, "app non approuvée : hors token"


class TestPurgeWorkshopLeaks:
    """Purge one-shot des liens N-N croisés atelier↔prod existants."""

    async def _purge(self, session: AsyncSession) -> dict:
        from app.m2m_scope import purge_workshop_leaks
        return await purge_workshop_leaks(session)

    @pytest.mark.asyncio
    async def test_purge_removes_cross_context_links_keeps_same_context(
        self, async_session: AsyncSession
    ):
        prod_shared, ws_shared = await _make_shared_context(async_session)
        my_app = await _make_app(async_session, "controle-socle", "Controle Socle")
        ws_app = await _make_app(async_session, "ghost-ws", "Ghost Atelier", is_workshop=True)

        # SA prod (owner identifiable) avec une fuite atelier
        sa_prod = ServiceAccount(
            name="SA - Controle Socle",
            client_id="sa_purge_prod",
            client_secret_hash="x",
            is_active=True,
        )
        # SA atelier (owner identifiable) avec une fuite prod
        sa_ws = ServiceAccount(
            name="SA - Ghost Atelier",
            client_id="sa_purge_ws",
            client_secret_hash="x",
            is_active=True,
        )
        async_session.add_all([sa_prod, sa_ws])
        await async_session.commit()
        await async_session.refresh(sa_prod)
        await async_session.refresh(sa_ws)
        for linked in (my_app, prod_shared, ws_shared):
            await _link(async_session, sa_prod, linked)
        for linked in (ws_app, ws_shared, prod_shared):
            await _link(async_session, sa_ws, linked)

        report = await self._purge(async_session)

        prod_slugs = await _sa_app_ids(async_session, sa_prod.id)
        assert "exemple-app-8-ws" not in prod_slugs, "fuite atelier purgée du SA prod"
        assert {"controle-socle", "exemple-app-4"} <= prod_slugs, "liens prod intacts"

        ws_slugs = await _sa_app_ids(async_session, sa_ws.id)
        assert "exemple-app-4" not in ws_slugs, "fuite prod purgée du SA atelier"
        assert {"ghost-ws", "exemple-app-8-ws"} <= ws_slugs, "liens atelier intacts"

        assert any("exemple-app-8-ws" in s for s in report.get("removed", [])), report

    @pytest.mark.asyncio
    async def test_purge_skips_ambiguous_mixed_sa(self, async_session: AsyncSession):
        prod_shared, ws_shared = await _make_shared_context(async_session)
        app_a = await _make_app(async_session, "app-a", "App A")
        ws_b = await _make_app(async_session, "ws-b", "Ws B", is_workshop=True)

        sa = ServiceAccount(
            name="SA partagé ambigu",  # aucun owner identifiable, contextes mixtes
            client_id="sa_purge_ambigu",
            client_secret_hash="x",
            is_active=True,
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)
        for linked in (app_a, ws_b, prod_shared, ws_shared):
            await _link(async_session, sa, linked)

        report = await self._purge(async_session)

        slugs = await _sa_app_ids(async_session, sa.id)
        assert slugs == {"app-a", "ws-b", "exemple-app-4", "exemple-app-8-ws"}, (
            "SA ambigu : intouché (décision manuelle requise)"
        )
        assert "sa_purge_ambigu" in " ".join(report.get("skipped_ambiguous", [])), report


class TestGrantHelperUnit:
    """Test unitaire du helper central de grant."""

    @pytest.mark.asyncio
    async def test_grant_respects_workshop_parity(self, async_session: AsyncSession):
        from app.m2m_scope import grant_shared_apps_to_sa

        prod_shared, ws_shared = await _make_shared_context(async_session)
        ws_other = await _make_app(async_session, "canari-ws", "Canari", is_shared=True, is_workshop=True)
        my_ws_app = await _make_app(async_session, "mon-atelier-ws", "Mon Atelier", is_workshop=True)

        sa = ServiceAccount(
            name="SA - Mon Atelier",
            client_id="sa_helper_ws",
            client_secret_hash="x",
            is_active=True,
        )
        async_session.add(sa)
        await async_session.commit()
        await async_session.refresh(sa)
        await _link(async_session, sa, my_ws_app)

        added = await grant_shared_apps_to_sa(async_session, sa)

        assert sorted(added) == ["canari-ws", "exemple-app-8-ws"], added
        slugs = await _sa_app_ids(async_session, sa.id)
        assert "exemple-app-4" not in slugs, "pas de shared prod dans un SA atelier"
        assert {"mon-atelier-ws", "exemple-app-8-ws", "canari-ws"} <= slugs
