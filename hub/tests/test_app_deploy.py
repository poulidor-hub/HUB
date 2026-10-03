"""Tests du déploiement automatique des apps (spec 2026-08-18-auto-deploy-apps)."""
import pytest
from sqlalchemy import select

from app.models import AppDeployment

from pathlib import Path

from app.deploy_service import (
    apply_env_values,
    build_env_values,
    generate_secrets,
)


def _write_env(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(content)
    return p


def test_build_env_values(tmp_path):
    vals = build_env_values(slug="exemple-app", api_port=9002, frontend_port=4204, hub_host="192.0.2.10")
    assert vals["APP_SLUG"] == "exemple-app"
    assert vals["API_PORT"] == "9002"
    assert vals["FRONTEND_PORT"] == "4204"
    assert vals["FRONTEND_HTTPS_PORT"] == "4205"
    assert vals["HUB_HOST"] == "192.0.2.10"
    assert vals["APP_HOST"] == ""


def test_apply_env_replaces_all_variants(tmp_path):
    env = _write_env(tmp_path, "\n".join([
        "APP_SLUG=old",
        "APP_PORT=1111",
        "APP_PORT_API=2222",
        "FRONTEND_PORT=3333",
        "FRONTEND_HTTPS_PORT=3334",
        "HUB_URL=http://1.2.3.4:8000",
        "SERVER_IP=1.2.3.4",
        "HOST_IP=1.2.3.4",
        "APP_HOST=5.6.7.8",
        "POSTGRES_PASSWORD=old_pwd",
        "DATABASE_URL=postgresql://app:old_pwd@db:5432/app",
    ]))
    apply_env_values(env, {
        "APP_SLUG": "exemple-app",
        "API_PORT": "9002",
        "FRONTEND_PORT": "4204",
        "FRONTEND_HTTPS_PORT": "4205",
        "HUB_HOST": "192.0.2.10",
        "APP_HOST": "",
        "POSTGRES_PASSWORD": "new_pwd",
    })
    text = env.read_text()
    assert "APP_SLUG=exemple-app" in text
    assert "APP_PORT=9002" in text
    assert "APP_PORT_API=9002" in text
    assert "FRONTEND_PORT=4204" in text
    assert "FRONTEND_HTTPS_PORT=4205" in text
    assert "HUB_URL=192.0.2.10" in text
    assert "SERVER_IP=" in text and "SERVER_IP=1.2.3.4" not in text
    assert "HOST_IP=" in text and "HOST_IP=1.2.3.4" not in text
    assert "APP_HOST=" in text and "APP_HOST=5.6.7.8" not in text
    # DATABASE_URL reconstruite avec le mot de passe généré
    assert "postgresql://app:new_pwd@db:5432/app" in text


def test_apply_env_ensure_appends_missing_canonical_keys(tmp_path):
    env = _write_env(tmp_path, "UNRELATED=1\n")
    apply_env_values(
        env,
        {"APP_SLUG": "x", "API_PORT": "9000", "FRONTEND_PORT": "4200",
         "HUB_HOST": "h", "APP_HOST": ""},
        ensure=["APP_SLUG", "API_PORT", "FRONTEND_PORT", "HUB_HOST", "APP_HOST"],
    )
    text = env.read_text()
    assert "UNRELATED=1" in text
    assert "APP_SLUG=x" in text
    assert "API_PORT=9000" in text
    assert "FRONTEND_PORT=4200" in text
    assert "HUB_HOST=h" in text


def test_apply_env_preserves_existing_secrets_when_not_provided(tmp_path):
    env = _write_env(tmp_path, "POSTGRES_PASSWORD=keep_me\n")
    apply_env_values(env, {"APP_SLUG": "x"})
    assert "POSTGRES_PASSWORD=keep_me" in env.read_text()


def test_apply_env_https_port_not_appended_if_absent(tmp_path):
    env = _write_env(tmp_path, "FRONTEND_PORT=4200\n")
    apply_env_values(env, {"FRONTEND_HTTPS_PORT": "4201"})
    assert "FRONTEND_HTTPS_PORT" not in env.read_text()


def test_build_env_values_forces_hub_http():
    """Le port API du HUB est HTTP dans ce parc → HUB_HTTPS_ENABLED forcé à false."""
    vals = build_env_values(slug="x", api_port=9000, frontend_port=4200, hub_host="192.0.2.10")
    assert vals["HUB_HTTPS_ENABLED"] == "false"


def test_apply_env_replaces_hub_https_enabled_true(tmp_path):
    """Un .env avec HUB_HTTPS_ENABLED=true (ex: exemple-app) est corrigé à false."""
    env = _write_env(tmp_path, "HUB_HTTPS_ENABLED=true\nAPP_SLUG=x\n")
    apply_env_values(env, build_env_values(slug="x", api_port=9000, frontend_port=4200, hub_host="h"))
    assert "HUB_HTTPS_ENABLED=false" in env.read_text()


def test_apply_env_ensures_hub_https_enabled_present(tmp_path):
    """Sans la clé dans le .env, elle est ajoutée (explicit > default)."""
    env = _write_env(tmp_path, "APP_SLUG=x\n")
    apply_env_values(env, build_env_values(slug="x", api_port=9000, frontend_port=4200, hub_host="h"))
    assert "HUB_HTTPS_ENABLED=false" in env.read_text()


def test_apply_env_appends_app_subnet(tmp_path):
    """Parité hubctl _write_app_env : APP_SUBNET écrit dès le déploiement.
    Sans lui, `make build` échoue sur ${APP_SUBNET:?} avant que le sync-env
    de l'app n'ait pu intervenir (apps dont le .env.example omet la clé)."""
    env = _write_env(tmp_path, "UNRELATED=1\n")
    apply_env_values(env, {"APP_SUBNET": "10.50.0.42/24"})
    assert "APP_SUBNET=10.50.0.42/24" in env.read_text()


def test_apply_env_appends_secrets_encryption_key(tmp_path):
    """Parité hubctl : la clé Fernet générée est écrite même si absente du
    .env.example (bug : générée puis perdue, jamais persistée)."""
    env = _write_env(tmp_path, "UNRELATED=1\n")
    apply_env_values(env, {"SECRETS_ENCRYPTION_KEY": "fernet-key-123"})
    assert "SECRETS_ENCRYPTION_KEY=fernet-key-123" in env.read_text()


def test_generate_secrets(tmp_path):
    s1 = generate_secrets()
    s2 = generate_secrets()
    assert s1["POSTGRES_PASSWORD"] != s2["POSTGRES_PASSWORD"]
    assert len(s1["POSTGRES_PASSWORD"]) >= 24
    assert s1["SECRETS_ENCRYPTION_KEY"] != s2["SECRETS_ENCRYPTION_KEY"]


# ---- Worker : fakes subprocess ----

import asyncio

from app.models import AppDeployment, Application, PortAllocation


class _CtxSession:
    """Wrapper : _run_deployment fait `async with factory() as db`."""
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class _FakeProc:
    def __init__(self, returncode=0, out=b""):
        self.returncode = None
        self._returncode = returncode
        self._out = out

    async def communicate(self):
        self.returncode = self._returncode
        return self._out, b""

    def kill(self):
        pass


def _install_fake_exec(monkeypatch, results):
    """results: liste de (returncode, stdout bytes) consommée dans l'ordre."""
    calls = []
    queue = list(results)

    async def _fake_exec(*cmd, cwd=None, env=None, stdout=None, stderr=None):
        calls.append({"cmd": cmd, "cwd": str(cwd), "env": env})
        rc, out = queue.pop(0) if queue else (0, b"")
        return _FakeProc(rc, out)

    import app.deploy_service as ds
    monkeypatch.setattr(ds.asyncio, "create_subprocess_exec", _fake_exec)
    return calls


async def _mk_deployment(async_session, slug="exemple-app", repo="https://github.com/o/r.git"):
    app_row = Application(name="exemple-app", slug=slug, api_url=f"http://h:9002",
                          git_repo_url=repo)
    async_session.add(app_row)
    alloc = PortAllocation(app_slug=slug, host="192.0.2.10", api_port=9002,
                           frontend_port=4204, frontend_https_port=4205,
                           subnet="10.50.0.7/24")
    async_session.add(alloc)
    dep = AppDeployment(app_slug=slug, status="pending")
    async_session.add(dep)
    await async_session.commit()
    return dep.id


@pytest.mark.asyncio
async def test_run_deployment_success(async_session, monkeypatch, tmp_path):
    """Pipeline complet : clone -> .env créé+injecté -> compose build -> up -> health OK."""
    import app.deploy_service as ds

    dep_id = await _mk_deployment(async_session)
    appdir = tmp_path / "exemple-app"
    appdir.mkdir()
    (appdir / "Makefile").write_text("build:\n\t@echo build\nup:\n\t@echo up\n")  # app conforme template
    (appdir / ".env.example").write_text(
        "APP_SLUG=old\nAPP_PORT=1\nPOSTGRES_PASSWORD=?\n"
    )

    calls = _install_fake_exec(monkeypatch, [(0, b"clone ok"), (0, b""), (0, b"")])
    monkeypatch.setattr(ds, "_wait_healthy", lambda *a, **k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))

    factory = lambda: _CtxSession(async_session)
    await ds._run_deployment(dep_id, "exemple-app", session_factory=factory)

    cmds = [c["cmd"] for c in calls]
    assert any("clone" in c for c in cmds)          # git clone (ou pull)
    assert any("build" in " ".join(c) for c in cmds)
    env_text = (tmp_path / "exemple-app" / ".env").read_text()
    assert "APP_SLUG=exemple-app" in env_text
    assert "APP_PORT=9002" in env_text
    # Placeholder '?' : jamais produit par token_urlsafe (alphabet [A-Za-z0-9_-]),
    # sinon un secret régénéré commençant par le placeholder fausse l'assertion (~1/64).
    assert "POSTGRES_PASSWORD=?" not in env_text     # secret régénéré à la création

    dep = await async_session.get(AppDeployment, dep_id)
    await async_session.refresh(dep)
    assert dep.status == "success"
    assert dep.finished_at is not None


