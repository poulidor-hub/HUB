# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests pour le dashboard de sante des apps (routes/apps_health.py).

Couvrez les comportements cles introduits par l'enrichissement du probe :
- ne pas s'arreter sur un 4xx et continuer vers le path suivant
- extraire la version (top-level ou via /api/health/detailed)
- recuperer les donnees riches (/detailed) : uptime, hub_connected, services_count
- statuts offline / degraded
"""
from urllib.parse import urlparse

import pytest

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


def _container_state_mock(return_value):
    """Mock de get_container_state : retourne return_value et compte les appels."""
    calls = {"n": 0}

    def _state(slug):
        calls["n"] += 1
        return return_value

    return _state, calls


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
    """Aucune reponse -> status offline + error.
    Docker indisponible (None) : fallback offline, jamais stopped."""
    state_mock, _ = _container_state_mock(None)
    with patch.object(apps_health, "_get_httpx_client", lambda: _ErrorClient()), \
         patch.object(apps_health, "get_container_state", state_mock):
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

    with patch.object(apps_health, "_get_httpx_client", lambda: _HybridClient()), \
         patch.object(apps_health, "get_container_state", _container_state_mock(None)[0]):
        resp = await _get_health(client, admin_token)

    summary = resp.json()["summary"]
    assert summary["online"] >= 1
    assert summary["offline"] >= 1


# ─── Tests statut stopped (app stoppée vs vraie panne offline) ────────

async def test_probe_stopped_container_reports_stopped(client, admin_token, test_app):
    """Ping fail + conteneur 'exited' -> stopped, compté dans summary.stopped."""
    state_mock, calls = _container_state_mock("exited")
    with patch.object(apps_health, "_get_httpx_client", lambda: _ErrorClient()), \
         patch.object(apps_health, "get_container_state", state_mock):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["status"] == "stopped"
    summary = resp.json()["summary"]
    assert summary["offline"] == 0
    assert summary["stopped"] == 1
    assert calls["n"] == 1  # un seul appel Docker, sur la branche offline


async def test_probe_absent_container_reports_stopped(client, admin_token, test_app):
    """Docker joignable mais aucun conteneur -> stopped, 'Conteneur absent'."""
    state_mock, _ = _container_state_mock("absent")
    with patch.object(apps_health, "_get_httpx_client", lambda: _ErrorClient()), \
         patch.object(apps_health, "get_container_state", state_mock):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["status"] == "stopped"
    assert app["error"] == "Conteneur absent"


async def test_probe_running_but_unreachable_reports_offline(client, admin_token, test_app):
    """Conteneur 'running' mais ping fail -> vraie panne : offline (pas stopped)."""
    state_mock, _ = _container_state_mock("running")
    with patch.object(apps_health, "_get_httpx_client", lambda: _ErrorClient()), \
         patch.object(apps_health, "get_container_state", state_mock):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["status"] == "offline"
    summary = resp.json()["summary"]
    assert summary["offline"] == 1
    assert summary["stopped"] == 0


async def test_probe_docker_unavailable_reports_offline(client, admin_token, test_app):
    """Docker indisponible (None) -> incertitude : fallback offline, jamais stopped."""
    state_mock, _ = _container_state_mock(None)
    with patch.object(apps_health, "_get_httpx_client", lambda: _ErrorClient()), \
         patch.object(apps_health, "get_container_state", state_mock):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["status"] == "offline"
    assert resp.json()["summary"]["stopped"] == 0


async def test_probe_remote_app_reports_offline(client, admin_token, async_session):
    """App distante : pas d'appel Docker (get_container_state jamais appelé),
    status offline même si Docker dirait 'absent' (pas de conteneur local)."""
    from app.models import Application
    remote_app = Application(
        name="Remote App", slug="remote-app",
        api_url="http://remote.example.com:8000",
        is_active=True, requires_auth=True,
        is_remote=True,
    )
    async_session.add(remote_app)
    await async_session.commit()

    state_mock, calls = _container_state_mock("absent")
    with patch.object(apps_health, "_get_httpx_client", lambda: _ErrorClient()), \
         patch.object(apps_health, "get_container_state", state_mock):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "remote-app")
    assert app["status"] == "offline"
    assert calls["n"] == 0  # garde app distante : zéro appel Docker


# ─── Tests actions multi-conteneurs (groupe compose) ───────────────────

class _FakeActionContainer:
    """Conteneur docker-py fake : enregistre les actions et leurs kwargs."""

    def __init__(self, name, status="running", fail_on=None):
        self.name = name
        self.status = status
        self.fail_on = fail_on  # action qui doit lever (ex: "stop")
        self.calls = []  # [(action, kwargs)]

    def _do(self, action, kwargs):
        self.calls.append((action, kwargs))
        if self.fail_on == action:
            raise RuntimeError(f"docker {action} failed for {self.name}")
        self.status = "running" if action in ("start", "restart") else "exited"

    def start(self, **kwargs):
        self._do("start", kwargs)

    def stop(self, **kwargs):
        self._do("stop", kwargs)

    def restart(self, **kwargs):
        self._do("restart", kwargs)

    def reload(self):
        pass


def _fake_group(prefix="monapp", fail_on=None, fail_name=None):
    """Groupe 3 conteneurs (api, frontend, postgres), trié par nom."""
    group = [
        _FakeActionContainer(f"{prefix}-api"),
        _FakeActionContainer(f"{prefix}-frontend"),
        _FakeActionContainer(f"{prefix}-postgres", status="exited"),
    ]
    if fail_name:
        for c in group:
            if c.name == fail_name:
                c.fail_on = fail_on
    return group


def _patch_group(containers):
    """Patche find_containers_for_app sur le module route (convention du fichier)."""
    return patch.object(apps_health, "find_containers_for_app", lambda slug: containers)


async def _post_action(client, token, slug, action):
    return await client.post(
        f"/api/apps/{slug}/{action}",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.parametrize("action", ["stop", "start", "restart"])
async def test_action_targets_all_group_containers(
    client, admin_token, test_app, action
):
    """Stop/start/restart touchent TOUS les conteneurs du groupe (3/3),
    dans l'ordre stable (tri par nom), timeout=10 sur stop et restart."""
    group = _fake_group()
    with _patch_group(group):
        resp = await _post_action(client, admin_token, "test-app", action)

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert len(data["containers"]) == 3
    # Ordre stable : tri par nom
    assert [c["name"] for c in data["containers"]] == [
        "monapp-api", "monapp-frontend", "monapp-postgres",
    ]
    # Chaque conteneur a bien reçu l'action UNE fois
    for c in group:
        assert [a for a, _ in c.calls] == [action]
    # Stop/restart : timeout=10 ; start : sans timeout
    for c in group:
        kwargs = c.calls[0][1]
        if action in ("stop", "restart"):
            assert kwargs == {"timeout": 10}
        else:
            assert kwargs == {}

    # Champs rétrocompat : 1er conteneur (tri par nom)
    assert data.get("container_name") in (None, "monapp-api")
    if action == "stop":
        assert data["message"].startswith("Arret de Test App reussi (3/3")
    elif action == "start":
        assert data["message"].startswith("Demarrage de Test App reussi (3/3")


