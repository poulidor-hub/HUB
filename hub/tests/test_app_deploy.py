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
        calls.append({"cmd": cmd, "cwd": str(cwd)})
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
                           frontend_port=4204, frontend_https_port=4205)
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
    (tmp_path / "exemple-app").mkdir()
    (tmp_path / "exemple-app" / ".env.example").write_text(
        "APP_SLUG=old\nAPP_PORT=1\nPOSTGRES_PASSWORD=x\n"
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
    assert "POSTGRES_PASSWORD=x" not in env_text     # secret régénéré à la création

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
    (appdir / ".git").mkdir()  # dir existante -> pull, pas clone

    calls = _install_fake_exec(monkeypatch, [(0, b"pull"), (0, b""), (0, b"")])
    monkeypatch.setattr(ds, "_wait_healthy", lambda *a, **k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))

    await ds._run_deployment(dep_id, "exemple-app", session_factory=lambda: _CtxSession(async_session))

    assert any("pull" in " ".join(c["cmd"]) for c in calls)
    env_text = (appdir / ".env").read_text()
    assert "POSTGRES_PASSWORD=keep" in env_text
    assert "APP_SLUG=exemple-app" in env_text


@pytest.mark.asyncio
async def test_has_active_deployment(async_session):
    from app.deploy_service import has_active_deployment

    assert not await has_active_deployment(async_session)
    async_session.add(AppDeployment(app_slug="a", status="running"))
    await async_session.commit()
    assert await has_active_deployment(async_session)


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
