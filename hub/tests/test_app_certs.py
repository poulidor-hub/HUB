"""Tests de POST /api/apps/cert/issue — émission de certs serveur signés par la PKI du HUB."""
import pytest

import pki
from app.auth import create_access_token
from app.config import get_settings


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
