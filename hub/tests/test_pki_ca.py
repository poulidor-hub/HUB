"""Tests de GET /api/pki/ca.crt et /api/pki/ca/info — téléchargement de la CA racine du HUB.

Fonctionnalité 2026-10-06 : bouton « Certificat » sur la page Hub — l'utilisateur
télécharge la CA racine pour l'installer dans son magasin de confiance et supprimer
les avertissements « certificat auto-signé » sur le Hub et toutes les apps HTTPS.

La CA est publique par nature (aucun secret) : les endpoints sont sans auth,
alignés sur test_remote_app_m2m.py::test_1_download_ca_certificate qui les
appelle sans header. `ca.key` n'est jamais servi.
"""
import pytest

import pki


@pytest.fixture
def certs_env(tmp_path, monkeypatch):
    """Redirige toute la PKI vers tmp_path : aucun accès au vrai certs/.

    Même pattern que test_app_certs.py ; la clé RSA passe à 2048 bits
    pour garder les tests rapides (4096 en prod).
    """
    monkeypatch.setattr(pki, "_certs_dir", lambda: tmp_path)
    monkeypatch.setattr(pki.settings, "pki_key_size", 2048)
    pki.init_ca()
    return tmp_path


@pytest.mark.asyncio
async def test_ca_crt_download_public(client, certs_env):
    """GET /api/pki/ca.crt sans authentification : 200 + PEM + bon content-type."""
    r = await client.get("/api/pki/ca.crt")
    assert r.status_code == 200
    assert r.text.startswith("-----BEGIN CERTIFICATE-----")
    assert r.headers["content-type"].startswith("application/x-x509-ca-cert")
    assert "POULIDOR-Hub-Root-CA.crt" in r.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_ca_crt_404_when_not_initialized(client):
    """CA non initialisée (certs/ vide) : 404, pas une 500."""
    r = await client.get("/api/pki/ca.crt")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ca_info_public(client, certs_env):
    """GET /api/pki/ca/info : métadonnées affichables (sujet, validité, empreinte)."""
    r = await client.get("/api/pki/ca/info")
    assert r.status_code == 200
    body = r.json()
    assert body["subject"]
    assert body["not_before"]
    assert body["not_after"]
    assert len(body["fingerprint_sha256"]) == 64  # SHA-256 hex


@pytest.mark.asyncio
async def test_ca_key_never_served(client, certs_env):
    """La clé privée de la CA n'est jamais exposée, même avec la PKI initialisée."""
    for path in ("/api/pki/ca.key", "/api/pki/ca"):
        r = await client.get(path)
        assert r.status_code == 404, f"{path} ne doit pas être servi"
