# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Approuver ou rejeter une application republie le routage.

Une application non approuvée n'entre plus dans la zone DNS ni dans les hôtes
virtuels. L'approbation doit donc republier le routage : sans cela, une
application approuvée ne deviendrait joignable par son nom qu'au prochain
redémarrage du Hub ou au prochain autre changement (revue finale de la branche).
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApprovalStatus
from tests.conftest import get_auth_header


async def _en_attente(async_session: AsyncSession, test_app) -> None:
    test_app.approval_status = ApprovalStatus.pending
    test_app.is_active = False
    async_session.add(test_app)
    await async_session.commit()


@pytest.mark.asyncio
async def test_approuver_une_application_republie_le_routage(
        client: AsyncClient, async_session: AsyncSession, test_app, admin_token: str):
    await _en_attente(async_session, test_app)
    republications = []

    async def faux_regenerate():
        republications.append(True)

    with patch("app.routing_service.regenerate", faux_regenerate):
        r = await client.post(f"/api/apps/{test_app.slug}/approve", json={"approve": True},
                              headers=get_auth_header(admin_token))

    assert r.status_code == 200, r.text
    assert republications == [True], (
        "une application approuvée doit être publiée par le routage sans attendre un redémarrage")


@pytest.mark.asyncio
async def test_rejeter_une_application_republie_le_routage(
        client: AsyncClient, async_session: AsyncSession, test_app, admin_token: str):
    await _en_attente(async_session, test_app)
    republications = []

    async def faux_regenerate():
        republications.append(True)

    with patch("app.routing_service.regenerate", faux_regenerate):
        r = await client.post(f"/api/apps/{test_app.slug}/approve",
                              json={"approve": False, "reason": "Demande refusée"},
                              headers=get_auth_header(admin_token))

    assert r.status_code == 200, r.text
    assert republications == [True]


@pytest.mark.asyncio
async def test_une_publication_du_routage_en_echec_ne_bloque_pas_l_approbation(
        client: AsyncClient, async_session: AsyncSession, test_app, admin_token: str):
    await _en_attente(async_session, test_app)

    async def regenerate_en_echec():
        raise RuntimeError("publication impossible")

    with patch("app.routing_service.regenerate", regenerate_en_echec):
        r = await client.post(f"/api/apps/{test_app.slug}/approve", json={"approve": True},
                              headers=get_auth_header(admin_token))

    assert r.status_code == 200, r.text
    await async_session.refresh(test_app)
    assert test_app.approval_status == ApprovalStatus.approved
