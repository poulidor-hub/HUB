"""Tests du service atelier (workshop_service) — cycle pilotable depuis l'UI HUB.

Pattern : subprocess mockés (_FakeProc/_install_fake_exec, cf. test_app_deploy.py),
DB sqlite in-memory (conftest). Le contrat .workshop.env (7 clés hubctl v0.6.1)
est figé ici : toute divergence doit faire crier ces tests.
"""
import pytest

import app.workshop_service as ws
from app.models import WorkshopOperation


@pytest.mark.asyncio
async def test_workshop_operation_model_persists(async_session):
    op = WorkshopOperation(
        app_slug="wstest", operation="update", status="pending",
        triggered_by="admin",
    )
    async_session.add(op)
    await async_session.commit()
    await async_session.refresh(op)
    assert op.id is not None
    assert op.status == "pending"
    assert op.current_step is None
    assert op.started_at is not None


@pytest.mark.asyncio
async def test_workshop_retention_setting_default():
    from app.config import get_settings
    assert get_settings().workshop_retention_days == 30


# ── Noyau : contrat .workshop.env, git, allocation ──


def test_ws_env_keys_contract():
    """Contrat de dualité : format exact hubctl v0.6.1 (7 clés, ordre canonique).
    Si hubctl évolue, ce test doit crier des deux côtés."""
    assert ws.WS_ENV_KEYS == [
        "APP_SLUG", "API_PORT", "FRONTEND_PORT", "FRONTEND_HTTPS_PORT",
        "WS_REF", "WS_COMMIT", "APP_SUBNET",
    ]


def test_read_env_file(tmp_path):
    f = tmp_path / "ws.env"
    f.write_text("APP_SLUG=x-ws\nAPI_PORT=10000\n# comment\n\nWS_REF=main\n")
    assert ws._read_env_file(f) == {"APP_SLUG": "x-ws", "API_PORT": "10000", "WS_REF": "main"}


def test_write_ws_env_full_order(tmp_path):
    f = tmp_path / ".workshop.env"
    ws._write_ws_env_full(f, {
        "WS_COMMIT": "abc", "APP_SLUG": "x-ws", "API_PORT": "10000",
        "FRONTEND_PORT": "5200", "FRONTEND_HTTPS_PORT": "5201",
        "WS_REF": "main", "APP_SUBNET": "10.210.0.0/24",
    })
    lines = f.read_text().strip().splitlines()
    keys = [l.split("=")[0] for l in lines]
    assert keys == ws.WS_ENV_KEYS


def test_get_workshop_state_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    st = ws.get_workshop_state("wstest")
    assert st == {"exists": False, "ws_slug": None, "ws_ref": None, "ws_commit": None,
                  "api_port": None, "frontend_port": None, "subnet": None,
                  "git_ok": False, "version": None}


def test_get_workshop_state_present(tmp_path, monkeypatch):
    app = tmp_path / "wstest"; app.mkdir()
    (app / ".workshop.env").write_text(
        "APP_SLUG=wstest-ws\nAPI_PORT=10000\nFRONTEND_PORT=5200\n"
        "FRONTEND_HTTPS_PORT=5201\nWS_REF=main\nWS_COMMIT=abc123def\n"
        "APP_SUBNET=10.210.0.0/24\n")
    (app / "VERSION").write_text("0.8.3\n")
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "_git_state", lambda d: {"ok": True, "branch": "main", "head": "abc"})
    st = ws.get_workshop_state("wstest")
    assert st["exists"] is True
    assert st["ws_slug"] == "wstest-ws"
    assert st["ws_ref"] == "main" and st["ws_commit"] == "abc123def"
    assert st["version"] == "0.8.3" and st["git_ok"] is True


def test_alloc_ports_skips_used():
    used = set(range(10000, 10010)) | set(range(5200, 5210))
    res = ws._alloc_ports(used)
    assert res == (10010, 5210)


def test_alloc_ports_exhausted(monkeypatch):
    monkeypatch.setattr(ws, "WS_API_PORT_RANGE", (10000, 10002))
    used = {10000, 10001, 10002}
    assert ws._alloc_ports(used) is None


def test_alloc_subnet_first_free(monkeypatch):
    monkeypatch.setattr(ws, "_docker_used_subnets", lambda: set())
    monkeypatch.setattr(ws, "_used_ws_subnets", lambda: set())
    assert ws._alloc_subnet() == "10.210.0.0/24"


