# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests de scripts/purge_orphan_backups.py — logique pure find_orphans.

Le script pilote docker exec pour agir sur le conteneur hub ; seule la
logique pure (classification orphelin / référencé / trop récent) est testée
ici, les E/S conteneur étant couvertes par le dry-run réel en exploitation.
"""
import importlib.util
import time
from pathlib import Path

import pytest


def _find_purge_script() -> Path:
    """Localise scripts/purge_orphan_backups.py selon l'environnement d'exécution.

    - Hôte        : <repo>/hub/tests/…  →  <repo>/scripts/purge_orphan_backups.py
    - Conteneur   : /code/tests/…        →  /code/scripts/purge_orphan_backups.py
      (la cible `make test-docker` copie le script à côté de /code/tests —
      le contexte image ne contient que ./hub, pas le dossier scripts/ racine)
    """
    here = Path(__file__).resolve()
    for base in here.parents:
        candidate = base / "scripts" / "purge_orphan_backups.py"
        if candidate.exists():
            return candidate
    # Comportement historique (échec explicite dans test_script_exists)
    return here.parents[2] / "scripts" / "purge_orphan_backups.py"


SCRIPT = _find_purge_script()


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("purge_orphan_backups", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_exists():
    assert SCRIPT.exists()


def test_orphan_when_not_referenced(mod):
    files = {"exemple-app/backup_a.zip": (10, 100.0)}
    assert mod.find_orphans(files, set(), cutoff_ts=200.0) == [
        {"path": "exemple-app/backup_a.zip", "size": 10, "mtime": 100}]


def test_referenced_file_is_kept(mod):
    files = {"exemple-app/backup_a.zip": (10, 100.0),
             "exemple-app/backup_a.encrypted": (12, 100.0)}
    referenced = {"exemple-app/backup_a.zip", "exemple-app/backup_a.encrypted"}
    assert mod.find_orphans(files, referenced, cutoff_ts=200.0) == []


def test_recent_file_is_never_orphan(mod):
    """Garde anti-course : un backup en cours d'écriture n'est pas orphelin."""
    files = {"hub/backup_new.zip": (10, time.time())}
    assert mod.find_orphans(files, set(), cutoff_ts=time.time() - 3600) == []


def test_ordering_is_stable_and_deterministic(mod):
    files = {
        "b/backup_2.zip": (5, 50.0),
        "a/backup_1.zip": (3, 40.0),
        "c/deep/nested.zip": (9, 30.0),
    }
    result = mod.find_orphans(files, set(), cutoff_ts=100.0)
    assert [item["path"] for item in result] == [
        "a/backup_1.zip", "b/backup_2.zip", "c/deep/nested.zip"]


def test_null_reference_equivalent_to_empty(mod):
    """Les paths NULL de la DB ne doivent jamais masquer un orphelin."""
    files = {"dead-app/backup_old.zip": (7, 10.0)}
    referenced = {""}  # jamais vide-chaîne après filtrage côté conteneur
    referenced.discard("")
    assert mod.find_orphans(files, referenced, cutoff_ts=100.0) != []