@pytest.mark.asyncio
async def test_run_deployment_clone_failure(async_session, monkeypatch, tmp_path):
    import app.deploy_service as ds

    dep_id = await _mk_deployment(async_session)
    tmp_path.mkdir(parents=True, exist_ok=True)
    _install_fake_exec(monkeypatch, [(128, b"fatal: repository not found")])
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))

    await ds._run_deployment(dep_id, "exemple-app", session_factory=lambda: _CtxSession(async_session))

    dep = await async_session.get(AppDeployment, dep_id)
    await async_session.refresh(dep)
    assert dep.status == "failed"
    assert "repository not found" in (dep.logs or "")


@pytest.mark.asyncio
async def test_run_deployment_env_preserved_on_redeploy(async_session, monkeypatch, tmp_path):
    """Re-déploiement : .env existant conservé, secrets non régénérés."""
    import app.deploy_service as ds

    dep_id = await _mk_deployment(async_session)
    appdir = tmp_path / "exemple-app"
    appdir.mkdir()
    (appdir / ".env.example").write_text("APP_PORT=1\nPOSTGRES_PASSWORD=x\n")
    (appdir / ".env").write_text("APP_SLUG=old\nAPP_PORT=1\nPOSTGRES_PASSWORD=keep\n")
    (appdir / "Makefile").write_text("build:\n\t@echo build\nup:\n\t@echo up\n")  # app conforme template
    (appdir / ".git").mkdir()  # dir existante -> pull, pas clone

    calls = _install_fake_exec(monkeypatch, [(0, b"pull"), (0, b""), (0, b"")])
    monkeypatch.setattr(ds, "_wait_healthy", lambda *a, **k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))

    await ds._run_deployment(dep_id, "exemple-app", session_factory=lambda: _CtxSession(async_session))

    assert any("pull" in " ".join(c["cmd"]) for c in calls)
    env_text = (appdir / ".env").read_text()
    assert "POSTGRES_PASSWORD=keep" in env_text
    assert "APP_SLUG=exemple-app" in env_text