def test_alloc_subnet_skips_used(monkeypatch):
    monkeypatch.setattr(ws, "_docker_used_subnets", lambda: {"10.210.0.0/24"})
    monkeypatch.setattr(ws, "_used_ws_subnets", lambda: {"10.210.1.0/24"})
    assert ws._alloc_subnet() == "10.210.2.0/24"


def test_alloc_subnet_overlap_broad(monkeypatch):
    monkeypatch.setattr(ws, "_docker_used_subnets", lambda: {"10.0.0.0/8"})
    monkeypatch.setattr(ws, "_used_ws_subnets", lambda: set())
    assert ws._alloc_subnet() is None


async def test_has_active_workshop_operation(async_session):
    from app.models import WorkshopOperation
    assert await ws.has_active_workshop_operation(async_session) is False
    async_session.add(WorkshopOperation(app_slug="a", operation="update", status="running"))
    await async_session.commit()
    assert await ws.has_active_workshop_operation(async_session) is True
    assert await ws.has_active_workshop_operation(async_session, slug="b") is False


async def test_has_active_workshop_operation_multiple_rows(async_session):
    """Race double-clic : 2+ lignes pending/running ne doivent pas lever
    MultipleResultsFound (500) — garde limit(1), pattern has_active_deployment."""
    async_session.add(WorkshopOperation(app_slug="a", operation="update", status="running"))
    async_session.add(WorkshopOperation(app_slug="a", operation="create", status="pending"))
    await async_session.commit()
    assert await ws.has_active_workshop_operation(async_session) is True
    assert await ws.has_active_workshop_operation(async_session, slug="a") is True


# ── Opérations create / update ──
# Patterns subprocess mockés (cf. test_app_deploy.py:132-170) : les commandes
# passent par _run_cmd importé de deploy_service → monkeypatcher
# deploy_service.asyncio.create_subprocess_exec.

import asyncio

from sqlalchemy import select

import app.deploy_service as ds


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


def _install_exec(monkeypatch, results):
    """results: liste (returncode, stdout) consommée dans l'ordre (git+docker mélangés)."""
    calls = []
    queue = list(results)

    async def _fake_exec(*cmd, cwd=None, env=None, stdout=None, stderr=None):
        calls.append({"cmd": cmd, "cwd": str(cwd), "env": env or {}})
        rc, out = queue.pop(0) if queue else (0, b"")
        return _FakeProc(rc, out)

    monkeypatch.setattr(ds.asyncio, "create_subprocess_exec", _fake_exec)
    return calls


async def _no_token():
    return None


def _no_github_token(monkeypatch):
    """Fetch sans PAT — isole les fetch authentifiés de la DB réelle
    (ws.async_session_maker est bindé à l'import, hors patch conftest)."""
    monkeypatch.setattr(ws, "_github_token", _no_token)


def _mk_app(tmp_path, monkeypatch, *, with_ws=False, version="0.8.3"):
    app = tmp_path / "wstest"; app.mkdir(exist_ok=True)
    (app / "docker-compose.yml").write_text("services: {}\n")
    # Makefile : _op_ship préfère make build/up quand il existe (parité hubctl)
    (app / "Makefile").write_text("build:\nup:\n")
    (app / "VERSION").write_text(version + "\n")
    (app / ".env").write_text("API_PORT=9001\nFRONTEND_PORT=4201\nAPP_VERSION=0.8.2\n")
    if with_ws:
        ws._write_ws_env_full(app / ".workshop.env", {
            "APP_SLUG": "wstest-ws", "API_PORT": "10000", "FRONTEND_PORT": "5200",
            "FRONTEND_HTTPS_PORT": "5201", "WS_REF": "main", "WS_COMMIT": "prev123",
            "APP_SUBNET": "10.210.0.0/24",
        })
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    return app


async def _mk_op(async_session, op="update", slug="wstest"):
    o = WorkshopOperation(app_slug=slug, operation=op, status="pending",
                          triggered_by="admin")
    async_session.add(o)
    await async_session.commit()
    await async_session.refresh(o)
    # Évince l'objet de l'identity map : les écritures de fond passent par une
    # autre session (maker patché) — sans expunge, async_session.get() rendrait
    # l'instance en cache ("pending") sans relire la ligne (cf. refresh dans
    # test_app_deploy.py).
    async_session.expunge(o)
    return o


