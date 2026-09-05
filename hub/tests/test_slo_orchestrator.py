"""Tests unitaires de l'orchestrateur SLO v2 (propagate_remote_revoke).

Le cookie hub_oidc_session porte un oidc_sub (immuable) depuis v3.47.0,
mais les traducteurs indexent les sessions distantes par username courant —
qui peut avoir dérivé après un renommage. L'orchestrateur doit résoudre
l'user en DB et appeler le revoke pour chaque identifiant (username
courant d'abord, valeur brute du cookie ensuite), dédupliqués, en
best-effort (jamais d'exception propagée).

httpx et _get_internal_sa_token sont monkeypatchés : aucune requête réseau.
"""
import types

import pytest

from app.auth import get_password_hash
from app.config import Settings
from app.models import User
from app.slo_orchestrator import propagate_remote_revoke


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text

    def json(self) -> dict:
        return {"deleted_sessions": 2}


def _make_fake_httpx(calls: list, status_by_url: dict):
    """Fabrique un substitut de module httpx qui enregistre les POST."""

    class _FakeClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            calls.append(url)
            status = status_by_url.get(url, 200)
            return _FakeResponse(status, "boom" if status >= 500 else "")

    return types.SimpleNamespace(AsyncClient=_FakeClient)


@pytest.fixture
def patched_slo(monkeypatch):
    """Patch httpx + token SA dans le module orchestrateur (pas de réseau)."""
    calls: list = []
    status_by_url: dict = {}
    monkeypatch.setattr(
        "app.slo_orchestrator.httpx",
        _make_fake_httpx(calls, status_by_url),
    )

    async def _fake_token(settings):
        return "fake-sa-token"

    monkeypatch.setattr("app.slo_orchestrator._get_internal_sa_token", _fake_token)
    return calls, status_by_url


@pytest.fixture
async def renamed_user(async_session) -> User:
    """User renommé : username courant jmartin, oidc_sub immuable jdupont."""
    user = User(
        username="jmartin",
        email="jmartin@test.com",
        hashed_password=get_password_hash("password123"),
        oidc_sub="jdupont",
        is_active=True,
    )
    async_session.add(user)
    await async_session.commit()
    await async_session.refresh(user)
    return user


def _revoke_url(app, identifier: str) -> str:
    return f"{str(app.api_url).rstrip('/')}/api/exemple-app/sessions/{identifier}/revoke"


@pytest.mark.asyncio
async def test_unrenamed_user_single_call_per_translator(
    async_session, test_app, settings: Settings, patched_slo
):
    """User non renommé (username == oidc_sub) : UN seul appel par traducteur."""
    calls, _ = patched_slo
    user = User(
        username="alin",
        email="alin@test.com",
        hashed_password=get_password_hash("password123"),
        oidc_sub="alin",
        is_active=True,
    )
    async_session.add(user)
    await async_session.commit()

    await propagate_remote_revoke(
        async_session, "alin", [{"translator_slug": "test-app"}], settings
    )

    assert calls == [_revoke_url(test_app, "alin")]


@pytest.mark.asyncio
async def test_renamed_user_two_calls_username_first(
    async_session, test_app, settings: Settings, patched_slo, renamed_user
):
    """User renommé, cookie porte l'oidc_sub : DEUX appels, username d'abord."""
    calls, _ = patched_slo
    await propagate_remote_revoke(
        async_session, "jdupont", [{"translator_slug": "test-app"}], settings
    )

    assert calls == [
        _revoke_url(test_app, "jmartin"),
        _revoke_url(test_app, "jdupont"),
    ]


@pytest.mark.asyncio
async def test_unknown_identifier_single_raw_call(
    async_session, test_app, settings: Settings, patched_slo
):
    """Identifiant introuvable en DB (legacy) : un seul appel, valeur brute."""
    calls, _ = patched_slo
    await propagate_remote_revoke(
        async_session, "ghost", [{"translator_slug": "test-app"}], settings
    )

    assert calls == [_revoke_url(test_app, "ghost")]


@pytest.mark.asyncio
async def test_no_translator_no_http_call(
    async_session, test_app, settings: Settings, patched_slo, renamed_user
):
    """Aucune entrée avec translator_slug → aucun appel HTTP."""
    calls, _ = patched_slo
    entries = [
        {"app_name": "Wiki", "translator_slug": None},
        {"app_name": "Autre"},
    ]
    await propagate_remote_revoke(async_session, "jdupont", entries, settings)

    assert calls == []


@pytest.mark.asyncio
async def test_http_500_is_best_effort(
    async_session, test_app, other_app, settings: Settings, patched_slo, renamed_user
):
    """HTTP 500 sur un traducteur : pas d'exception, les autres appels continuent."""
    calls, status_by_url = patched_slo
    for ident in ("jmartin", "jdupont"):
        status_by_url[_revoke_url(test_app, ident)] = 500

    entries = [{"translator_slug": "test-app"}, {"translator_slug": "other-app"}]
    await propagate_remote_revoke(async_session, "jdupont", entries, settings)

    assert calls == [
        _revoke_url(test_app, "jmartin"),
        _revoke_url(test_app, "jdupont"),
        _revoke_url(other_app, "jmartin"),
        _revoke_url(other_app, "jdupont"),
    ]
