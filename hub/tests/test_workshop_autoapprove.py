# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Auto-approbation atelier par fallback NOM (incident 09-10/11/2026).

L'auto-approbation d'un atelier (`-ws`) dans POST /api/apps/register ne
matchait l'app de base QUE par slug dérivé (`<slug>-ws` → `<slug>`).
Quand le slug de l'app principale diffère du préfixe de l'atelier (ex.
atelier `exemple-app-ws` mais app de base `exemple-app`),
l'atelier restait `pending`/`inactive` à vie : SA désactivé → 403 au
mint → martèlement register/token (2019 réinscriptions legacy, 2523×403,
257×429 pendant 2h35 cette nuit).

Fallback : si le slug dérivé ne matche pas, chercher une app de base
APPROUVÉE NON-ATELIER au même NOM que l'atelier (les ateliers héritent du
nom de l'app principale).
"""
import pytest

from app.models import Application, ApprovalStatus

import app.routes.apps as apps_mod
from tests.conftest import get_auth_header


@pytest.fixture
async def base_app(async_session):
    """App de base APPROUVÉE dont le slug ne préfixe PAS le slug atelier."""
    app = Application(
        name="Transfert de fichiers",
        slug="exemple-app",
        description="App de base (slug différent du dossier atelier)",
        api_url="http://localhost:9109",
        frontend_url="http://localhost:9110",
        callback_url="http://localhost:9110/callback",
        is_active=True,
        approval_status=ApprovalStatus.approved,
        requires_auth=True,
    )
    async_session.add(app)
    await async_session.commit()
    await async_session.refresh(app)
    return app


@pytest.fixture
def docker_ip(monkeypatch):
    """Les tests httpx arrivent avec une IP non-Docker : on simule le réseau
    Docker de confiance (condition d'accès de POST /api/apps/register)."""
    monkeypatch.setattr(apps_mod, "is_docker_network_ip", lambda ip: True)


class TestWorkshopAutoApproveByName:
    @pytest.mark.asyncio
    async def test_creation_autoapproved_by_name(
            self, client, base_app, docker_ip):
        """Création : atelier dont le slug de base dérivé ne matche rien mais
        dont le NOM correspond à une app approuvée → auto-approuvé."""
        r = await client.post(
            "/api/apps/register",
            json={"slug": "exemple-app-ws", "name": "Transfert de fichiers",
                  "api_port": 19109},
        )
        assert r.status_code == 200, r.text
        app = r.json()["application"]
        assert app["slug"] == "exemple-app-ws"
        assert app["approval_status"] == "approved", (
            "l'atelier doit être auto-approuvé par fallback nom "
            f"(status: {app['approval_status']})")
        assert app["is_active"] is True

    @pytest.mark.asyncio
    async def test_creation_pending_when_no_base_match(self, client, docker_ip):
        """Garde : sans app de base (ni par slug, ni par nom), l'atelier
        reste pending — l'auto-approbation ne doit pas s'élargir au vide."""
        r = await client.post(
            "/api/apps/register",
            json={"slug": "orphelin-ws", "name": "App Sans Base",
                  "api_port": 19119},
        )
        assert r.status_code == 200, r.text
        app = r.json()["application"]
        assert app["approval_status"] == "pending"
        assert app["is_active"] is False

    @pytest.mark.asyncio
    async def test_reregistration_autoapproved_by_name(
            self, client, base_app, async_session, docker_ip):
        """Ré-enregistrement : atelier PENDING créé avant le fix, dont le slug
        dérivé ne matche pas mais le nom si → auto-approuvé au register."""
        orphan = Application(
            name="Transfert de fichiers",
            slug="exemple-app-ws",
            description="Atelier créé avant le fix (pending à vie)",
            api_url="http://localhost:19109",
            frontend_url="http://localhost:19110",
            callback_url="http://localhost:19110/callback",
            is_active=False,
            approval_status=ApprovalStatus.pending,
            is_workshop=True,
            requires_auth=True,
        )
        async_session.add(orphan)
        await async_session.commit()

        r = await client.post(
            "/api/apps/register",
            json={"slug": "exemple-app-ws", "name": "Transfert de fichiers",
                  "api_port": 19109},
        )
        assert r.status_code == 200, r.text
        await async_session.refresh(orphan)
        app = r.json()["application"]
        assert app["approval_status"] == "approved", (
            "l'atelier pending doit être auto-approuvé au ré-enregistrement "
            f"par fallback nom (status: {app['approval_status']})")
        assert app["is_active"] is True

    @pytest.mark.asyncio
    async def test_slug_match_still_works(self, client, base_app, docker_ip):
        """Invariant : le match par slug dérivé reste prioritaire."""
        r = await client.post(
            "/api/apps/register",
            json={"slug": "exemple-app-ws", "name": "Autre nom possible",
                  "api_port": 19129},
        )
        assert r.status_code == 200, r.text
        app = r.json()["application"]
        assert app["approval_status"] == "approved"
        assert app["is_active"] is True