async def test_op_update_nominal(tmp_path, monkeypatch, async_session):
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)
    op = await _mk_op(async_session)
    # File ajustée à l'ordre réel des commandes émises par _op_update
    # (latitude explicite du brief — invariant = résultat final + build_calls) :
    seq = [
        (0, b".git\n"),           # _git_state : rev-parse --git-dir
        (0, b"https://origin\n"), # _git_state : remote get-url origin
        (0, b"prev123\n"),        # _git_state : rev-parse HEAD
        (0, b"main\n"),           # _git_state : rev-parse --abbrev-ref HEAD
        (0, b""),                 # _worktree_clean : status --porcelain (vide)
        (0, b"main\n"),           # _worktree_clean : abbrev-ref (base @{u})
        (0, b""),                 # _worktree_clean : log @{u}..HEAD (vide = clean)
        (0, b""),                 # git fetch --quiet origin --tags
        (0, b"new456\n"),         # rev-parse origin/main (cible)
        (0, b""),                 # git checkout --quiet new456 (detached)
        (0, b""),                 # docker compose build
        (0, b""),                 # docker compose up -d
    ]
    calls = _install_exec(monkeypatch, seq)
    _no_github_token(monkeypatch)

    async def _fake_healthy(port, timeout=60):
        return True

    monkeypatch.setattr(ws, "_wait_healthy", _fake_healthy)
    await ws._run_workshop_operation(op.id, "wstest", "update")
    env = ws._read_env_file(app / ".workshop.env")
    assert env["WS_COMMIT"] == "new456" and env["WS_REF"] == "main"
    build_calls = [c for c in calls
                   if list(c["cmd"][:2]) == ["docker", "compose"] and "build" in c["cmd"]]
    assert build_calls and build_calls[0]["env"].get("APP_SLUG") == "wstest-ws"
    assert build_calls[0]["env"].get("APP_VERSION") == "0.8.3"


async def test_op_update_idempotent(tmp_path, monkeypatch, async_session):
    _mk_app(tmp_path, monkeypatch, with_ws=True)  # WS_COMMIT=prev123
    op = await _mk_op(async_session)
    calls = _install_exec(monkeypatch, [])

    async def _origin_main(app_dir):
        return "prev123"  # == WS_COMMIT → déjà à jour

    monkeypatch.setattr(ws, "_origin_main", _origin_main)
    _no_github_token(monkeypatch)
    monkeypatch.setattr(ws, "_worktree_clean", lambda d: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ws, "_git_state",
                        lambda d: asyncio.sleep(0, result={"ok": True, "branch": "main", "head": "prev123"}))
    await ws._run_workshop_operation(op.id, "wstest", "update")
    docker_calls = [c for c in calls if list(c["cmd"][:1]) == ["docker"]]
    assert docker_calls == []  # aucun rebuild
    async_session_explicit = await async_session.get(WorkshopOperation, op.id)
    assert async_session_explicit.status == "success"


async def test_op_update_refuse_worktree_sale(tmp_path, monkeypatch, async_session):
    _mk_app(tmp_path, monkeypatch, with_ws=True)
    op = await _mk_op(async_session)
    calls = _install_exec(monkeypatch, [])
    monkeypatch.setattr(ws, "_git_state",
                        lambda d: asyncio.sleep(0, result={"ok": True, "branch": "main", "head": "prev123"}))
    monkeypatch.setattr(ws, "_worktree_clean", lambda d: asyncio.sleep(0, result=False))
    await ws._run_workshop_operation(op.id, "wstest", "update")
    failed = await async_session.get(WorkshopOperation, op.id)
    assert failed.status == "failed"
    assert "working tree" in failed.logs.lower()
    assert calls == []  # rien d'exécuté après le refus


