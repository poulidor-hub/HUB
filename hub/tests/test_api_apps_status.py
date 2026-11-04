# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests de GET /api/apps/status — compteur de connectivité (admin).

Demande 2026-10-07 : les ateliers (-ws), le plus souvent arrêtés, ne doivent
pas gonfler le dénominateur (« 14/20 applications en ligne » avec 6 ateliers
éteints). Le summary ne compte plus que les apps de production ; les entrées
par app restent dans `apps[]` pour les icônes de la table admin.
"""
import pytest

from app.auth import create_access_token
from app.models import Application
from tests.conftest import get_auth_header


@pytest.fixture
async def admin_headers(admin_user):
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    return get_auth_header(token)


@pytest.fixture
async def stopped_workshop(async_session):
    """Atelier arrêté (is_active=False, is_workshop=True) sans URL joignable."""
    ws = Application(
        name="Test App Atelier",
        slug="test-app-ws",
        description="Atelier de test-app (arrêté)",
        icon="bi-tools",
        color="#6c757d",
        api_url="http://localhost:59999",
        frontend_url="http://localhost:59998",
        callback_url="http://localhost:59998/callback",
        is_active=False,
        is_workshop=True,
    )
    async_session.add(ws)
    await async_session.commit()
    await async_session.refresh(ws)
    return ws


@pytest.mark.asyncio
async def test_status_summary_exclut_ateliers(client, admin_headers, test_app, stopped_workshop):
    """Le summary ne compte que les apps de production, ateliers exclus."""
    r = await client.get("/api/apps/status", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()

    slugs = [a["slug"] for a in body["apps"]]
    assert "test-app" in slugs
    # L'entrée par app est conservée (icônes de la table admin, vue « Atelier »)
    assert "test-app-ws" in slugs

    # Le compteur : 1 seule app de production, l'atelier arrêté est exclu
    assert body["summary"]["total"] == 1
    assert body["summary"]["online"] + body["summary"]["offline"] + body["summary"]["error"] == 1


@pytest.mark.asyncio
async def test_status_summary_sans_ateliers_inchangé(client, admin_headers, test_app):
    """Sans atelier, le summary reste identique (non-régression)."""
    r = await client.get("/api/apps/status", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total"] == 1
