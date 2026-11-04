# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests du precheck de déploiement (feat 2026-10-11).

Politique : bloquant strict — toute incertitude (API GitHub injoignable,
timeout…) est un fail. La modale de mise en place ne doit être proposée que
si `ready` est vrai, et le POST /deploy doit refuser (422) sinon.
"""
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.deploy_precheck import run_deploy_precheck
from app.models import AppDeployment, Application

settings = get_settings()
HUB_HOST = settings.get_hub_host()


async def _mk_app(async_session, slug="demo-app",
                  repo="https://github.com/acme/demo.git", api_port=9100):
    row = Application(name="Demo", slug=slug, api_url=f"http://h:{api_port}",
                      git_repo_url=repo)
    async_session.add(row)
    await async_session.commit()
    return row


def _gh_json(payload=None, status=200):
    return (payload if payload is not None else {"default_branch": "main"}, status)


def _mock_fetch_json(payload=None, status=200):
    """Mock async de fetch_github_json (le code fait `await`)."""
    async def fake(url, db=None):
        return _gh_json(payload, status)
    return fake


def _raw_dispatch(makefile=(None, 404), env=(None, 404), env_example=(None, 404)):
    """Dispatcher de fetch_github_raw_with_status selon le nom de fichier."""
    async def fake(url, db=None):
        if url.endswith("/Makefile"):
            return makefile
        if url.endswith("/.env"):
            return env
        if url.endswith("/.env.example"):
            return env_example
        return (None, 0)
    return fake


def _patch_env_ok(monkeypatch):
    """Environnement conteneur sain : outils présents, démon accessible."""
    monkeypatch.setattr("app.deploy_precheck._detect_tools",
                        lambda: [("git", True), ("make", True), ("docker compose", True)])
    monkeypatch.setattr("app.deploy_precheck._docker_daemon_status",
                        lambda: (True, ""))
    monkeypatch.setattr("app.deploy_precheck._apps_dir_writable",
                        lambda st: (True, ""))


@pytest.fixture(autouse=True)
def _no_docker_scans(monkeypatch):
    """Les scans Docker réels n'ont pas leur place dans ces tests."""
    monkeypatch.setattr("app.port_allocator.docker_scanner.get_used_host_ports",
                        lambda: set())
    monkeypatch.setattr("app.port_allocator.docker_scanner.get_used_network_subnets",
                        lambda: set())


def _check(checks, check_id):
    return next(c for c in checks if c["id"] == check_id)


@pytest.mark.asyncio
async def test_precheck_ready_nominal(async_session, monkeypatch):
    """Tout est réuni : ready=True, aucun fail."""
    await _mk_app(async_session)
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json", _mock_fetch_json())
    monkeypatch.setattr("app.deploy_precheck.fetch_github_raw_with_status",
                        _raw_dispatch(makefile=("build:\n\t@echo b\nup:\n\t@echo u\n", 200),
                                      env_example=("APP_SLUG=demo\n", 200)))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is True
    ids = {c["id"] for c in checks}
    assert {"git_url", "repo", "makefile", "env_file", "no_active_deploy",
            "ports_subnet", "tools", "docker_daemon", "apps_dir_writable"} <= ids
    assert all(c["status"] != "fail" for c in checks)


@pytest.mark.asyncio
async def test_precheck_fail_git_url_missing(async_session, monkeypatch):
    """Sans git_repo_url : fail immédiat, checks distants court-circuités."""
    await _mk_app(async_session, repo=None)
    _patch_env_ok(monkeypatch)

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is False
    assert _check(checks, "git_url")["status"] == "fail"
    assert _check(checks, "repo")["status"] == "skipped"


@pytest.mark.asyncio
async def test_precheck_fail_repo_404(async_session, monkeypatch):
    """Repo absent ou PAT sans accès : fail bloquant, fichiers non vérifiés."""
    await _mk_app(async_session)
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json",
                        _mock_fetch_json(None, 404))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is False
    assert _check(checks, "repo")["status"] == "fail"
    assert _check(checks, "makefile")["status"] == "skipped"