async def test_op_update_rollback_build_ko(tmp_path, monkeypatch, async_session):
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)
    op = await _mk_op(async_session)
    monkeypatch.setattr(ws, "_git_state",
                        lambda d: asyncio.sleep(0, result={"ok": True, "branch": "main", "head": "prev123"}))
    monkeypatch.setattr(ws, "_worktree_clean", lambda d: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ws, "_origin_main", lambda d: asyncio.sleep(0, result="new456"))
    _no_github_token(monkeypatch)

    calls = []

    def _exec(cmd, **kw):
        calls.append({"cmd": cmd})
        if "build" in cmd:
            return _FakeProc(1, b"build failed")
        return _FakeProc(0, b"")

    async def _fake_exec(*cmd, cwd=None, env=None, stdout=None, stderr=None):
        return _exec(cmd)

    monkeypatch.setattr(ds.asyncio, "create_subprocess_exec", _fake_exec)
    await ws._run_workshop_operation(op.id, "wstest", "update")
    failed = await async_session.get(WorkshopOperation, op.id)
    assert failed.status == "failed"
    # git restauré (checkout main + reset prev123 passés)
    git_calls = [c["cmd"] for c in calls if c["cmd"][0] == "git"]
    flat = [" ".join(c) for c in git_calls]
    assert any("reset" in f and "prev123" in f for f in flat), flat
    # .workshop.env NON mis à jour
    env = ws._read_env_file(app / ".workshop.env")
    assert env["WS_COMMIT"] == "prev123"


async def test_op_create_nominal(tmp_path, monkeypatch, async_session):
    app = _mk_app(tmp_path, monkeypatch)  # sans .workshop.env
    op = await _mk_op(async_session, op="create")
    monkeypatch.setattr(ws, "_git_state",
                        lambda d: asyncio.sleep(0, result={"ok": True, "branch": "main", "head": "abc000"}))
    monkeypatch.setattr(ws, "_alloc_ports", lambda used=None: (10002, 5202))
    monkeypatch.setattr(ws, "_alloc_subnet", lambda: "10.210.3.0/24")

    async def _fake_healthy(port, timeout=60):
        return True

    monkeypatch.setattr(ws, "_wait_healthy", _fake_healthy)
    calls = _install_exec(monkeypatch, [(0, b"") for _ in range(10)])
    await ws._run_workshop_operation(op.id, "wstest", "create")
    env = ws._read_env_file(app / ".workshop.env")
    assert env["APP_SLUG"] == "wstest-ws"
    assert env["API_PORT"] == "10002" and env["FRONTEND_PORT"] == "5202"
    assert env["WS_REF"] == "main" and env["WS_COMMIT"] == "abc000"
    assert env["APP_SUBNET"] == "10.210.3.0/24"
    done = await async_session.get(WorkshopOperation, op.id)
    assert done.status == "success"
    # clone DB via alpine cp
    clone_calls = [c["cmd"] for c in calls
                   if list(c["cmd"][:1]) == ["docker"] and "alpine" in " ".join(c["cmd"])]
    assert clone_calls


async def test_op_create_refuse_existant(tmp_path, monkeypatch, async_session):
    _mk_app(tmp_path, monkeypatch, with_ws=True)
    op = await _mk_op(async_session, op="create")
    calls = _install_exec(monkeypatch, [])
    await ws._run_workshop_operation(op.id, "wstest", "create")
    failed = await async_session.get(WorkshopOperation, op.id)
    assert failed.status == "failed"
    assert calls == []


# ── Opérations ship / remove + purge ──


async def test_op_ship_refuse_ref_non_main(tmp_path, monkeypatch, async_session):
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)
    # atelier teste feat/x
    ws.apply_env_values(app / ".workshop.env", {"WS_REF": "feat/x", "WS_COMMIT": "abc"})
    op = await _mk_op(async_session, op="ship")
    calls = _install_exec(monkeypatch, [])
    await ws._run_workshop_operation(op.id, "wstest", "ship")
    failed = await async_session.get(WorkshopOperation, op.id)
    assert failed.status == "failed"
    assert "main" in failed.logs
    assert calls == []


async def test_op_ship_refuse_main_avance(tmp_path, monkeypatch, async_session):
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)  # WS_COMMIT=prev123
    op = await _mk_op(async_session, op="ship")
    monkeypatch.setattr(ws, "_git_state",
                        lambda d: asyncio.sleep(0, result={"ok": True, "branch": "main", "head": "prev123"}))
    monkeypatch.setattr(ws, "_worktree_clean", lambda d: asyncio.sleep(0, result=True))
    _no_github_token(monkeypatch)
    async def _healthy(port, timeout=60):
        return int(port) == 10000  # atelier sain, prod KO (pas atteinte)
    monkeypatch.setattr(ws, "_wait_healthy", _healthy)
    def _exec(cmd, **kw):
        calls.append({"cmd": cmd})
        if "origin/main" in " ".join(cmd):
            return _FakeProc(0, b"new456\n")  # origin/main a avancé
        return _FakeProc(0, b"")
    calls = []
    async def _fake_exec(*cmd, cwd=None, env=None, stdout=None, stderr=None):
        return _exec(cmd)
    monkeypatch.setattr(ds.asyncio, "create_subprocess_exec", _fake_exec)
    await ws._run_workshop_operation(op.id, "wstest", "ship")
    failed = await async_session.get(WorkshopOperation, op.id)
    assert failed.status == "failed"
    assert "avancé" in failed.logs or "avance" in failed.logs
    # git restauré vers prev123
    flat = [" ".join(c["cmd"]) for c in calls]
    assert any("reset" in f and "prev123" in f for f in flat)
    # .workshop.env conservé (atelier non supprimé)
    assert (app / ".workshop.env").exists()


