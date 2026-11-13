# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests du collector apps_code (capture du code APPS/ pour le PRA)."""
import tarfile
from pathlib import Path

import pytest

from app import snapshot_collectors
from app.snapshot_collectors import run_apps_code_tar, run_pg_dump


def _make_app_tree(base):
    """Arbre fictif : 1 app avec .git, sources, et artefacts régénérables."""
    (base / "monapp" / "src").mkdir(parents=True)
    (base / "monapp" / "src" / "main.py").write_text("print('hi')")
    (base / "monapp" / ".git").mkdir()
    (base / "monapp" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (base / "monapp" / "node_modules" / "pkg").mkdir(parents=True)
    (base / "monapp" / "node_modules" / "pkg" / "junk.js").write_text("x")
    (base / "monapp" / "__pycache__").mkdir()
    (base / "monapp" / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00")
    (base / "monapp" / "docker-compose.yml").write_text("services: {}\n")


@pytest.mark.asyncio
async def test_apps_code_tar_includes_git_excludes_regenerables(tmp_path, monkeypatch):
    src = tmp_path / "apps_source"
    _make_app_tree(src)
    out_dir = tmp_path / "snapshot_tmp"
    monkeypatch.setattr(snapshot_collectors, "APPS_SOURCE_DIR", src)

    stats = await run_apps_code_tar(out_dir)

    assert stats["apps_count"] == 1
    assert stats["size_bytes"] > 0
    members = [m.name for m in tarfile.open(out_dir / "apps" / "apps_code.tar")]
    assert any(m == "./monapp/.git/HEAD" or m.endswith(".git/HEAD") for m in members)
    assert any(m.endswith("monapp/src/main.py") for m in members)
    assert any(m.endswith("monapp/docker-compose.yml") for m in members)
    assert not any("node_modules" in m for m in members)
    assert not any("__pycache__" in m for m in members)


@pytest.mark.asyncio
async def test_apps_code_tar_excludes_oversized_files_and_build_dirs(tmp_path, monkeypatch):
    """Diagnostic disque 08/11 : le snapshot apps_code ne doit PAS embarquer
    les artefacts de build (target Rust, .venv, build...) ni les fichiers
    individuels > 50MB (modèles gguf, addok.db... couverts par include_app_backups
    ou retéléchargeables)."""
    src = tmp_path / "apps_source"
    app = src / "monapp"
    (app / "src").mkdir(parents=True)
    (app / "src" / "main.py").write_text("print('hi')")
    # Fichier 61MB (sparse : taille réelle sans coût d'écriture disque)
    big = app / "modeles" / "grand_modele.gguf"
    big.parent.mkdir(parents=True)
    with big.open("wb") as fh:
        fh.truncate(61 * 1024 * 1024)
    # Fichier 10MB sous la limite : doit rester dans le tar
    medium = app / "data" / "medium.bin"
    medium.parent.mkdir(parents=True)
    with medium.open("wb") as fh:
        fh.truncate(10 * 1024 * 1024)
    # Répertoires de build/artefacts exclus à toute profondeur
    for dirname in ("target", "build", ".venv", "coverage", ".turbo"):
        artifact_dir = app / dirname
        artifact_dir.mkdir()
        (artifact_dir / "artifact.bin").write_bytes(b"x" * 1024)
    # target imbriqué dans un sous-répertoire de l'app (cas réel du diagnostic)
    nested_target = app / "rust_service" / "target" / "release"
    nested_target.mkdir(parents=True)
    (nested_target / "monapp").write_bytes(b"\x00" * 2048)

    monkeypatch.setattr(snapshot_collectors, "APPS_SOURCE_DIR", src)

    stats = await run_apps_code_tar(tmp_path / "out")

    members = [m.name for m in tarfile.open(tmp_path / "out" / "apps" / "apps_code.tar")]
    assert any(m.endswith("monapp/src/main.py") for m in members)
    assert any(m.endswith("monapp/data/medium.bin") for m in members)
    # Fichier > 50MB exclu
    assert not any("grand_modele.gguf" in m for m in members)
    assert stats["oversized_excluded"] == 1
    # Répertoires de build exclus, y compris imbriqués
    assert not any("/target/" in m or m.endswith("/target") for m in members)
    assert not any("/build/" in m or m.endswith("/build") for m in members)
    assert not any(".venv" in m for m in members)
    assert not any("/coverage" in m for m in members)
    assert not any(".turbo" in m for m in members)


@pytest.mark.asyncio
async def test_apps_code_tar_missing_mount_degrades_gracefully(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_collectors, "APPS_SOURCE_DIR", tmp_path / "inexistant")

    stats = await run_apps_code_tar(tmp_path / "out")

    assert stats["size_bytes"] == 0
    assert stats["apps_count"] == 0
    assert stats["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_apps_code_tar_tolerates_exit_1_file_changed(tmp_path, monkeypatch):
    """tar exit 1 (fichier modifié pendant lecture) ne doit pas faire échouer
    le snapshot si l'archive existe et est non vide (arbre APPS/ vivant)."""
    src = tmp_path / "apps_source"
    (src / "monapp").mkdir(parents=True)
    (src / "monapp" / "main.py").write_text("x")
    out_dir = tmp_path / "out"
    (out_dir / "apps").mkdir(parents=True)
    # Archive réelle non vide (comme si tar avait écrit puis rencontré un fichier changé)
    (out_dir / "apps" / "apps_code.tar").write_bytes(b"TAR" * 100)

    class _FakeProc:
        returncode = 1
        async def communicate(self):
            return b"", b"tar: monapp/main.py: file changed as we read it"

    async def _fake_exec(*cmd, **kwargs):
        return _FakeProc()

    import asyncio as _asyncio
    monkeypatch.setattr(_asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(snapshot_collectors, "APPS_SOURCE_DIR", src)

    stats = await run_apps_code_tar(out_dir)  # ne doit pas lever

    assert stats["size_bytes"] == 300
    assert stats["apps_count"] == 1


@pytest.mark.asyncio
async def test_pg_dump_excludes_own_snapshots_table(tmp_path, monkeypatch):
    """Le dump ne doit PAS embarquer la table hub_snapshots : la propre ligne du
    snapshot y serait capturee au statut 'running' et, au restore, requalifiee
    'failed' par le kill-switch (quirk auto-reference)."""
    captured = {}

    class _FakeProc:
        returncode = 0
        async def communicate(self):
            return b"", b""

    async def _fake_exec(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        # Creer le fichier de sortie attendu par run_pg_dump
        out = Path(cmd[cmd.index("--file") + 1])
        out.write_bytes(b"PGDMP" * 10)
        return _FakeProc()

    import asyncio as _asyncio
    monkeypatch.setattr(_asyncio, "create_subprocess_exec", _fake_exec)

    stats = await run_pg_dump(tmp_path, "postgresql+asyncpg://hub:pw@db:5432/hub_auth")

    assert "--exclude-table=hub_snapshots" in captured["cmd"]
    assert stats["size_bytes"] > 0
