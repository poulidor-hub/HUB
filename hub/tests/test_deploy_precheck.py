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


# ---- Précheck « image socle » (FROM poulidor/socle-api|web) ----
#
# Exécuté par le pipeline entre le clone/.env et `make build` (les Dockerfile
# lus sont ceux du build). Le démon est remplacé par `_docker_image_state`
# (mock au niveau module) : jamais de vrai docker dans ces tests.

import app.deploy_precheck as dp


def _mk_repo(tmp_path, files):
    """Dépôt tmp minimal : {chemin relatif: contenu}."""
    repo = tmp_path / "repo"
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return repo


def _mock_docker(monkeypatch, present=(), unknown=()):
    """Remplace `docker image inspect` : références déclarées présentes ou non
    vérifiables, absentes sinon. Retourne la liste des références inspectées."""
    inspected = []

    def fake(ref):
        inspected.append(ref)
        if ref in unknown:
            return "unknown"
        return "present" if ref in present else "absent"

    monkeypatch.setattr(dp, "_docker_image_state", fake)
    return inspected


def test_socle_image_hardcoded_tag_absent_is_blocking(tmp_path, monkeypatch):
    """`FROM poulidor/socle-api:2.40.2` en dur, image absente du Docker local :
    bloquant, image nommée, remède `./hubctl socle build`."""
    repo = _mk_repo(tmp_path, {"Dockerfile.backend": "FROM poulidor/socle-api:2.40.2\n"})
    inspected = _mock_docker(monkeypatch)

    status, detail = dp.check_socle_images(repo)

    assert status == "fail"
    assert "poulidor/socle-api:2.40.2" in detail
    assert "./hubctl socle build" in detail
    assert inspected == ["poulidor/socle-api:2.40.2"]


def test_socle_image_hardcoded_tag_present_is_ok(tmp_path, monkeypatch):
    repo = _mk_repo(tmp_path, {"Dockerfile.backend": "FROM poulidor/socle-api:2.40.2\n"})
    _mock_docker(monkeypatch, present=("poulidor/socle-api:2.40.2",))

    status, detail = dp.check_socle_images(repo)

    assert status == "ok"
    assert "poulidor/socle-api:2.40.2" in detail


def test_socle_image_socle_web_treated_like_socle_api(tmp_path, monkeypatch):
    """`poulidor/socle-web` (frontend) suit le même sort que `socle-api`."""
    repo = _mk_repo(tmp_path, {"Dockerfile.frontend": "FROM poulidor/socle-web:2.40.2\n"})
    inspected = _mock_docker(monkeypatch)

    status, detail = dp.check_socle_images(repo)

    assert status == "fail"
    assert "poulidor/socle-web:2.40.2" in detail
    assert "./hubctl socle build" in detail
    assert inspected == ["poulidor/socle-web:2.40.2"]


def test_socle_image_silent_without_socle_reference(tmp_path, monkeypatch):
    """App sans socle : précheck silencieux (ok sans détail), démon non interrogé."""
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": "FROM python:3.12-slim\n",
        "Dockerfile.frontend": "FROM node:20-alpine AS build\nFROM nginx:alpine\n",
        "Makefile": "build:\nup:\n",
    })
    inspected = _mock_docker(monkeypatch)

    status, detail = dp.check_socle_images(repo)

    assert (status, detail) == ("ok", "")
    assert inspected == []


def test_socle_image_arg_default_resolves_variable(tmp_path, monkeypatch):
    """Cas (3) : `ARG SOCLE_VERSION=2.36.1` déclaré avant le FROM → version résolue."""
    repo = _mk_repo(tmp_path, {"Dockerfile.backend": (
        "ARG SOCLE_VERSION=2.36.1\n"
        "FROM poulidor/socle-api:${SOCLE_VERSION}\n"
    )})
    inspected = _mock_docker(monkeypatch, present=("poulidor/socle-api:2.36.1",))

    status, detail = dp.check_socle_images(repo)

    assert status == "ok"
    assert inspected == ["poulidor/socle-api:2.36.1"]


