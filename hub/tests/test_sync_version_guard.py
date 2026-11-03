"""Garde-fous anti-écrasement croisé de sync_version.py (incident 2026-10-04).

Deux sessions poussant des releases en parallèle ont produit un doublon de
numéro puis une régression de VERSION. `sync_version.py bump` doit refuser :
1. de bumpper si origin est strictement en avance sur le local ;
2. de prendre un numéro d'entrée CHANGELOG déjà occupé.

Dégradation voulue : origin indéterminable (hors-ligne) → garde silencieux.
"""
import subprocess
import sys
from pathlib import Path

import pytest

# HOST_ONLY : sync_version.py vit à la racine du checkout, jamais copiée dans
# l'image Docker — run hôte uniquement.
import os as _os
if not _os.path.exists(str(Path(__file__).resolve().parents[2] / "sync_version.py")):
    pytest.skip("sync_version.py hors image — run hôte", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import sync_version as sv


# ── get_origin_version : dégradation silencieuse ──────────────────────

def test_get_origin_version_none_sans_upstream(monkeypatch):
    """Pas d'upstream (branche locale seule) → None, jamais d'exception."""
    def fake_run(cmd, **kwargs):
        class Out:
            returncode = 1
            stdout = ""
        return Out()
    monkeypatch.setattr(sv.subprocess, "run", fake_run)
    assert sv.get_origin_version() is None


def test_get_origin_version_none_sur_timeout(monkeypatch):
    monkeypatch.setattr(sv.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("git", 10)))
    assert sv.get_origin_version() is None


# ── check_origin_not_ahead ─────────────────────────────────────────────

def test_bloque_si_origin_en_avance(monkeypatch, capsys):
    monkeypatch.setattr(sv, "get_origin_version", lambda: "3.62.9")
    assert sv.check_origin_not_ahead("3.62.8") is False
    out = capsys.readouterr().out
    assert "COLLISION DE RELEASE" in out
    assert "git pull" in out


def test_passe_si_origin_egal(monkeypatch):
    monkeypatch.setattr(sv, "get_origin_version", lambda: "3.62.9")
    assert sv.check_origin_not_ahead("3.62.9") is True


def test_passe_si_origin_derriere_avec_info(monkeypatch, capsys):
    monkeypatch.setattr(sv, "get_origin_version", lambda: "3.62.7")
    assert sv.check_origin_not_ahead("3.62.9") is True
    assert "en avance sur origin" in capsys.readouterr().out


def test_passe_en_silencieux_si_origin_indeterminable(monkeypatch, capsys):
    monkeypatch.setattr(sv, "get_origin_version", lambda: None)
    assert sv.check_origin_not_ahead("3.62.8") is True
    assert "silencieux" in capsys.readouterr().out


# ── check_changelog_collision ──────────────────────────────────────────

def test_bloque_si_numero_deja_pris(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sv, "CHANGELOG_FILE", tmp_path / "CHANGELOG.md")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [3.62.10] - 4 octobre 2026\n\ncontenu autre session\n")
    assert sv.check_changelog_collision("3.62.10") is False
    assert "existe déjà" in capsys.readouterr().out


def test_passe_si_numero_libre(monkeypatch, tmp_path):
    monkeypatch.setattr(sv, "CHANGELOG_FILE", tmp_path / "CHANGELOG.md")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [3.62.9] - 4 octobre 2026\n")
    assert sv.check_changelog_collision("3.62.10") is True


def test_passe_si_changelog_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(sv, "CHANGELOG_FILE", tmp_path / "CHANGELOG.md")
    assert sv.check_changelog_collision("3.62.10") is True


# ── Intégration : la commande bump doit sortir en échec (code 2) ──────

def test_bump_exit2_si_origin_en_avance(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sv, "VERSION_FILE", tmp_path / "VERSION")
    (tmp_path / "VERSION").write_text("3.62.8\n")
    monkeypatch.setattr(sv, "check_origin_not_ahead", lambda v: False)
    monkeypatch.setattr(sys, "argv", ["sync_version.py", "bump", "patch"])
    with pytest.raises(SystemExit) as exc:
        sv.main()
    assert exc.value.code == 2
    # Aucune écriture ne doit avoir eu lieu
    assert (tmp_path / "VERSION").read_text().strip() == "3.62.8"
