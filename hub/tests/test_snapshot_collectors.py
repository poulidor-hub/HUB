"""Tests du collector apps_code (capture du code APPS/ pour le PRA)."""
import tarfile

import pytest

from app import snapshot_collectors
from app.snapshot_collectors import run_apps_code_tar


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
