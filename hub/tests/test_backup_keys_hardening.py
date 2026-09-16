"""Hardening clés backup : CRITICAL en production si génération à chaud (spec §4.3)."""
import logging

import app.config as cfg


def test_generation_backup_keys_logs_critical(monkeypatch, tmp_path, caplog):
    secrets_dir = tmp_path / "secrets"
    monkeypatch.setattr(cfg, "_SECRETS_DIR", secrets_dir)
    monkeypatch.setenv("APP_ENV", "production")
    with caplog.at_level(logging.CRITICAL, logger="app.config"):
        cfg.ensure_backup_keys_exist()
    assert (secrets_dir / "backup_private_key.pem").exists()
    assert (secrets_dir / "backup_public_key.pem").exists()
    assert any(r.levelno >= logging.CRITICAL for r in caplog.records)


def test_backup_keys_existantes_ne_loggent_pas_critical(monkeypatch, tmp_path, caplog):
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    priv, pub = cfg.generate_rsa_keys()
    (secrets_dir / "backup_private_key.pem").write_text(priv)
    (secrets_dir / "backup_public_key.pem").write_text(pub)
    monkeypatch.setattr(cfg, "_SECRETS_DIR", secrets_dir)
    with caplog.at_level(logging.CRITICAL, logger="app.config"):
        cfg.ensure_backup_keys_exist()
    assert not any(r.levelno >= logging.CRITICAL for r in caplog.records)