def test_socle_image_variable_resolved_from_env_example(tmp_path, monkeypatch):
    """Cas (4) : variable sans ARG, clé du `.env.example` (forme durcie `:?`)."""
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": "FROM poulidor/socle-api:${APP_SOCLE_VERSION:?}\n",
        ".env.example": "APP_SLUG=demo\nAPP_SOCLE_VERSION=2.36.1\n",
    })
    inspected = _mock_docker(monkeypatch, present=("poulidor/socle-api:2.36.1",))

    status, detail = dp.check_socle_images(repo)

    assert status == "ok"
    assert inspected == ["poulidor/socle-api:2.36.1"]


def test_socle_image_unresolved_variable_warns_without_blocking(tmp_path, monkeypatch):
    """Cas (5) : version non résoluble (ARG sans défaut, clé absente du .env)
    → avertissement disant qu'on n'a pas pu vérifier, démon non interrogé."""
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": "ARG SOCLE_VERSION\nFROM poulidor/socle-api:${SOCLE_VERSION}\n",
        ".env.example": "APP_SLUG=demo\n",
    })
    inspected = _mock_docker(monkeypatch)

    status, detail = dp.check_socle_images(repo)

    assert status == "warn"
    assert "poulidor/socle-api" in detail and "SOCLE_VERSION" in detail
    assert inspected == []


def test_socle_image_dotenv_overrides_env_example(tmp_path, monkeypatch):
    """Le `.env` (écrit par le Hub, lu par compose et le Makefile) prime sur le `.env.example`."""
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": "FROM poulidor/socle-api:${APP_SOCLE_VERSION}\n",
        ".env.example": "APP_SOCLE_VERSION=2.36.1\n",
        ".env": 'APP_SOCLE_VERSION="2.40.2"\n',
    })
    inspected = _mock_docker(monkeypatch, present=("poulidor/socle-api:2.40.2",))

    status, detail = dp.check_socle_images(repo)

    assert status == "ok"
    assert inspected == ["poulidor/socle-api:2.40.2"]


def test_socle_image_stage_local_arg_does_not_feed_later_from(tmp_path, monkeypatch):
    """Sémantique Docker : un ARG déclaré APRÈS un FROM est local à l'étape ;
    un FROM ultérieur ne voit que les ARG globaux (sinon version fausse = faux blocage)."""
    repo = _mk_repo(tmp_path, {"Dockerfile.site": (
        "ARG SOCLE_VERSION=2.36.1\n"
        "FROM poulidor/socle-api:${SOCLE_VERSION} AS api\n"
        "ARG SOCLE_VERSION=9.9.9\n"
        "FROM poulidor/socle-web:${SOCLE_VERSION} AS web\n"
    )})
    inspected = _mock_docker(monkeypatch, present=("poulidor/socle-api:2.36.1",
                                                    "poulidor/socle-web:2.36.1"))

    status, detail = dp.check_socle_images(repo)

    assert status == "ok"
    assert inspected == ["poulidor/socle-api:2.36.1", "poulidor/socle-web:2.36.1"]


@pytest.mark.parametrize("tag, env_lines, want", [
    ("${SOCLE_VERSION}", ["SOCLE_VERSION=2.36.1"], "poulidor/socle-api:2.36.1"),
    ("$SOCLE_VERSION", ["SOCLE_VERSION=2.36.1"], "poulidor/socle-api:2.36.1"),
    ("${SOCLE_VERSION:?APP_SOCLE_VERSION manquant}", ["SOCLE_VERSION=2.36.1"],
     "poulidor/socle-api:2.36.1"),
    ("${SOCLE_VERSION:-2.36.1}", [], "poulidor/socle-api:2.36.1"),
    ("${SOCLE_VERSION:-2.36.1}", ["SOCLE_VERSION="], "poulidor/socle-api:2.36.1"),
    ("${SOCLE_VERSION:?}", [], None),
])
def test_socle_image_tag_forms(tmp_path, monkeypatch, tag, env_lines, want):
    """Formes de variable Dockerfile/compose : `$VAR`, `${VAR:?}`, défaut `:-`.
    `want=None` = non résoluble (démon non interrogé)."""
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": f"FROM poulidor/socle-api:{tag}\n",
        ".env.example": "\n".join(env_lines) + "\n",
    })
    inspected = _mock_docker(monkeypatch, present=(want,) if want else ())

    status, detail = dp.check_socle_images(repo)

    assert inspected == ([want] if want else [])
    assert status == ("ok" if want else "warn")