async def test_op_ship_nominal(tmp_path, monkeypatch, async_session):
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)  # WS_COMMIT=prev123
    op = await _mk_op(async_session, op="ship")
    monkeypatch.setattr(ws, "_git_state",
                        lambda d: asyncio.sleep(0, result={"ok": True, "branch": "main", "head": "prev123"}))
    monkeypatch.setattr(ws, "_worktree_clean", lambda d: asyncio.sleep(0, result=True))
    _no_github_token(monkeypatch)
    healthy_ports = []
    async def _healthy(port, timeout=60):
        healthy_ports.append(int(port))
        return True
    monkeypatch.setattr(ws, "_wait_healthy", _healthy)
    def _exec(cmd, **kw):
        calls.append({"cmd": cmd})
        if "origin/main" in " ".join(cmd):
            return _FakeProc(0, b"prev123\n")  # == WS_COMMIT
        return _FakeProc(0, b"")
    calls = []
    async def _fake_exec(*cmd, cwd=None, env=None, stdout=None, stderr=None):
        return _exec(cmd)
    monkeypatch.setattr(ds.asyncio, "create_subprocess_exec", _fake_exec)
    await ws._run_workshop_operation(op.id, "wstest", "ship")
    done = await async_session.get(WorkshopOperation, op.id)
    assert done.status == "success"
    # make build puis make up (prod), puis down -v atelier
    flat = [" ".join(c["cmd"]) for c in calls]
    assert any(f.startswith("make") and "build" in f for f in flat)
    assert any(f.startswith("make") and " up" in f for f in flat)
    assert any("down" in f and "-v" in f for f in flat), flat
    # atelier supprimé
    assert not (app / ".workshop.env").exists()
    # health : atelier (10000) puis prod (9001 du .env)
    assert 10000 in healthy_ports and 9001 in healthy_ports


async def test_op_ship_echec_build_atelier_conserve(tmp_path, monkeypatch, async_session):
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)
    op = await _mk_op(async_session, op="ship")
    monkeypatch.setattr(ws, "_git_state",
                        lambda d: asyncio.sleep(0, result={"ok": True, "branch": "main", "head": "prev123"}))
    monkeypatch.setattr(ws, "_worktree_clean", lambda d: asyncio.sleep(0, result=True))
    _no_github_token(monkeypatch)
    monkeypatch.setattr(ws, "_wait_healthy",
                        lambda p, timeout=60: asyncio.sleep(0, result=True))
    def _exec(cmd, **kw):
        calls.append({"cmd": cmd})
        if "origin/main" in " ".join(cmd):
            return _FakeProc(0, b"prev123\n")
        if list(cmd[:1]) == ["make"] and "build" in cmd:
            return _FakeProc(1, b"make build failed")
        return _FakeProc(0, b"")
    calls = []
    async def _fake_exec(*cmd, cwd=None, env=None, stdout=None, stderr=None):
        return _exec(cmd)
    monkeypatch.setattr(ds.asyncio, "create_subprocess_exec", _fake_exec)
    await ws._run_workshop_operation(op.id, "wstest", "ship")
    failed = await async_session.get(WorkshopOperation, op.id)
    assert failed.status == "failed"
    assert (app / ".workshop.env").exists()  # atelier conservé


async def test_op_remove_nominal(tmp_path, monkeypatch, async_session):
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)
    op = await _mk_op(async_session, op="remove")
    calls = _install_exec(monkeypatch, [(0, b"") for _ in range(4)])
    await ws._run_workshop_operation(op.id, "wstest", "remove")
    done = await async_session.get(WorkshopOperation, op.id)
    assert done.status == "success"
    flat = [" ".join(c["cmd"]) for c in calls]
    assert any("down" in f and "-v" in f for f in flat)
    assert not (app / ".workshop.env").exists()