# ---- Alignement sur la voie manuelle / hubctl (parité _write_app_env) ----

@pytest.mark.asyncio
async def test_run_deployment_pull_uses_ff_only(async_session, monkeypatch, tmp_path):
    """Parité action UPDATE : `git pull --ff-only`, pas de merge surprise."""
    import app.deploy_service as ds

    dep_id = await _mk_deployment(async_session)
    appdir = tmp_path / "exemple-app"
    appdir.mkdir()
    (appdir / "Makefile").write_text("build:\nup:\n")
    (appdir / ".env.example").write_text("APP_SLUG=old\n")
    (appdir / ".env").write_text("APP_SLUG=old\n")
    (appdir / ".git").mkdir()  # dir existante -> pull

    calls = _install_fake_exec(monkeypatch, [(0, b"pull"), (0, b""), (0, b"")])
    monkeypatch.setattr(ds, "_wait_healthy", lambda *a, **k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))

    await ds._run_deployment(dep_id, "exemple-app", session_factory=lambda: _CtxSession(async_session))

    pulls = [c for c in calls if "pull" in c["cmd"]]
    assert pulls, "la mise à jour doit passer par git pull"
    assert any("--ff-only" in c["cmd"] for c in pulls)


@pytest.mark.asyncio
async def test_run_deployment_writes_app_subnet(async_session, monkeypatch, tmp_path):
    """Le subnet alloué par le hub est écrit dans le .env (parité hubctl),
    y compris quand le .env.example omet la clé."""
    import app.deploy_service as ds

    dep_id = await _mk_deployment(async_session)
    appdir = tmp_path / "exemple-app"
    appdir.mkdir()
    (appdir / "Makefile").write_text("build:\nup:\n")
    (appdir / ".env.example").write_text("APP_SLUG=old\n")  # pas d'APP_SUBNET

    calls = _install_fake_exec(monkeypatch, [(0, b"clone"), (0, b""), (0, b"")])
    monkeypatch.setattr(ds, "_wait_healthy", lambda *a, **k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))

    await ds._run_deployment(dep_id, "exemple-app", session_factory=lambda: _CtxSession(async_session))

    env_text = (appdir / ".env").read_text()
    assert "APP_SUBNET=10.50.0.7/24" in env_text