# Formes réelles du parc : `build.args` du compose en liste ou en mapping,
# `context` imbriqué, tag Compose `!override`.
_COMPOSE_LIST_ARGS = """\
services:
  api:
    build:
      context: .
      dockerfile: Dockerfile.backend
      args:
        - APP_VERSION=${APP_VERSION:-0.0.0}
        - SOCLE_VERSION=${APP_SOCLE_VERSION:?APP_SOCLE_VERSION manquant}
"""
_COMPOSE_MAP_ARGS = """\
services:
  api:
    build:
      context: ./backend
      dockerfile: Dockerfile
      args:
        SOCLE_VERSION: ${APP_SOCLE_VERSION:?APP_SOCLE_VERSION manquant}
    ports: !override
      - "${API_PORT}:8000"
"""


@pytest.mark.parametrize("compose, dockerfile", [
    (_COMPOSE_LIST_ARGS, "Dockerfile.backend"),
    (_COMPOSE_MAP_ARGS, "backend/Dockerfile"),
], ids=["liste", "mapping-context-imbrique"])
def test_socle_image_variable_resolved_from_compose_build_args(tmp_path, monkeypatch,
                                                               compose, dockerfile):
    """Cas (4), forme du parc : `ARG SOCLE_VERSION` sans défaut, version portée
    par `build.args` du compose (`SOCLE_VERSION=${APP_SOCLE_VERSION:?…}`)."""
    repo = _mk_repo(tmp_path, {
        dockerfile: "ARG SOCLE_VERSION\nFROM poulidor/socle-api:${SOCLE_VERSION}\n",
        "docker-compose.yml": compose,
        ".env.example": "APP_SLUG=demo\nAPP_SOCLE_VERSION=2.36.1\n",
    })
    inspected = _mock_docker(monkeypatch, present=("poulidor/socle-api:2.36.1",))

    status, detail = dp.check_socle_images(repo)

    assert status == "ok"
    assert inspected == ["poulidor/socle-api:2.36.1"]


def test_socle_image_compose_build_args_override_arg_default(tmp_path, monkeypatch):
    """Sémantique Docker : `--build-arg` (compose `build.args`) prime sur le
    défaut de l'ARG. L'app construit sur APP_SOCLE_VERSION (2.36.1), pas sur le
    défaut périmé du Dockerfile (2.34.12) — sinon faux blocage."""
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": "ARG SOCLE_VERSION=2.34.12\nFROM poulidor/socle-api:${SOCLE_VERSION}\n",
        "docker-compose.yml": _COMPOSE_LIST_ARGS,
        ".env.example": "APP_SOCLE_VERSION=2.36.1\n",
    })
    inspected = _mock_docker(monkeypatch, present=("poulidor/socle-api:2.36.1",))

    status, detail = dp.check_socle_images(repo)

    assert status == "ok"
    assert inspected == ["poulidor/socle-api:2.36.1"]


def test_socle_image_ignores_dockerfile_built_only_by_test_overlay(tmp_path, monkeypatch):
    """Seuls comptent les Dockerfiles construits par le compose de déploiement :
    un `Dockerfile.test` (overlay `docker-compose.test.yml`, `make test-docker`)
    sur un vieux socle ne doit pas bloquer un déploiement qui réussirait."""
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": "ARG SOCLE_VERSION\nFROM poulidor/socle-api:${SOCLE_VERSION}\n",
        "Dockerfile.test": "ARG SOCLE_VERSION=2.33.1\nFROM poulidor/socle-api:${SOCLE_VERSION}\n",
        "docker-compose.yml": _COMPOSE_LIST_ARGS,
        "docker-compose.test.yml": (
            "services:\n"
            "  test:\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: Dockerfile.test\n"
        ),
        ".env.example": "APP_SOCLE_VERSION=2.36.1\n",
    })
    inspected = _mock_docker(monkeypatch, present=("poulidor/socle-api:2.36.1",))

    status, detail = dp.check_socle_images(repo)

    assert status == "ok"
    assert inspected == ["poulidor/socle-api:2.36.1"]


