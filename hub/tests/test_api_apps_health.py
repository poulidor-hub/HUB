"""
Tests pour le dashboard de sante des apps (routes/apps_health.py).

Couvrez les comportements cles introduits par l'enrichissement du probe :
- ne pas s'arreter sur un 4xx et continuer vers le path suivant
- extraire la version (top-level ou via /api/health/detailed)
- recuperer les donnees riches (/detailed) : uptime, hub_connected, services_count
- statuts offline / degraded
"""
from urllib.parse import urlparse

from app.routes import apps_health


# ─── Mocks httpx ───────────────────────────────────────────────────────

class _MockResp:
    def __init__(self, status_code, json_data=None, reason="OK"):
        self.status_code = status_code
        self._json = json_data or {}
        self.reason_phrase = reason

    def json(self):  # httpx.Response.json() est SYNCHRONE
        return self._json


class _MockClient:
    """Mock httpx.AsyncClient : route par path HTTP exact."""

    def __init__(self, routes):
        # routes: dict {path_http: _MockResp}
        self.routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        path = urlparse(url).path
        if path in self.routes:
            return self.routes[path]
        return _MockResp(404, reason="Not Found")


class _ErrorClient:
    """Client dont toutes les requetes echouent (app hors ligne)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        raise ConnectionError("Connection refused")


def _patch_client(routes):
    """Patche _get_httpx_client pour retourner un _MockClient(routes)."""
    return _patch(apps_health, routes)


from unittest.mock import patch


def _patch(module, routes):
    return patch.object(module, "_get_httpx_client", lambda: _MockClient(routes))


async def _get_health(client, token):
    return await client.get(
        "/api/apps/health",
        headers={"Authorization": f"Bearer {token}"},
    )


def _find(data, slug):
    return next(a for a in data["apps"] if a["slug"] == slug)


# ─── Tests ─────────────────────────────────────────────────────────────

async def test_probe_extracts_version_from_api_health(client, admin_token, test_app):
    """App template : /api/health 200 avec version top-level -> online + version."""
    routes = {
        "/api/health": _MockResp(200, {"status": "healthy", "version": "1.2.3"}),
        "/api/health/detailed": _MockResp(404),
    }
    with _patch(apps_health, routes):
        resp = await _get_health(client, admin_token)

    assert resp.status_code == 200
    app = _find(resp.json(), "test-app")
    assert app["status"] == "online"
    assert app["version"] == "1.2.3"


async def test_probe_does_not_break_on_4xx(client, admin_token, test_app):
    """L'ancien bug : un 404 sur le 1er path court-circuitait tout.
    Desormais on continue vers le path suivant et on recupere la version."""
    routes = {
        "/api/health": _MockResp(404),
        "/health": _MockResp(200, {"status": "healthy", "version": "9.9.9"}),
        "/api/health/detailed": _MockResp(404),
    }
    with _patch(apps_health, routes):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["status"] == "online"
    assert app["version"] == "9.9.9"
    # L'app etant joignable avec un vrai endpoint, pas de note "no health"
    assert app["error"] is None


async def test_probe_collects_rich_data_from_detailed(client, admin_token, test_app):
    """Le /api/health/detailed fournit uptime, hub.connected, services.discovered_count."""
    routes = {
        "/api/health": _MockResp(200, {"status": "healthy", "version": "1.0.0"}),
        "/api/health/detailed": _MockResp(200, {
            "status": "healthy",
            "app": {"slug": "test-app", "name": "Test App", "version": "1.0.0"},
            "uptime_seconds": 7384.0,
            "hub": {"connected": True, "m2m_token_available": True},
            "services": {"discovered_count": 4},
        }),
    }
    with _patch(apps_health, routes):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["status"] == "online"
    assert app["version"] == "1.0.0"
    assert app["uptime_seconds"] == 7384.0
    assert app["hub_connected"] is True
    assert app["m2m_token_available"] is True
    assert app["services_count"] == 4


async def test_probe_version_fallback_from_detailed(client, admin_token, test_app):
    """Si /api/health n'a pas de version, on la recupere via detailed.app.version."""
    routes = {
        "/api/health": _MockResp(200, {"status": "healthy"}),  # pas de version
        "/api/health/detailed": _MockResp(200, {
            "app": {"slug": "test-app", "version": "2.5.1"},
            "hub": {"connected": True},
            "services": {"discovered_count": 0},
        }),
    }
    with _patch(apps_health, routes):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["version"] == "2.5.1"


async def test_probe_degraded_on_5xx(client, admin_token, test_app):
    """Un 500 sur tous les paths -> status degraded."""
    routes = {
        "/api/health": _MockResp(500, reason="Internal Server Error"),
        "/health": _MockResp(500, reason="Internal Server Error"),
        "/": _MockResp(500, reason="Internal Server Error"),
    }
    with _patch(apps_health, routes):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["status"] == "degraded"
    assert "500" in (app["error"] or "")


async def test_probe_offline_when_unreachable(client, admin_token, test_app):
    """Aucune reponse -> status offline + error."""
    with patch.object(apps_health, "_get_httpx_client", lambda: _ErrorClient()):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["status"] == "offline"
    assert app["error"] is not None


async def test_probe_4xx_only_is_online_without_version(client, admin_token, test_app):
    """Tous les paths en 404 (serveur joignable mais aucun endpoint sante) ->
    online, sans version, sans donnees riches."""
    routes = {
        "/api/health": _MockResp(404),
        "/health": _MockResp(404),
        "/": _MockResp(404),
        "/api/health/detailed": _MockResp(404),
    }
    with _patch(apps_health, routes):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["status"] == "online"
    assert app["version"] is None
    assert app["hub_connected"] is None
    assert app["services_count"] is None


async def test_summary_counts(client, admin_token, async_session):
    """Le resume compte correctement online/offline/degraded."""
    from app.models import Application
    app_online = Application(
        name="Online", slug="online", api_url="http://localhost:9001",
        is_active=True, requires_auth=True,
    )
    app_offline = Application(
        name="Offline", slug="offline", api_url="http://localhost:9002",
        is_active=True, requires_auth=True,
    )
    async_session.add_all([app_online, app_offline])
    await async_session.commit()

    online_routes = {
        "/api/health": _MockResp(200, {"status": "healthy", "version": "1.0"}),
        "/api/health/detailed": _MockResp(404),
    }

    call_count = {"n": 0}

    class _HybridClient:
        """localhost:9001 -> online, localhost:9002 -> error."""
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            if "9002" in url:
                raise ConnectionError("refused")
            return online_routes.get(urlparse(url).path, _MockResp(404))

    with patch.object(apps_health, "_get_httpx_client", lambda: _HybridClient()):
        resp = await _get_health(client, admin_token)

    summary = resp.json()["summary"]
    assert summary["online"] >= 1
    assert summary["offline"] >= 1
