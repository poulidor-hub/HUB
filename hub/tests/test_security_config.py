"""Tests de la validation de configuration de sécurité (config.py)."""
import pytest

from app.config import (
    Settings,
    ConfigValidationError,
    is_production,
    validate_security_config,
)


def make_settings(**overrides) -> Settings:
    """Construit un Settings isolé (sans lire le .env réel)."""
    base = dict(
        app_env="development",
        admin_password="aStrongAdminPass1",
        database_url="postgresql+asyncpg://hubadmin:aStrongDbPass1@db:5432/hub_auth",
        secrets_encryption_key="",
        allowed_origins="https://hub.local",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestEnvMode:
    def test_is_production_true(self):
        assert is_production(make_settings(app_env="production")) is True

    def test_is_production_false_for_development(self):
        assert is_production(make_settings(app_env="development")) is False

    def test_is_production_case_insensitive(self):
        assert is_production(make_settings(app_env="Production")) is True


class TestDispatcher:
    def test_returns_list(self):
        result = validate_security_config(make_settings())
        assert isinstance(result, list)

    def test_dev_mode_never_raises(self):
        validate_security_config(make_settings(app_env="development"))


class TestAllowedOrigins:
    def test_empty_origins_fails_in_production(self):
        with pytest.raises(ConfigValidationError, match="ALLOWED_ORIGINS"):
            validate_security_config(make_settings(app_env="production", allowed_origins=""))

    def test_empty_origins_ok_in_development(self):
        validate_security_config(make_settings(app_env="development", allowed_origins=""))

    def test_set_origins_ok_in_production(self):
        validate_security_config(
            make_settings(app_env="production", allowed_origins="https://hub.local,https://192.0.2.10")
        )

    def test_origins_list_property_parses_and_strips(self):
        s = make_settings(allowed_origins=" https://a , https://b ,")
        assert s.allowed_origins_list == ["https://a", "https://b"]