@pytest.mark.asyncio
async def test_run_deployment_env_chmod_600(async_session, monkeypatch, tmp_path):
    """Parité hubctl : le .env est restreint à 0600 (secrets en clair)."""
    import os
    import app.deploy_service as ds

    dep_id = await _mk_deployment(async_session)
    appdir = tmp_path / "exemple-app"
    appdir.mkdir()
    (appdir / "Makefile").write_text("build:\nup:\n")
    (appdir / ".env.example").write_text("APP_SLUG=old\n")
    env_path = appdir / ".env"
    env_path.write_text("APP_SLUG=old\n")
    os.chmod(env_path, 0o644)  # chmod relâché avant re-déploiement
    (appdir / ".git").mkdir()

    calls = _install_fake_exec(monkeypatch, [(0, b"pull"), (0, b""), (0, b"")])
    monkeypatch.setattr(ds, "_wait_healthy", lambda *a, **k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))

    await ds._run_deployment(dep_id, "exemple-app", session_factory=lambda: _CtxSession(async_session))

    assert (env_path.stat().st_mode & 0o777) == 0o600


@pytest.mark.asyncio
async def test_run_deployment_build_uses_allowlisted_env(async_session, monkeypatch, tmp_path):
    """Jamais l'env du conteneur hub dans le make/compose de l'app (parité
    atelier/recreate) : allowlist seulement — sinon les ${VAR} du compose de
    l'app sont écrasées par des clés hub homonymes (ex. HUB_HTTPS_ENABLED)."""
    import app.deploy_service as ds

    dep_id = await _mk_deployment(async_session)
    appdir = tmp_path / "exemple-app"
    appdir.mkdir()
    (appdir / "Makefile").write_text("build:\nup:\n")
    (appdir / ".env.example").write_text("APP_SLUG=old\n")
    (appdir / ".env").write_text("APP_SLUG=old\n")
    (appdir / ".git").mkdir()

    calls = _install_fake_exec(monkeypatch, [(0, b"pull"), (0, b""), (0, b"")])
    monkeypatch.setattr(ds, "_wait_healthy", lambda *a, **k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))
    monkeypatch.setenv("HUB_HTTPS_ENABLED", "true")  # poison : clé hub homonyme

    await ds._run_deployment(dep_id, "exemple-app", session_factory=lambda: _CtxSession(async_session))

    builds = [c for c in calls if "build" in c["cmd"]]
    ups = [c for c in calls if any("up" == part for part in c["cmd"])]
    assert builds and ups
    for c in builds + ups:
        assert c["env"] is not None
        assert "HUB_HTTPS_ENABLED" not in c["env"], \
            "l'env du hub ne doit pas fuiter dans le make/compose de l'app"
        assert "PATH" in c["env"], "l'allowlist doit laisser passer l'essentiel"


@pytest.mark.asyncio
async def test_run_deployment_fails_explicitly_without_makefile(async_session, monkeypatch, tmp_path):
    """App sans Makefile (non conforme template) : échec explicite, jamais un
    `docker compose` nu sans sync-env (APP_SUBNET manquant, pas d'overlay HTTPS)."""
    import app.deploy_service as ds

    dep_id = await _mk_deployment(async_session)
    appdir = tmp_path / "exemple-app"
    appdir.mkdir()  # pas de Makefile
    (appdir / ".env.example").write_text("APP_SLUG=old\n")

    calls = _install_fake_exec(monkeypatch, [(0, b"clone")])
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))

    await ds._run_deployment(dep_id, "exemple-app", session_factory=lambda: _CtxSession(async_session))

    dep = await async_session.get(AppDeployment, dep_id)
    await async_session.refresh(dep)
    assert dep.status == "failed"
    assert "Makefile" in (dep.logs or "")
    assert len(calls) == 1, "aucun build/up ne doit être tenté sans Makefile"


