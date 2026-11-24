# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Garde-régression TLS des appels GitHub : vérification du certificat serveur.

Le Hub envoie le PAT GitHub à api.github.com et raw.githubusercontent.com :
chaque interaction doit construire son client httpx SANS désactiver la
vérification (`verify=False` rendait le jeton interceptable par un MITM).

Un test par interaction (cinq sites de construction du client) :
- github_client.fetch_github_json
- github_client.fetch_github_raw
- github_client.fetch_github_raw_with_status
- repo_access._api_repo_status
- routes/github_catalog.refresh_github_repos

Hors périmètre : les `verify=False` vers les applications du parc
(certificats auto-signés).
"""

from types import SimpleNamespace

import pytest
from httpx import AsyncClient

import app.github_client as github_client
import app.repo_access as repo_access
import app.routes.github_catalog as github_catalog
from app.auth import create_access_token
from app.models import User
from tests.conftest import get_auth_header


# =============================================================================
# Helpers
# =============================================================================

class _FakeAsyncClient:
    """Client httpx factice : context manager async, GET servi par `response`.

    Enregistre les URLs demandées dans `urls` (preuve que l'appel est bien
    passé par ce client).
    """

    def __init__(self, response, urls):
        self._response = response
        self._urls = urls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, **kwargs):
        self._urls.append(url)
        return self._response


def _response(status_code=200, payload=None, text=""):
    """Réponse factice : ce que lisent les cinq sites (status, json, text, headers)."""
    return SimpleNamespace(
        status_code=status_code,
        json=lambda: payload,
        text=text,
        headers={},
    )


def _record_httpx_clients(monkeypatch, module, response):
    """Remplace httpx.AsyncClient tel que vu par `module` par une fabrique qui
    enregistre ses arguments nommés.

    Retourne (created, urls) : kwargs de construction (un par client créé) et
    URLs demandées.
    """
    created, urls = [], []

    def factory(*args, **kwargs):
        created.append(kwargs)
        return _FakeAsyncClient(response, urls)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    return created, urls


def _assert_tls_verification_enabled(created, site):
    """Le client est construit avec la vérification TLS active : `verify` absent
    (défaut httpx : True) ou différent de False."""
    assert len(created) == 1, (
        f"{site} : {len(created)} client(s) httpx construit(s), 1 attendu"
    )
    verify = created[0].get("verify", True)
    assert verify is not False, (
        f"{site} : httpx.AsyncClient créé avec verify=False — vérification du "
        f"certificat serveur désactivée (kwargs={created[0]})"
    )


# =============================================================================
# hub/app/github_client.py
# =============================================================================

class TestGithubClientTlsVerification:
    """Client GitHub partagé : trois fonctions, trois constructions de client."""

    @pytest.fixture(autouse=True)
    def _no_github_headers(self, monkeypatch):
        """Headers factices : isole le test de la config, de l'env et de la base
        (le sujet est la construction du client, pas l'authentification)."""
        async def fake_github_headers(db=None):
            return {}

        monkeypatch.setattr(github_client, "github_headers", fake_github_headers)

    async def test_fetch_github_json(self, monkeypatch):
        created, urls = _record_httpx_clients(
            monkeypatch, github_client, _response(200, {"login": "octocat"})
        )

        data, status = await github_client.fetch_github_json("https://api.github.com/user")

        assert (data, status) == ({"login": "octocat"}, 200)
        assert urls == ["https://api.github.com/user"]
        _assert_tls_verification_enabled(created, "github_client.fetch_github_json")

    async def test_fetch_github_raw(self, monkeypatch):
        created, urls = _record_httpx_clients(
            monkeypatch, github_client, _response(200, text="contenu-brut")
        )

        text = await github_client.fetch_github_raw("https://raw.githubusercontent.com/o/r/README.md")

        assert text == "contenu-brut"
        assert urls == ["https://raw.githubusercontent.com/o/r/README.md"]
        _assert_tls_verification_enabled(created, "github_client.fetch_github_raw")

    async def test_fetch_github_raw_with_status(self, monkeypatch):
        created, urls = _record_httpx_clients(
            monkeypatch, github_client, _response(404)
        )

        text, status = await github_client.fetch_github_raw_with_status(
            "https://raw.githubusercontent.com/o/r/hub-app.yaml"
        )

        assert (text, status) == (None, 404)
        assert urls == ["https://raw.githubusercontent.com/o/r/hub-app.yaml"]
        _assert_tls_verification_enabled(created, "github_client.fetch_github_raw_with_status")


# =============================================================================
# hub/app/repo_access.py
# =============================================================================

class TestRepoAccessTlsVerification:
    """Diagnostic API GitHub du préflight d'accès Git."""

    async def test_api_repo_status(self, monkeypatch):
        created, urls = _record_httpx_clients(monkeypatch, repo_access, _response(200))

        status = await repo_access._api_repo_status(("octocat", "hello-world"), "ghp_test")

        assert status == 200
        assert urls == ["https://api.github.com/repos/octocat/hello-world"]
        _assert_tls_verification_enabled(created, "repo_access._api_repo_status")


# =============================================================================
# hub/app/routes/github_catalog.py
# =============================================================================

class TestGithubCatalogTlsVerification:
    """Synchronisation du catalogue des dépôts (/user/repos paginé)."""

    async def test_refresh_github_repos(
        self,
        client: AsyncClient,
        admin_user: User,
        settings,
        monkeypatch,
    ):
        """POST /api/apps/github/repos/refresh : le client de la pagination."""
        settings.github_token = "ghp_test"
        # Scan de fond des manifestes hors sujet : neutralisé (zéro réseau)
        monkeypatch.setattr(github_catalog, "start_manifest_scan", lambda: True)
        created, urls = _record_httpx_clients(monkeypatch, github_catalog, _response(200, []))

        token, _, _ = create_access_token(username=admin_user.username, apps={})
        resp = await client.post(
            "/api/apps/github/repos/refresh",
            headers=get_auth_header(token),
        )

        assert resp.status_code == 200
        assert resp.json()["error"] is None
        assert len(urls) == 1
        _assert_tls_verification_enabled(created, "routes/github_catalog.refresh_github_repos")