def test_socle_image_lists_every_missing_image_and_version(tmp_path, monkeypatch):
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": "FROM poulidor/socle-api:2.36.1\n",
        "Dockerfile.frontend": "FROM poulidor/socle-web:2.40.2\n",
    })
    inspected = _mock_docker(monkeypatch)

    status, detail = dp.check_socle_images(repo)

    assert status == "fail"
    assert "poulidor/socle-api:2.36.1" in detail and "poulidor/socle-web:2.40.2" in detail
    assert "HUBsocle 2.36.1" in detail and "HUBsocle 2.40.2" in detail
    assert sorted(inspected) == ["poulidor/socle-api:2.36.1", "poulidor/socle-web:2.40.2"]


def test_socle_image_docker_unavailable_warns_without_blocking(tmp_path, monkeypatch):
    """Démon injoignable / droit refusé n'est pas « image absente » : jamais bloquant."""
    repo = _mk_repo(tmp_path, {"Dockerfile.backend": "FROM poulidor/socle-api:2.40.2\n"})
    _mock_docker(monkeypatch, unknown=("poulidor/socle-api:2.40.2",))

    status, detail = dp.check_socle_images(repo)

    assert status == "warn"
    assert "poulidor/socle-api:2.40.2" in detail


def test_socle_image_missing_image_dominates_unresolved_reference(tmp_path, monkeypatch):
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": "FROM poulidor/socle-api:2.40.2\n",
        "Dockerfile.worker": "ARG SOCLE_VERSION\nFROM poulidor/socle-api:${SOCLE_VERSION}\n",
    })
    _mock_docker(monkeypatch)

    status, detail = dp.check_socle_images(repo)

    assert status == "fail"
    assert "poulidor/socle-api:2.40.2" in detail


def test_socle_image_check_never_raises(tmp_path, monkeypatch):
    """Un précheck ne doit jamais casser un déploiement : erreur interne → warn."""
    repo = _mk_repo(tmp_path, {"Dockerfile.backend": "FROM poulidor/socle-api:2.40.2\n"})

    def boom(text):
        raise RuntimeError("analyse impossible")

    monkeypatch.setattr(dp, "_socle_froms", boom)

    status, detail = dp.check_socle_images(repo)

    assert status == "warn"


def test_socle_image_checks_compose_named_otherwise_than_docker_compose_yml(tmp_path, monkeypatch):
    """Le compose de déploiement ne s'appelle pas toujours `docker-compose.yml`
    (cas réel : `COMPOSE = docker compose -f docker-compose.admin.yml`, seul
    fichier à construire l'api, la base ne contenant que des `image:`) — ses
    builds doivent être vus."""
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": "ARG SOCLE_VERSION\nFROM poulidor/socle-api:${SOCLE_VERSION}\n",
        "docker-compose.yml": "services:\n  tool:\n    image: example/tool:latest\n",
        "docker-compose.admin.yml": _COMPOSE_LIST_ARGS,
        ".env.example": "APP_SOCLE_VERSION=2.36.1\n",
    })
    inspected = _mock_docker(monkeypatch, present=("poulidor/socle-api:2.36.1",))

    status, detail = dp.check_socle_images(repo)

    assert status == "ok"
    assert inspected == ["poulidor/socle-api:2.36.1"]


