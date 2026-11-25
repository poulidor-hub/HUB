# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Le compte de service propriétaire suit le nom de son application.

Le compte propriétaire est reconnu par une convention de NOM (« SA - <nom de
l'application> », cf. is_owner_service_account). Renommer l'application sans
renommer son compte la déposséderait : le compte ne serait plus reconnu, la
ré-inscription suivante croirait le compte disparu et « réparerait » — nouveau
compte actif, secret renvoyé à l'appelant, adresses réécrites. C'est la prise de
contrôle fermée par la règle du jeton propriétaire, rouverte par un renommage.

Quatre chemins peuvent renommer une application : sa propre ré-inscription, le
PATCH administrateur, l'auto-enregistrement déclenché par un administrateur et
l'import par lot. Chacun est couvert.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Application, ApprovalStatus, ServiceAccount
from tests.conftest import get_auth_header

GARDE_IP = patch("app.routes.apps.is_docker_network_ip", return_value=True)
SECRETS = patch("app.routes.service_accounts.SECRETS_DIR", Path("/tmp/opencode/secu/secrets-tests"))


async def _inscrire_et_approuver(client: AsyncClient, async_session: AsyncSession,
                                 tmp_path: Path, slug: str, nom: str) -> dict:
    """Inscription depuis le réseau interne, puis approbation. Renvoie les identifiants."""
    with GARDE_IP, patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
        r = await client.post("/api/apps/register", json={
            "slug": slug, "name": nom, "api_port": 19701, "frontend_port": 19702})
    assert r.status_code == 200, r.text
    identifiants = r.json()["credentials"]

    app = (await async_session.execute(
        select(Application).where(Application.slug == slug))).scalar_one()
    app.approval_status = ApprovalStatus.approved
    app.is_active = True
    sa = (await async_session.execute(
        select(ServiceAccount).where(ServiceAccount.client_id == identifiants["client_id"]))).scalar_one()
    sa.is_active = True
    await async_session.commit()
    return identifiants


async def _jeton(client: AsyncClient, identifiants: dict) -> str:
    r = await client.post("/api/auth/token", data={
        "grant_type": "client_credentials",
        "client_id": identifiants["client_id"], "client_secret": identifiants["client_secret"]})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _etat(async_session: AsyncSession, slug: str):
    result = await async_session.execute(
        select(Application).options(selectinload(Application.service_accounts))
        .where(Application.slug == slug))
    app = result.scalar_one()
    return app, sorted(sa.name for sa in app.service_accounts)


async def _le_proprietaire_reste_reconnu(client: AsyncClient, async_session: AsyncSession, slug: str, nom: str):
    """Après un renommage, l'application garde un propriétaire reconnu : une
    ré-inscription SANS jeton est refusée et son adresse de lancement ne bouge pas."""
    app, _ = await _etat(async_session, slug)
    ancienne_url = app.frontend_url
    with GARDE_IP:
        r = await client.post("/api/apps/register", json={
            "slug": slug, "name": nom, "api_port": 19701, "frontend_port": 19702,
            "custom_host": "192.0.2.99"})
    assert r.status_code == 401, (
        f"ré-inscription sans jeton acceptée après renommage ({r.status_code}) : "
        "l'application n'a plus de propriétaire reconnu")
    await async_session.refresh(app)
    assert app.frontend_url == ancienne_url


@pytest.mark.asyncio
async def test_le_compte_proprietaire_suit_le_renommage_par_l_application(
        client: AsyncClient, async_session: AsyncSession, tmp_path: Path):
    identifiants = await _inscrire_et_approuver(client, async_session, tmp_path, "renom-app", "Ancien Nom")
    jeton = await _jeton(client, identifiants)

    with GARDE_IP, patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
        r = await client.post("/api/apps/register", json={
            "slug": "renom-app", "name": "Nouveau Nom", "api_port": 19701, "frontend_port": 19702},
            headers={"Authorization": f"Bearer {jeton}", "X-Hub-Register-Idempotent": "1"})

    assert r.status_code == 200, r.text
    assert r.json()["secret_rotated"] is False, (
        "le propriétaire a prouvé son identité avec le marqueur : son secret doit être conservé")
    app, comptes = await _etat(async_session, "renom-app")
    assert app.name == "Nouveau Nom"
    assert comptes == ["SA - Nouveau Nom"], (
        "le compte propriétaire doit suivre le nom de l'application, sans compte surnuméraire")
    await _le_proprietaire_reste_reconnu(client, async_session, "renom-app", "Nouveau Nom")


@pytest.mark.asyncio
async def test_le_compte_proprietaire_suit_le_renommage_par_le_patch_admin(
        client: AsyncClient, async_session: AsyncSession, tmp_path: Path, admin_token: str):
    await _inscrire_et_approuver(client, async_session, tmp_path, "renom-patch", "Ancien Nom")
    app, _ = await _etat(async_session, "renom-patch")

    r = await client.patch(f"/api/apps/{app.id}", json={"name": "Nouveau Nom"},
                           headers=get_auth_header(admin_token))

    assert r.status_code == 200, r.text
    _, comptes = await _etat(async_session, "renom-patch")
    assert comptes == ["SA - Nouveau Nom"]
    await _le_proprietaire_reste_reconnu(client, async_session, "renom-patch", "Nouveau Nom")


@pytest.mark.asyncio
async def test_le_compte_proprietaire_suit_le_renommage_par_l_auto_enregistrement_admin(
        client: AsyncClient, async_session: AsyncSession, tmp_path: Path, admin_token: str):
    await _inscrire_et_approuver(client, async_session, tmp_path, "renom-auto", "Ancien Nom")

    with patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
        r = await client.post("/api/apps/auto-register", json={
            "slug": "renom-auto", "name": "Nouveau Nom", "api_port": 19701, "frontend_port": 19702},
            headers=get_auth_header(admin_token))

    assert r.status_code == 200, r.text
    _, comptes = await _etat(async_session, "renom-auto")
    assert comptes == ["SA - Nouveau Nom"]
    await _le_proprietaire_reste_reconnu(client, async_session, "renom-auto", "Nouveau Nom")


@pytest.mark.asyncio
async def test_le_compte_proprietaire_suit_le_renommage_par_l_import_par_lot(
        client: AsyncClient, async_session: AsyncSession, tmp_path: Path, admin_token: str):
    await _inscrire_et_approuver(client, async_session, tmp_path, "renom-import", "Ancien Nom")

    with patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
        r = await client.post("/api/apps/batch-import", json={"apps": [{
            "base_slug": "renom-import", "name": "Nouveau Nom",
            "api_port": 19701, "frontend_port": 19702}]},
            headers=get_auth_header(admin_token))

    assert r.status_code == 200, r.text
    _, comptes = await _etat(async_session, "renom-import")
    assert comptes == ["SA - Nouveau Nom"]
    await _le_proprietaire_reste_reconnu(client, async_session, "renom-import", "Nouveau Nom")
