# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests des overrides compose par app (.workshop.overrides.env).

Motivation (bug « ledger Task 0 ») : une app qui bind un port host métier
via une var de son .env prod (ex. exemple-app :
"${PHILIA_NG_PORT:-42690}:8080") retombait sur le port prod dans
l'atelier → conflit de bind → restart loop. Contrat testé :

1. allowlist docker (_compose_base_env) — le plus faible ;
2. .workshop.overrides.env (bat le .env prod lu par Compose lui-même) ;
3. clés du contrat .workshop.env (WS_ENV_KEYS) — gagnent TOUJOURS, et le
   fichier écrit reste strictement limité à ces clés ;
+ clés réservées ignorées avec warning, fichier absent → no-op strict,
  fichier illisible → warning + opération poursuivie.

Patterns subprocess/fixtures : cf. test_workshop_service.py (importés).
"""
import asyncio
import logging
import os
from pathlib import Path

import pytest

import app.workshop_service as ws
from app.models import WorkshopOperation
from tests.test_workshop_service import (
    _install_exec,
    _mk_app,
    _mk_op,
    _no_github_token,
)


def _git_state_ok():
    """Pattern test_op_create_nominal : git state sync mocké en coroutine."""
    async def _state(_dir):
        return await asyncio.sleep(0, result={"ok": True, "branch": "main",
                                              "head": "abc000"})
    return _state


# ── (a) Fichier absent → no-op strict ──


def test_overrides_absent_noop(tmp_path, monkeypatch):
    """Sans .workshop.overrides.env, l'env compose est strictement identique
    à avant : allowlist + clés du contrat + APP_VERSION, rien de plus."""
    _mk_app(tmp_path, monkeypatch, with_ws=True)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    expected = ws._compose_base_env()
    expected.update(ws._read_env_file(ws._ws_env_path("wstest")))
    expected["APP_VERSION"] = "0.8.3"
    assert ws._ws_compose_env("wstest") == expected
    assert ws._ws_override_env("wstest") == {}


# ── (b) Overrides présents → clés métier dans l'env compose ──


def test_overrides_business_keys_in_compose_env(tmp_path, monkeypatch):
    """Les clés métier des overrides atteignent l'env du compose -ws :
    Compose interpolera ${PHILIA_NG_PORT:-42690} → 5290 (pas le port prod)."""
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)
    (app / ".workshop.overrides.env").write_text(
        "# port host métier atelier\nPHILIA_NG_PORT=5290\nGPU_ENABLED=false\n")
    env = ws._ws_compose_env("wstest")
    assert env["PHILIA_NG_PORT"] == "5290"
    assert env["GPU_ENABLED"] == "false"
    # Le contrat reste intact (les clés allocateur ne sont pas écrasées)
    assert env["API_PORT"] == "10000"
    assert env["APP_SLUG"] == "wstest-ws"


# ── (c) Précédence : le contrat .workshop.env bat les overrides ──


def test_overrides_precedence_contract_wins(tmp_path, monkeypatch):
    """Une clé du contrat présente dans les deux fichiers : la valeur du
    .workshop.env gagne (possédée par l'allocateur) ; les clés métier
    voisines passent normalement."""
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)
    (app / ".workshop.overrides.env").write_text(
        "API_PORT=19999\nPHILIA_NG_PORT=5290\n")
    env = ws._ws_compose_env("wstest")
    assert env["API_PORT"] == "10000"        # contrat, PAS 19999
    assert env["PHILIA_NG_PORT"] == "5290"   # override métier appliqué


# ── (d) Clés réservées → ignorées + warning ──


def test_overrides_reserved_keys_ignored_with_warning(tmp_path, monkeypatch,
                                                      caplog):
    """Toute clé de WS_OVERRIDE_RESERVED dans les overrides est ignorée
    (warning explicite) — les overrides ne peuvent pas détourner
    ports/subnet/ref ni forcer l'overlay HTTPS."""
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)
    reserved_keys = sorted(ws.WS_OVERRIDE_RESERVED)
    assert "HTTPS_ENABLED" in reserved_keys and "API_PORT" in reserved_keys
    (app / ".workshop.overrides.env").write_text(
        "".join(f"{k}=9999\n" for k in reserved_keys) + "PHILIA_NG_PORT=5290\n")
    with caplog.at_level(logging.WARNING, logger="hub.workshop"):
        overrides = ws._ws_override_env("wstest")
    assert overrides == {"PHILIA_NG_PORT": "5290"}
    warned = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    for key in reserved_keys:
        assert any(f"clé réservée {key} ignorée" in m for m in warned), warned


# ── (e) Path create : l'env passé au subprocess up contient l'override ──