@pytest.mark.parametrize("overlay_build", [
    "    build:\n      context: .\n      dockerfile: Dockerfile.backend\n"
    "      args:\n        - APP_VERSION=1.2.3\n",
    # overlay qui ne redéfinirait que des args : compose lui fait hériter
    # contexte et Dockerfile du fichier de base
    "    build:\n      args:\n        - APP_VERSION=1.2.3\n",
], ids=["overlay-explicite", "overlay-heritant"])
def test_socle_image_merges_build_args_of_deploy_overlays(tmp_path, monkeypatch, overlay_build):
    """Compose complète les `build.args` d'un même service entre ses fichiers :
    un overlay de déploiement qui ne redéfinirait que APP_VERSION ne doit pas
    faire retomber SOCLE_VERSION sur le défaut (périmé) de l'ARG — faux blocage."""
    repo = _mk_repo(tmp_path, {
        "Dockerfile.backend": "ARG SOCLE_VERSION=2.34.12\nFROM poulidor/socle-api:${SOCLE_VERSION}\n",
        "docker-compose.yml": _COMPOSE_LIST_ARGS,
        "docker-compose.prod.yml": "services:\n  api:\n" + overlay_build,
        ".env.example": "APP_SOCLE_VERSION=2.36.1\n",
    })
    inspected = _mock_docker(monkeypatch, present=("poulidor/socle-api:2.36.1",))

    status, detail = dp.check_socle_images(repo)

    assert status == "ok"
    assert inspected == ["poulidor/socle-api:2.36.1"]


def test_socle_image_ignores_images_sharing_the_socle_name_prefix(tmp_path, monkeypatch):
    """Seules `poulidor/socle-api` et `poulidor/socle-web` comptent : une image
    dont le nom se contente de commencer pareil n'est pas une image socle."""
    repo = _mk_repo(tmp_path, {"Dockerfile.backend": (
        "FROM poulidor/socle-apix:1.0\n"
        "FROM poulidor/socle-api-web:2.0\n"
        "FROM poulidor/socle-apix\n"
    )})
    inspected = _mock_docker(monkeypatch)

    status, detail = dp.check_socle_images(repo)

    assert (status, detail) == ("ok", "")
    assert inspected == []


def test_socle_image_checks_digest_pinned_reference_as_written(tmp_path, monkeypatch):
    """`FROM poulidor/socle-api@sha256:…` : référence vérifiée telle quelle,
    sans version à nommer dans le remède."""
    ref = "poulidor/socle-api@sha256:0123456789abcdef"
    repo = _mk_repo(tmp_path, {"Dockerfile.backend": f"FROM {ref} AS base\n"})
    inspected = _mock_docker(monkeypatch)

    status, detail = dp.check_socle_images(repo)

    assert status == "fail"
    assert ref in detail
    assert "./hubctl socle build" in detail
    assert "()" not in detail
    assert inspected == [ref]


# ---- `_docker_image_state` réel : seul le sous-processus docker est remplacé ----

import subprocess


def test_docker_image_state_reads_inspect_outcome(monkeypatch):
    """Messages réels du CLI (Docker 29) : seul « No such image » dit
    « absente » ; démon injoignable ou refus du proxy restent « unknown »."""
    outcomes = [
        (0, "", "present"),
        (1, "Error response from daemon: No such image: poulidor/socle-api:2.40.2\n", "absent"),
        (1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
            "Is the docker daemon running?\n", "unknown"),
        (1, "Error response from daemon: 403 Forbidden\n", "unknown"),
    ]
    for returncode, stderr, want in outcomes:
        calls = []

        def fake_run(cmd, _rc=returncode, _err=stderr, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, _rc, stdout="", stderr=_err)

        monkeypatch.setattr(dp.subprocess, "run", fake_run)

        state = dp._docker_image_state("poulidor/socle-api:2.40.2")
        assert state == want, f"rc={returncode} : {stderr!r}"
        assert calls == [["docker", "image", "inspect", "poulidor/socle-api:2.40.2"]]


def test_docker_image_state_unknown_when_inspect_cannot_run(monkeypatch):
    """CLI absent ou inspect trop long : jamais assimilé à « absente »."""
    errors = [
        FileNotFoundError("[Errno 2] No such file or directory: 'docker'"),
        subprocess.TimeoutExpired(cmd=["docker", "image", "inspect"], timeout=15),
    ]
    for error in errors:
        def broken_run(cmd, _err=error, **kwargs):
            raise _err

        monkeypatch.setattr(dp.subprocess, "run", broken_run)

        assert dp._docker_image_state("poulidor/socle-api:2.40.2") == "unknown", repr(error)
