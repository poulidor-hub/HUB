"""Tests de l'extraction apps_code au restore (helper pur, sans DB)."""
import tarfile

from app.restore_snapshot import _extract_apps_code


def _make_apps_code_tar(payload_dir, dest):
    with tarfile.open(dest, "w") as t:
        t.add(str(payload_dir), arcname=".")


def test_extract_apps_code_restores_tree(tmp_path, monkeypatch):
    from app import restore_snapshot

    extract_dir = tmp_path / "extract"
    (extract_dir / "apps").mkdir(parents=True)
    payload = tmp_path / "payload"
    (payload / "monapp").mkdir(parents=True)
    (payload / "monapp" / "VERSION").write_text("1.2.3\n")
    _make_apps_code_tar(payload, extract_dir / "apps" / "apps_code.tar")

    target = tmp_path / "apps_source"
    target.mkdir()
    monkeypatch.setattr(restore_snapshot, "APPS_SOURCE_DIR", target)

    assert _extract_apps_code(extract_dir) is True
    assert (target / "monapp" / "VERSION").read_text() == "1.2.3\n"


def test_extract_apps_code_missing_mount_warns_without_error(tmp_path, monkeypatch):
    from app import restore_snapshot

    extract_dir = tmp_path / "extract"
    (extract_dir / "apps").mkdir(parents=True)
    (extract_dir / "apps" / "apps_code.tar").write_bytes(b"fake")

    monkeypatch.setattr(restore_snapshot, "APPS_SOURCE_DIR", tmp_path / "inexistant")

    assert _extract_apps_code(extract_dir) is False


def test_extract_apps_code_no_archive_is_noop(tmp_path):
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    assert _extract_apps_code(extract_dir) is False