async def test_restart_response_retrocompat_fields(client, admin_token, test_app):
    """RestartResponse garde container_name/status (1er conteneur du groupe)
    + containers[] enrichi."""
    group = _fake_group()
    with _patch_group(group):
        resp = await _post_action(client, admin_token, "test-app", "restart")

    data = resp.json()
    assert data["ok"] is True
    assert data["container_name"] == "monapp-api"
    assert data["status"] == "running"  # état après restart+reload
    assert [c["name"] for c in data["containers"]] == [
        "monapp-api", "monapp-frontend", "monapp-postgres",
    ]
    assert all(c["ok"] for c in data["containers"])
    assert all(c["state"] == "running" for c in data["containers"])


async def test_stop_partial_failure_aggregates(client, admin_token, test_app):
    """Un échec sur un conteneur n'interrompt pas les autres : ok global False,
    message « partiel (2/3) », détail porté par le conteneur fautif."""
    group = _fake_group(fail_on="stop", fail_name="monapp-frontend")
    with _patch_group(group):
        resp = await _post_action(client, admin_token, "test-app", "stop")

    assert resp.status_code == 200  # pas de 500 : le résultat est structuré
    data = resp.json()
    assert data["ok"] is False
    assert "partiel de Test App (2/3)" in data["message"]
    assert data["detail"] == "docker stop failed for monapp-frontend"

    by_name = {c["name"]: c for c in data["containers"]}
    assert by_name["monapp-frontend"]["ok"] is False
    assert by_name["monapp-frontend"]["detail"] == "docker stop failed for monapp-frontend"
    # Les deux autres ont bien été stoppés malgré l'échec
    assert by_name["monapp-api"]["ok"] is True
    assert by_name["monapp-api"]["state"] == "exited"
    assert by_name["monapp-postgres"]["ok"] is True
    for c in group:
        assert len(c.calls) == 1  # chacun actionné exactement une fois


