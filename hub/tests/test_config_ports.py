# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests des champs hub_api_port/hub_frontend_port et de l'instance_id."""
import importlib


def test_settings_default_ports(monkeypatch):
    # Adaptation banc : les vars HUB_API_PORT/HUB_FRONTEND_PORT sont injectées
    # dans l'environnement (docker-compose) — on les retire pour tester les
    # défauts du champ Settings de façon hermétique.
    monkeypatch.delenv("HUB_API_PORT", raising=False)
    monkeypatch.delenv("HUB_FRONTEND_PORT", raising=False)
    from app.config import Settings
    s = Settings(admin_password="x")
    assert s.hub_api_port == 8000
    assert s.hub_frontend_port == 80


def test_settings_ports_from_env(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("HUB_API_PORT", "18000")
    monkeypatch.setenv("HUB_FRONTEND_PORT", "18080")
    s = Settings(admin_password="x")
    assert s.hub_api_port == 18000
    assert s.hub_frontend_port == 18080


def test_get_instance_id_persistent(tmp_path, monkeypatch):
    """instance_id : crée le fichier s'il est absent, le relit s'il existe."""
    from app import config
    secrets_dir = tmp_path / "secrets"
    monkeypatch.setattr(config, "_SECRETS_DIR", secrets_dir)
    config.get_instance_id.cache_clear()
    monkeypatch.setattr(config, "_FALLBACK_INSTANCE_ID", None)
    first = config.get_instance_id()
    assert first and len(first) == 32  # uuid4().hex
    assert (secrets_dir / "hub_instance_id").exists()
    # second appel = même valeur (cache)
    assert config.get_instance_id() == first
    # nouveau process (cache vidé) relit le fichier
    config.get_instance_id.cache_clear()
    assert config.get_instance_id() == first


def test_get_instance_id_survives_unwritable_file(tmp_path, monkeypatch):
    """Chemin d'instance impossible à créer (parent est un fichier) : pas d'exception."""
    from app import config
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setattr(config, "_SECRETS_DIR", blocker / "secrets")
    config.get_instance_id.cache_clear()
    monkeypatch.setattr(config, "_FALLBACK_INSTANCE_ID", None)
    value = config.get_instance_id()
    assert isinstance(value, str) and len(value) == 32
    config.get_instance_id.cache_clear()
    assert config.get_instance_id() == value  # stable en mémoire pour ce process