@pytest.mark.asyncio
async def test_has_active_deployment_for(async_session):
    """Le conflit ne vise que la même app (pool de déploiements, spec 2026-09-17)."""
    from app.deploy_service import has_active_deployment_for

    assert not await has_active_deployment_for(async_session, "exemple-app")
    async_session.add(AppDeployment(app_slug="a", status="running"))
    await async_session.commit()
    assert await has_active_deployment_for(async_session, "a")
    assert not await has_active_deployment_for(async_session, "exemple-app")


# ---- Endpoints ----

from app.auth import create_access_token
from tests.conftest import get_auth_header


@pytest.fixture
async def app_with_repo(async_session):
    from app.models import Application as A
    row = A(name="exemple-app", slug="exemple-app", api_url="http://h:9002",
            git_repo_url="https://github.com/Poutchouli/exemple-app.git")
    async_session.add(row)
    await async_session.commit()
    return row


@pytest.mark.asyncio
async def test_deploy_requires_admin(client, test_user, app_with_repo):
    token, _, _ = create_access_token(username=test_user.username, apps={})
    r = await client.post("/api/apps/exemple-app/deploy", headers=get_auth_header(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_deploy_404_unknown(client, admin_token):
    r = await client.post("/api/apps/inconnue/deploy", headers=get_auth_header(admin_token))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_deploy_400_without_repo(client, admin_token, async_session):
    from app.models import Application as A
    async_session.add(A(name="X", slug="x", api_url="http://h:1", git_repo_url=None))
    await async_session.commit()
    r = await client.post("/api/apps/x/deploy", headers=get_auth_header(admin_token))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_deploy_no_conflict_with_other_app(client, admin_token, app_with_repo, async_session, monkeypatch):
    """Un déploiement d'une AUTRE app ne bloque plus (pool, spec 2026-09-17 §3.5)."""
    async_session.add(AppDeployment(app_slug="other", status="running"))
    await async_session.commit()
    import app.routes.app_deploy as route_mod

    async def _fake_start(slug, triggered_by):
        return 43
    monkeypatch.setattr(route_mod, "start_deployment", _fake_start)
    r = await client.post("/api/apps/exemple-app/deploy", headers=get_auth_header(admin_token))
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_deploy_conflict_same_app(client, admin_token, app_with_repo, async_session):
    async_session.add(AppDeployment(app_slug="exemple-app", status="running"))
    await async_session.commit()
    r = await client.post("/api/apps/exemple-app/deploy", headers=get_auth_header(admin_token))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_deploy_conflict_pending_same_app(client, admin_token, app_with_repo, async_session):
    """Une app 'pending' (en file) est aussi protégée contre le double déclenchement."""
    async_session.add(AppDeployment(app_slug="exemple-app", status="pending"))
    await async_session.commit()
    r = await client.post("/api/apps/exemple-app/deploy", headers=get_auth_header(admin_token))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_deploy_starts_and_returns_pending(client, admin_token, app_with_repo, monkeypatch):
    import app.routes.app_deploy as route_mod

    async def _fake_start(slug, triggered_by):
        return 42

    monkeypatch.setattr(route_mod, "start_deployment", _fake_start)
    r = await client.post("/api/apps/exemple-app/deploy", headers=get_auth_header(admin_token))
    assert r.status_code == 201
    body = r.json()
    assert body["deployment_id"] == 42
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_deploy_status_payload(client, admin_token, app_with_repo, async_session):
    async_session.add(AppDeployment(app_slug="exemple-app", status="success",
                                    current_step="done", logs="ok"))
    await async_session.commit()
    r = await client.get("/api/apps/exemple-app/deploy/status", headers=get_auth_header(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert body["current"]["status"] == "success"
    assert body["current"]["current_step"] == "done"
    assert body["history"][0]["status"] == "success"


@pytest.mark.asyncio
async def test_apps_list_exposes_last_deployment(client, admin_token, app_with_repo, async_session):
    async_session.add(AppDeployment(app_slug="exemple-app", status="failed"))
    async_session.add(AppDeployment(app_slug="exemple-app", status="success"))
    await async_session.commit()
    r = await client.get("/api/apps/", headers=get_auth_header(admin_token))
    assert r.status_code == 200
    mine = [a for a in r.json() if a["slug"] == "exemple-app"][0]
    assert mine["last_deployment"]["status"] == "success"


@pytest.mark.asyncio
async def test_apps_all_exposes_git_repo_and_last_deployment(client, admin_token, app_with_repo, async_session):
    """La table admin (GET /api/apps/all) doit exposer git_repo_url + last_deployment
    (badge « Non déployée » + bouton Déployer)."""
    async_session.add(AppDeployment(app_slug="exemple-app", status="failed"))
    await async_session.commit()
    r = await client.get("/api/apps/all", headers=get_auth_header(admin_token))
    assert r.status_code == 200
    mine = [a for a in r.json() if a["slug"] == "exemple-app"][0]
    assert mine["git_repo_url"] == "https://github.com/Poutchouli/exemple-app.git"
    assert mine["last_deployment"]["status"] == "failed"


@pytest.mark.asyncio
async def test_app_deployment_model_persists(async_session):
    """La table app_deployments existe et persiste (migration via Base.metadata en test)."""
    dep = AppDeployment(
        app_slug="exemple-app",
        status="running",
        current_step="clone",
        logs="clone…",
        triggered_by="admin",
    )
    async_session.add(dep)
    await async_session.commit()

    result = await async_session.execute(
        select(AppDeployment).where(AppDeployment.app_slug == "exemple-app")
    )
    fetched = result.scalar_one()
    assert fetched.id is not None
    assert fetched.status == "running"
    assert fetched.current_step == "clone"
    assert fetched.started_at is not None
    assert fetched.finished_at is None


@pytest.mark.asyncio
async def test_apps_host_path_setting_default(settings):
    """Le chemin APPS est configurable via APPS_HOST_PATH avec défaut utilisable."""
    from app.config import get_settings
    st = get_settings()
    assert hasattr(st, "apps_host_path")
    assert st.apps_host_path  # non vide par défaut


@pytest.mark.asyncio
async def test_requalify_stuck_deployments(async_session):
    from app.deploy_service import requalify_stuck_deployments

    async_session.add(AppDeployment(app_slug="a", status="pending"))
    async_session.add(AppDeployment(app_slug="b", status="running"))
    async_session.add(AppDeployment(app_slug="c", status="success"))
    await async_session.commit()

    n = await requalify_stuck_deployments(async_session)
    assert n == 2

    from sqlalchemy import select
    rows = (await async_session.execute(
        select(AppDeployment).where(AppDeployment.app_slug.in_(["a", "b", "c"]))
    )).scalars().all()
    by_slug = {r.app_slug: r for r in rows}
    assert by_slug["a"].status == "failed"
    assert by_slug["b"].status == "failed"
    assert "redémarrage" in (by_slug["a"].logs or "")
    assert by_slug["c"].status == "success"  # inchangé


@pytest.mark.asyncio
async def test_run_deployment_normalize_ssh_url(async_session, monkeypatch, tmp_path):
    """Bug 2026-10-03 : une app enregistrée avec une URL SSH (git@host:path)
    doit être clonée en HTTPS — le conteneur hub n'a pas de binaire ssh et le
    PAT ne s'applique qu'au HTTPS (« cannot run ssh: No such file or directory »)."""
    import app.deploy_service as ds

    dep_id = await _mk_deployment(async_session,
                                  repo="git@github.com:Poutchouli/exemple-app.git")
    appdir = tmp_path / "exemple-app"
    appdir.mkdir()
    (appdir / "Makefile").write_text("build:\n\t@echo build\nup:\n\t@echo up\n")
    (appdir / ".env.example").write_text("APP_SLUG=x\n")

    calls = _install_fake_exec(monkeypatch, [(0, b"clone ok"), (0, b""), (0, b"")])
    monkeypatch.setattr(ds, "_wait_healthy", lambda *a, **k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))

    factory = lambda: _CtxSession(async_session)
    await ds._run_deployment(dep_id, "exemple-app", session_factory=factory)

    clone_cmds = [c["cmd"] for c in calls if "clone" in c["cmd"]]
    assert clone_cmds, "le clone doit avoir été invoqué"
    clone_cmd = " ".join(clone_cmds[0])
    assert "https://github.com/Poutchouli/exemple-app.git" in clone_cmd
    assert "git@github.com:" not in clone_cmd