@pytest.mark.asyncio
async def test_precheck_fail_repo_api_unreachable_strict(async_session, monkeypatch):
    """Politique bloquant strict : API GitHub injoignable = fail, pas un warn."""
    await _mk_app(async_session)
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json",
                        _mock_fetch_json(None, 0))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is False
    assert _check(checks, "repo")["status"] == "fail"


@pytest.mark.asyncio
async def test_precheck_fail_makefile_missing(async_session, monkeypatch):
    await _mk_app(async_session)
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json", _mock_fetch_json())
    monkeypatch.setattr("app.deploy_precheck.fetch_github_raw_with_status",
                        _raw_dispatch(makefile=(None, 404),
                                      env_example=("X=1\n", 200)))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is False
    assert _check(checks, "makefile")["status"] == "fail"


@pytest.mark.asyncio
async def test_precheck_fail_makefile_missing_up_target(async_session, monkeypatch):
    """Un Makefile sans cible `up:` ne permet pas le déploiement automatique."""
    await _mk_app(async_session)
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json", _mock_fetch_json())
    monkeypatch.setattr("app.deploy_precheck.fetch_github_raw_with_status",
                        _raw_dispatch(makefile=("all:\n\t@echo x\n", 200),
                                      env_example=("X=1\n", 200)))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is False
    mk = _check(checks, "makefile")
    assert mk["status"] == "fail"
    assert "up" in mk["detail"]


@pytest.mark.asyncio
async def test_precheck_fail_env_file_missing(async_session, monkeypatch):
    """Ni .env ni .env.example à la racine : le Hub ne peut pas configurer l'app."""
    await _mk_app(async_session)
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json", _mock_fetch_json())
    monkeypatch.setattr("app.deploy_precheck.fetch_github_raw_with_status",
                        _raw_dispatch(makefile=("build:\nup:\n", 200)))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is False
    assert _check(checks, "env_file")["status"] == "fail"


@pytest.mark.asyncio
async def test_precheck_fail_active_deployment(async_session, monkeypatch):
    async_session.add(AppDeployment(app_slug="demo-app", status="running"))
    await async_session.commit()
    await _mk_app(async_session)
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json", _mock_fetch_json())
    monkeypatch.setattr("app.deploy_precheck.fetch_github_raw_with_status",
                        _raw_dispatch(makefile=("build:\nup:\n", 200),
                                      env_example=("X=1\n", 200)))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is False
    assert _check(checks, "no_active_deploy")["status"] == "fail"


@pytest.mark.asyncio
async def test_precheck_fail_subnet_pool_exhausted(async_session, monkeypatch):
    """Pool de sous-réseaux épuisé : le déploiement ne peut pas aboutir."""
    from app.port_allocator import SubnetPoolExhausted
    await _mk_app(async_session)
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json", _mock_fetch_json())
    monkeypatch.setattr("app.deploy_precheck.fetch_github_raw_with_status",
                        _raw_dispatch(makefile=("build:\nup:\n", 200),
                                      env_example=("X=1\n", 200)))
    monkeypatch.setattr("app.port_allocator.pick_free_subnet",
                        lambda *a, **k: (_ for _ in ()).throw(SubnetPoolExhausted("saturé")))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is False
    assert _check(checks, "ports_subnet")["status"] == "fail"


@pytest.mark.asyncio
async def test_precheck_fail_apps_dir_not_writable(async_session, monkeypatch):
    """Droits d'écriture APPS : test réel sur un chemin non inscriptible.
    (Seuls tools/daemon sont mockés — le check d'écriture est celui qu'on exerce.)"""
    await _mk_app(async_session)
    monkeypatch.setattr("app.deploy_precheck._detect_tools",
                        lambda: [("git", True), ("make", True), ("docker compose", True)])
    monkeypatch.setattr("app.deploy_precheck._docker_daemon_status",
                        lambda: (True, ""))
    monkeypatch.setattr(settings, "apps_host_path", "/proc/no-write-here", raising=False)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json", _mock_fetch_json())
    monkeypatch.setattr("app.deploy_precheck.fetch_github_raw_with_status",
                        _raw_dispatch(makefile=("build:\nup:\n", 200),
                                      env_example=("X=1\n", 200)))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is False
    assert _check(checks, "apps_dir_writable")["status"] == "fail"