@pytest.mark.parametrize("action", ["stop", "start", "restart"])
async def test_action_group_empty_returns_404(client, admin_token, test_app, action):
    """Groupe vide (aucun conteneur matché) → 404, message conservé."""
    with _patch_group([]):
        resp = await _post_action(client, admin_token, "test-app", action)

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert f"Aucun conteneur Docker trouve pour 'test-app'" in detail
    assert f"APP_SLUG=test-app" in detail


# ─── Tests containers_count (comptage batch du dashboard) ─────────────

async def test_health_item_containers_count_batch(client, admin_token, test_app):
    """containers_count rempli via count_containers_batch (UN appel pour tout
    le dashboard), renvoyé tel quel dans l'item."""
    batch_calls = {"n": 0}

    def _batch(slugs):
        batch_calls["n"] += 1
        return {slug: 3 for slug in slugs}

    routes = {
        "/api/health": _MockResp(200, {"status": "healthy", "version": "1.0.0"}),
        "/api/health/detailed": _MockResp(404),
    }
    with patch.object(apps_health, "_get_httpx_client", lambda: _MockClient(routes)), \
         patch.object(apps_health, "get_container_state", _container_state_mock(None)[0]), \
         patch.object(apps_health, "count_containers_batch", _batch):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["containers_count"] == 3
    assert batch_calls["n"] == 1  # UNE SEULE liste Docker pour le dashboard


async def test_health_item_containers_count_docker_unavailable(client, admin_token, test_app):
    """Docker indisponible (batch → None) : containers_count reste None."""
    def _batch(slugs):
        return {slug: None for slug in slugs}

    routes = {
        "/api/health": _MockResp(200, {"status": "healthy"}),
        "/api/health/detailed": _MockResp(404),
    }
    with patch.object(apps_health, "_get_httpx_client", lambda: _MockClient(routes)), \
         patch.object(apps_health, "get_container_state", _container_state_mock(None)[0]), \
         patch.object(apps_health, "count_containers_batch", _batch):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "test-app")
    assert app["containers_count"] is None


async def test_health_item_containers_count_remote_app_none(client, admin_token, async_session):
    """App distante : jamais comptée (batch non interrogé pour son slug)."""
    from app.models import Application
    remote_app = Application(
        name="Remote App", slug="remote-app",
        api_url="http://remote.example.com:8000",
        is_active=True, requires_auth=True,
        is_remote=True,
    )
    async_session.add(remote_app)
    await async_session.commit()

    seen_slugs = []

    def _batch(slugs):
        seen_slugs.extend(slugs)
        return {slug: 1 for slug in slugs}

    class _ErrorOrOkClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise ConnectionError("refused")

    with patch.object(apps_health, "_get_httpx_client", lambda: _ErrorOrOkClient()), \
         patch.object(apps_health, "get_container_state", _container_state_mock(None)[0]), \
         patch.object(apps_health, "count_containers_batch", _batch):
        resp = await _get_health(client, admin_token)

    app = _find(resp.json(), "remote-app")
    assert app["containers_count"] is None
    assert "remote-app" not in seen_slugs  # app distante exclue du batch


