# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests de POST /api/apps/cert/issue — émission de certs serveur signés par la PKI du HUB."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

import pki
from app.auth import create_access_token, get_password_hash
from app.config import get_settings
from app.models import Application, ServiceAccount, service_account_app_access


def _sa_headers(service_account) -> dict:
    """Fabrique un header Bearer M2M pour le service account (même pattern que test_m2m_slug_authorization)."""
    token, _, _ = create_access_token(
        username=service_account.client_id,
        apps={app.slug: "service" for app in service_account.applications},
        is_service_account=True,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def certs_env(tmp_path, monkeypatch):
    """Redirige toute la PKI vers tmp_path : aucun accès au vrai certs/.

    La clé RSA passe à 2048 bits pour garder les tests rapides (4096 en prod).
    """
    monkeypatch.setattr(pki, "_certs_dir", lambda: tmp_path)
    monkeypatch.setattr(pki.settings, "pki_key_size", 2048)
    pki.init_ca()
    return tmp_path


@pytest.mark.asyncio
async def test_cert_issue_requires_m2m(client):
    """Sans Bearer M2M : 401."""
    r = await client.post("/api/apps/cert/issue")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_cert_issue_returns_pems(client, service_account, certs_env):
    r = await client.post("/api/apps/cert/issue", headers=_sa_headers(service_account))
    assert r.status_code == 200
    body = r.json()
    assert body["cert"].startswith("-----BEGIN CERTIFICATE-----")
    assert body["key"].startswith("-----BEGIN PRIVATE KEY-----")
    assert body["ca"].startswith("-----BEGIN CERTIFICATE-----")
    assert body["san"]
    assert len(body["fingerprint"]) == 64  # SHA-256 hex


@pytest.mark.asyncio
async def test_cert_issue_san_inclut_localhost_et_hub_host(client, service_account, certs_env):
    """Demande utilisateur 2026-09-19 : le SAN couvre localhost (accès opérateur,
    curl --cacert depuis le serveur) ET le host du HUB (accès portail/utilisateurs)."""
    r = await client.post("/api/apps/cert/issue", headers=_sa_headers(service_account))
    assert r.status_code == 200
    san = r.json()["san"]
    assert "localhost" in san
    assert get_settings().get_hub_host() in san


@pytest.mark.asyncio
async def test_cert_issue_idempotent(client, service_account, certs_env):
    r1 = await client.post("/api/apps/cert/issue", headers=_sa_headers(service_account))
    r2 = await client.post("/api/apps/cert/issue", headers=_sa_headers(service_account))
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["fingerprint"] == r2.json()["fingerprint"]


@pytest.mark.asyncio
async def test_cert_issue_reissues_on_san_change(client, service_account, certs_env, monkeypatch):
    r1 = await client.post("/api/apps/cert/issue", headers=_sa_headers(service_account))
    # Changer le hub_host simule un renouvellement → SAN différent → ré-émission
    monkeypatch.setattr(get_settings(), "hub_host", "10.9.8.7", raising=False)
    r2 = await client.post("/api/apps/cert/issue", headers=_sa_headers(service_account))
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["fingerprint"] != r2.json()["fingerprint"]


@pytest.mark.asyncio
async def test_cert_issue_reissues_on_expiry_within_margin(client, service_account, certs_env, monkeypatch):
    """Cert existant expirant demain + même SAN → ré-émission (marge 30j)."""
    # 1er cert avec validité 1 jour → expire demain, dans la marge de 30j
    monkeypatch.setattr(pki.settings, "pki_cert_validity_days", 1)
    r1 = await client.post("/api/apps/cert/issue", headers=_sa_headers(service_account))
    assert r1.status_code == 200
    # Validité normale → le cert existant (expirant demain) doit être réémis, SAN identique
    monkeypatch.setattr(pki.settings, "pki_cert_validity_days", 365)
    r2 = await client.post("/api/apps/cert/issue", headers=_sa_headers(service_account))
    assert r2.status_code == 200
    assert r1.json()["san"] == r2.json()["san"]
    assert r1.json()["fingerprint"] != r2.json()["fingerprint"]


@pytest.fixture
async def foreign_sa_and_app(async_session):
    """Un 2e service account lié à sa propre app — cible pour l'isolement cross-slug.

    Même pattern que la fixture `service_account` de conftest, slug distinct.
    """
    app = Application(
        name="Foreign App",
        slug="foreign-app",
        api_url="http://localhost:8009",
        frontend_url="http://localhost:3009",
        callback_url="http://localhost:3009/callback",
        is_active=True,
    )
    async_session.add(app)
    await async_session.flush()
    sa = ServiceAccount(
        name="SA - Foreign App",
        client_id="sa_foreign_987654321",
        client_secret_hash=get_password_hash("secret"),
        is_active=True,
    )
    async_session.add(sa)
    await async_session.flush()
    await async_session.execute(
        service_account_app_access.insert().values(
            service_account_id=sa.id,
            app_id=app.id,
        )
    )
    await async_session.commit()
    result = await async_session.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.id == sa.id)
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_cert_issue_with_owned_x_slug(client, service_account, certs_env):
    """X-Slug explicite sur un slug possédé par le SA : émission normale."""
    headers = {**_sa_headers(service_account), "X-Slug": service_account.applications[0].slug}
    r = await client.post("/api/apps/cert/issue", headers=headers)
    assert r.status_code == 200
    assert r.json()["cert"].startswith("-----BEGIN CERTIFICATE-----")


@pytest.mark.asyncio
async def test_cert_issue_rejects_slug_of_another_sa(client, service_account, foreign_sa_and_app, certs_env):
    """X-Slug visant l'app d'un AUTRE SA : 403 et ses fichiers cert restent intacts."""
    foreign_slug = foreign_sa_and_app.applications[0].slug
    # L'autre SA émet légitimement son cert pour avoir un fichier à protéger
    r_owner = await client.post(
        "/api/apps/cert/issue",
        headers={**_sa_headers(foreign_sa_and_app), "X-Slug": foreign_slug},
    )
    assert r_owner.status_code == 200
    crt_path = certs_env / "server" / foreign_slug / "server.crt"
    key_path = certs_env / "server" / foreign_slug / "server.key"
    before = (crt_path.read_bytes(), key_path.read_bytes())

    r = await client.post(
        "/api/apps/cert/issue",
        headers={**_sa_headers(service_account), "X-Slug": foreign_slug},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "Service account not authorized for this application"
    assert (crt_path.read_bytes(), key_path.read_bytes()) == before