@pytest.mark.asyncio
async def test_precheck_fail_tools_missing(async_session, monkeypatch):
    """Outils du conteneur : un `make` ou `docker compose` absent est bloquant."""
    await _mk_app(async_session)
    monkeypatch.setattr("app.deploy_precheck._detect_tools",
                        lambda: [("git", True), ("make", False), ("docker compose", True)])
    monkeypatch.setattr("app.deploy_precheck._docker_daemon_status",
                        lambda: (True, ""))
    monkeypatch.setattr("app.deploy_precheck._apps_dir_writable",
                        lambda st: (True, ""))
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json", _mock_fetch_json())
    monkeypatch.setattr("app.deploy_precheck.fetch_github_raw_with_status",
                        _raw_dispatch(makefile=("build:\nup:\n", 200),
                                      env_example=("X=1\n", 200)))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is False
    tools = _check(checks, "tools")
    assert tools["status"] == "fail"
    assert "make" in tools["detail"]


@pytest.mark.asyncio
async def test_precheck_non_github_repo_uses_ls_remote(async_session, monkeypatch):
    """Repo non-GitHub : ls-remote pour l'accès, fichiers en warn (pas de
    vérification possible sans clone), ready=True si le reste est bon."""
    await _mk_app(async_session, repo="git@gitlab.example.com:acme/demo.git")
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck._ls_remote_ok",
                        lambda url: (True, ""))

    ready, checks = await run_deploy_precheck(async_session, "demo-app", settings)

    assert ready is True
    assert _check(checks, "repo")["status"] == "ok"
    assert _check(checks, "makefile")["status"] == "warn"
    assert _check(checks, "env_file")["status"] == "warn"


@pytest.mark.asyncio
async def test_precheck_endpoint_requires_admin(client):
    response = await client.get("/api/apps/demo-app/deploy/precheck")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_precheck_endpoint_404_unknown(client, admin_token):
    from tests.conftest import get_auth_header
    response = await client.get("/api/apps/inconnue/deploy/precheck",
                                headers=get_auth_header(admin_token))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deploy_post_blocked_when_precheck_fails(client, admin_token, async_session, monkeypatch):
    """Garde en profondeur : le POST refuse (422) si le precheck échoue,
    et aucun déploiement n'est créé."""
    from tests.conftest import get_auth_header
    await _mk_app(async_session, slug="blocked-app")
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json",
                        _mock_fetch_json(None, 404))  # repo inaccessible

    resp = await client.post("/api/apps/blocked-app/deploy",
                             headers=get_auth_header(admin_token))
    assert resp.status_code == 422
    body = resp.json()
    assert body["ready"] is False
    assert any(c["id"] == "repo" and c["status"] == "fail" for c in body["checks"])

    rows = (await async_session.execute(
        select(AppDeployment).where(AppDeployment.app_slug == "blocked-app")
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_deploy_post_works_when_precheck_ok(client, admin_token, async_session, monkeypatch):
    """Le chemin nominal n'est pas cassé par la garde."""
    from tests.conftest import get_auth_header
    await _mk_app(async_session, slug="ok-app")
    _patch_env_ok(monkeypatch)
    monkeypatch.setattr("app.deploy_precheck.fetch_github_json", _mock_fetch_json())
    monkeypatch.setattr("app.deploy_precheck.fetch_github_raw_with_status",
                        _raw_dispatch(makefile=("build:\nup:\n", 200),
                                      env_example=("X=1\n", 200)))
    import app.routes.app_deploy as route_mod

    async def _fake_start(slug, triggered_by):
        return 77

    monkeypatch.setattr(route_mod, "start_deployment", _fake_start)

    resp = await client.post("/api/apps/ok-app/deploy",
                             headers=get_auth_header(admin_token))
    assert resp.status_code == 201
    assert resp.json()["deployment_id"] == 77