# ─── Tests releases / diff (GitHub API) ────────────────────────────────

async def _create_github_app(session, slug="gh-app"):
    """Cree une app avec git_repo_url configure."""
    from app.models import Application
    app = Application(
        name="GitHub App", slug=slug,
        api_url="http://localhost:9999",
        git_repo_url="https://github.com/test-org/test-repo.git",
        is_active=True, requires_auth=True,
    )
    session.add(app)
    await session.commit()
    return app


async def test_releases_returns_parsed_list(client, admin_token, async_session):
    """L'endpoint /releases parse correctement les releases GitHub."""
    from unittest.mock import AsyncMock
    await _create_github_app(async_session)

    mock_releases = [
        {"tag_name": "v1.2.0", "name": "Release 1.2", "published_at": "2026-07-20T10:00:00Z",
         "body": "### Notes\n- Fix A\n- Add B", "html_url": "https://github.com/test-org/test-repo/releases/tag/v1.2.0"},
        {"tag_name": "v1.1.0", "name": "Release 1.1", "published_at": "2026-06-15T10:00:00Z",
         "body": "First stable", "html_url": "https://github.com/test-org/test-repo/releases/tag/v1.1.0"},
    ]
    with patch.object(apps_health, "fetch_github_json", new_callable=AsyncMock, return_value=(mock_releases, 200)):
        resp = await client.get(
            "/api/apps/gh-app/releases",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    data = resp.json()
    assert data["ok"] is True
    assert data["repo_configured"] is True
    assert len(data["releases"]) == 2
    assert data["releases"][0]["tag"] == "1.2.0"  # prefix v stripped
    assert data["releases"][0]["body"] == "### Notes\n- Fix A\n- Add B"
    assert data["releases"][1]["tag"] == "1.1.0"


async def test_releases_no_repo_configured(client, admin_token, test_app):
    """Sans git_repo_url, retourne repo_configured=False."""
    resp = await client.get(
        "/api/apps/test-app/releases",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = resp.json()
    assert data["ok"] is False
    assert data["repo_configured"] is False


async def test_diff_returns_parsed_files(client, admin_token, async_session):
    """L'endpoint /diff parse les fichiers modifies + patches."""
    from unittest.mock import AsyncMock
    await _create_github_app(async_session)

    mock_compare = {
        "total_commits": 3,
        "html_url": "https://github.com/test-org/test-repo/compare/v1.1.0...v1.2.0",
        "files": [
            {"filename": "main.py", "status": "modified", "additions": 5, "deletions": 2,
             "patch": "@@ -1,3 +1,6 @@\n import os\n+import sys\n def main():\n-    pass\n+    return 0"},
            {"filename": "README.md", "status": "added", "additions": 10, "deletions": 0,
             "patch": None},  # gros fichier, pas de patch
        ],
    }
    with patch.object(apps_health, "fetch_github_json", new_callable=AsyncMock, return_value=(mock_compare, 200)):
        resp = await client.get(
            "/api/apps/gh-app/diff?from=v1.1.0&to=v1.2.0",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    data = resp.json()
    assert data["ok"] is True
    assert data["total_commits"] == 3
    assert len(data["files"]) == 2
    assert data["files"][0]["filename"] == "main.py"
    assert data["files"][0]["additions"] == 5
    assert data["files"][0]["patch"] is not None
    assert "+import sys" in data["files"][0]["patch"]
    assert data["files"][1]["filename"] == "README.md"
    assert data["files"][1]["patch"] is None  # tronque par GitHub


async def test_diff_no_repo_configured(client, admin_token, test_app):
    """Sans git_repo_url, le diff retourne ok=False."""
    resp = await client.get(
        "/api/apps/test-app/diff?from=1.0&to=1.1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = resp.json()
    assert data["ok"] is False