async def test_purge_workshop_operations(async_session):
    from datetime import datetime, timedelta
    from app.models import utcnow
    old = WorkshopOperation(app_slug="x", operation="update", status="success",
                            finished_at=utcnow() - timedelta(days=99))
    recent = WorkshopOperation(app_slug="x", operation="update", status="success",
                               finished_at=utcnow())
    async_session.add_all([old, recent])
    await async_session.commit()
    removed = await ws.purge_workshop_operations(async_session, retention_days=30)
    assert removed == 1
    remaining = (await async_session.execute(
        select(WorkshopOperation))).scalars().all()
    assert len(remaining) == 1 and remaining[0].id == recent.id


# ── git-config : remote local + auth PAT + test connexion ──

import subprocess as _sp


def _mk_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    def g(*a): return _sp.run(["git", "-C", str(path), *a], capture_output=True, text=True)
    g("init", "-q", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (path / "f.txt").write_text("x"); g("add", "-A"); g("commit", "-qm", "init")
    return path


async def test_configure_remote_add(tmp_path, monkeypatch):
    repo = _mk_repo(tmp_path / "wstest")
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    assert await ws._configure_remote(repo, "https://example.com/x.git") == "added"
    out = _sp.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                  capture_output=True, text=True).stdout.strip()
    assert out == "https://example.com/x.git"


async def test_configure_remote_update_and_unchanged(tmp_path, monkeypatch):
    repo = _mk_repo(tmp_path / "wstest")
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    await ws._configure_remote(repo, "https://example.com/a.git")
    assert await ws._configure_remote(repo, "https://example.com/b.git") == "updated"
    assert await ws._configure_remote(repo, "https://example.com/b.git") == "unchanged"


async def test_configure_remote_not_a_repo(tmp_path, monkeypatch):
    d = tmp_path / "pasunrepo"; d.mkdir()
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="n'est pas un dépôt git"):
        await ws._configure_remote(d, "https://example.com/x.git")


async def test_git_with_auth_args(monkeypatch, tmp_path):
    calls = []
    async def _fake_exec(*cmd, cwd=None, env=None, stdout=None, stderr=None):
        calls.append(cmd)
        return _FakeProc(0, b"")
    monkeypatch.setattr(ds.asyncio, "create_subprocess_exec", _fake_exec)
    auth = ["-c", "http.extraheader=Authorization: Basic dG9rZW46eA=="]
    await ws._git(tmp_path, "fetch", "--quiet", "origin", auth_args=auth)
    assert calls[0][0:2] == ("git", "-c") and "safe.directory=*" in calls[0][2]
    assert "http.extraheader=Authorization: Basic" in " ".join(calls[0])
    assert calls[0][-3:] == ("fetch", "--quiet", "origin")


async def test_github_token_from_db(monkeypatch, async_session):
    from app.models import AppSetting
    async_session.add(AppSetting(key="github_token", value="ghp_test123"))
    await async_session.commit()

    class _Ctx:
        def __init__(self, s): self._s = s
        async def __aenter__(self): return self._s
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(ws, "async_session_maker", lambda: _Ctx(async_session))
    token = await ws._github_token()
    assert token == "ghp_test123"


async def test_git_connection_ok(monkeypatch, tmp_path):
    async def _fake_run(cmd, cwd, timeout, env_extra=None):
        return "ref1 refs/heads/main\nref2 refs/heads/dev\n"
    monkeypatch.setattr(ws, "_run_cmd", _fake_run)
    _no_github_token(monkeypatch)
    ok, msg = await ws.test_git_connection("https://example.com/x.git")
    assert ok is True and "2 branche(s)" in msg


async def test_git_connection_fail(monkeypatch, tmp_path):
    async def _fake_run(cmd, cwd, timeout, env_extra=None):
        raise RuntimeError("remote: Access denied\nfatal: Authentication failed")
    monkeypatch.setattr(ws, "_run_cmd", _fake_run)
    _no_github_token(monkeypatch)
    ok, msg = await ws.test_git_connection("https://example.com/x.git")
    assert ok is False and "Access denied" in msg and "Authentication failed" not in msg