@pytest.mark.asyncio
async def test_op_create_env_subprocess_contient_override(tmp_path, monkeypatch,
                                                          async_session):
    """Create complet : le compose build/up reçoit PHILIA_NG_PORT=5290, et le
    .workshop.env écrit reste strictement limité aux clés du contrat (aucune
    clé d'override ne fuit dans le fichier)."""
    app = _mk_app(tmp_path, monkeypatch)  # sans .workshop.env
    (app / ".workshop.overrides.env").write_text("PHILIA_NG_PORT=5290\n")
    op = await _mk_op(async_session, op="create")
    monkeypatch.setattr(ws, "_git_state", _git_state_ok())
    monkeypatch.setattr(ws, "_alloc_ports", lambda used=None: (10002, 5202))
    monkeypatch.setattr(ws, "_alloc_subnet", lambda: "10.210.3.0/24")

    async def _fake_healthy(port, timeout=60):
        return True

    monkeypatch.setattr(ws, "_wait_healthy", _fake_healthy)
    calls = _install_exec(monkeypatch, [(0, b"") for _ in range(10)])
    await ws._run_workshop_operation(op.id, "wstest", "create")
    up_calls = [c for c in calls
                if list(c["cmd"][:2]) == ["docker", "compose"] and "up" in c["cmd"]]
    assert up_calls, calls
    assert up_calls[0]["env"].get("PHILIA_NG_PORT") == "5290"
    # Contrat : le fichier écrit ne contient QUE les clés allocateur
    written = ws._read_env_file(app / ".workshop.env")
    assert list(written) == ws.WS_ENV_KEYS
    assert "PHILIA_NG_PORT" not in written


# ── (f) Fichier illisible → warning + opération qui continue ──


def _patch_overrides_read_perm(monkeypatch):
    """Simule un .workshop.overrides.env illisible (PermissionError, ex.
    owner root 0600) — les autres reads passent (style _patch_unlink_rofs)."""
    real_read_text = Path.read_text
    calls = {"n": 0}

    def _perm_read_text(self, *a, **kw):
        if self.name == ".workshop.overrides.env":
            calls["n"] += 1
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _perm_read_text)
    return calls


def _warning_msgs(caplog):
    return [r.message for r in caplog.records if r.levelno == logging.WARNING]


def test_overrides_unreadable_warning_and_noop(tmp_path, monkeypatch, caplog):
    """Overrides illisibles (OSError → PermissionError) : warning + {} —
    jamais de crash, le compose part sans overrides."""
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)
    (app / ".workshop.overrides.env").write_text("PHILIA_NG_PORT=5290\n")
    os.chmod(app / ".workshop.overrides.env", 0o000)
    try:
        with caplog.at_level(logging.WARNING, logger="hub.workshop"):
            assert ws._ws_override_env("wstest") == {}
        assert any("illisible" in m for m in _warning_msgs(caplog))
    finally:
        os.chmod(app / ".workshop.overrides.env", 0o644)  # cleanup tmp_path


def test_overrides_non_utf8_warning_and_noop(tmp_path, monkeypatch, caplog):
    """Fichier non-UTF-8 (encodage exotique, ex. UTF-16) : UnicodeDecodeError,
    même contrat que PermissionError — warning + {}, jamais de crash."""
    app = _mk_app(tmp_path, monkeypatch, with_ws=True)
    (app / ".workshop.overrides.env").write_bytes(
        b"\xff\xfeP\x00H\x00I\x00L\x00I\x00A\x00"  # BOM UTF-16 + texte UTF-16
        b"\x00=\x005\x002\x009\x000\x00")
    with caplog.at_level(logging.WARNING, logger="hub.workshop"):
        assert ws._ws_override_env("wstest") == {}
    assert any("illisible" in m for m in _warning_msgs(caplog))


@pytest.mark.asyncio
async def test_op_create_unreadable_overrides_continue(tmp_path, monkeypatch,
                                                       async_session, caplog):
    """Create avec overrides illisibles : warning + l'opération réussit quand
    même, l'env compose part sans overrides (jamais de crash d'opération
    atelier pour un fichier d'override)."""
    app = _mk_app(tmp_path, monkeypatch)
    (app / ".workshop.overrides.env").write_text("PHILIA_NG_PORT=5290\n")
    op = await _mk_op(async_session, op="create")
    monkeypatch.setattr(ws, "_git_state", _git_state_ok())
    monkeypatch.setattr(ws, "_alloc_ports", lambda used=None: (10002, 5202))
    monkeypatch.setattr(ws, "_alloc_subnet", lambda: "10.210.3.0/24")
    _no_github_token(monkeypatch)

    async def _fake_healthy(port, timeout=60):
        return True

    monkeypatch.setattr(ws, "_wait_healthy", _fake_healthy)
    perm_calls = _patch_overrides_read_perm(monkeypatch)
    calls = _install_exec(monkeypatch, [(0, b"") for _ in range(10)])
    with caplog.at_level(logging.WARNING, logger="hub.workshop"):
        await ws._run_workshop_operation(op.id, "wstest", "create")
    done = await async_session.get(WorkshopOperation, op.id)
    assert done.status == "success"
    assert perm_calls["n"] >= 1                      # le read a bien été tenté
    assert any("illisible" in m for m in _warning_msgs(caplog))
    up_calls = [c for c in calls
                if list(c["cmd"][:2]) == ["docker", "compose"] and "up" in c["cmd"]]
    assert up_calls, calls
    assert "PHILIA_NG_PORT" not in up_calls[0]["env"]
    assert up_calls[0]["env"].get("APP_SLUG") == "wstest-ws"
