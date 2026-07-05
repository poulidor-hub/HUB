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

    VALID_FERNET = "jUjf0DHpQW3QM5eofdrp_6FEvSr9pI43P-Y33o9Y-TA="

    def test_set_origins_ok_in_production(self):
        validate_security_config(
            make_settings(app_env="production", allowed_origins="https://hub.local,https://192.0.2.10",
                          secrets_encryption_key=self.VALID_FERNET)
        )

    def test_origins_list_property_parses_and_strips(self):
        s = make_settings(allowed_origins=" https://a , https://b ,")
        assert s.allowed_origins_list == ["https://a", "https://b"]


class TestSecrets:
    VALID_FERNET = "jUjf0DHpQW3QM5eofdrp_6FEvSr9pI43P-Y33o9Y-TA="

    def test_invalid_fernet_key_always_fails(self):
        with pytest.raises(ConfigValidationError, match="Fernet"):
            validate_security_config(
                make_settings(app_env="development", secrets_encryption_key="not-a-fernet-key")
            )

    def test_valid_fernet_key_ok_in_production(self):
        validate_security_config(
            make_settings(
                app_env="production",
                allowed_origins="https://hub.local",
                secrets_encryption_key=self.VALID_FERNET,
            )
        )

    def test_empty_key_fails_in_production_without_plaintext_flag(self):
        with pytest.raises(ConfigValidationError, match="SECRETS_ENCRYPTION_KEY"):
            validate_security_config(
                make_settings(app_env="production", allowed_origins="https://hub.local",
                              secrets_encryption_key="")
            )

    def test_empty_key_ok_in_production_with_plaintext_flag(self):
        validate_security_config(
            make_settings(app_env="production", allowed_origins="https://hub.local",
                          secrets_encryption_key="", allow_plaintext_secrets=True)
        )

    def test_empty_key_warns_in_development(self):
        warns = validate_security_config(make_settings(app_env="development", secrets_encryption_key=""))
        assert any("SECRETS_ENCRYPTION_KEY" in w for w in warns)


class TestPasswords:
    PROD = dict(
        app_env="production",
        allowed_origins="https://hub.local",
        secrets_encryption_key="jUjf0DHpQW3QM5eofdrp_6FEvSr9pI43P-Y33o9Y-TA=",
        admin_password="aStrongAdminPass1",
        database_url="postgresql+asyncpg://hubadmin:aStrongDbPass1@db:5432/hub_auth",
    )
    DEV = dict(
        app_env="development",
        admin_password="aStrongAdminPass1",
        database_url="postgresql+asyncpg://hubadmin:aStrongDbPass1@db:5432/hub_auth",
        secrets_encryption_key="",
        allowed_origins="https://hub.local",
    )

    @pytest.mark.parametrize("weak", ["admin", "password", "admin123", "CHANGEZ_MOI", "short", "hub_secure_password_2025"])
    def test_weak_admin_password_warns_in_dev(self, weak):
        opts = dict(self.DEV, admin_password=weak)
        warns = validate_security_config(make_settings(**opts))
        assert any("ADMIN_PASSWORD" in w for w in warns)

    @pytest.mark.parametrize("weak", ["admin", "password", "admin123", "short", "hub_secure_password_2025"])
    def test_weak_admin_password_blocks_in_production(self, weak):
        opts = dict(self.PROD, admin_password=weak)
        with pytest.raises(ConfigValidationError, match="ADMIN_PASSWORD"):
            validate_security_config(make_settings(**opts))

    def test_strong_passwords_silent_in_production(self):
        warns = validate_security_config(make_settings(**self.PROD))
        assert not warns

    def test_weak_db_password_warns_in_dev(self):
        opts = dict(
            self.DEV,
            database_url="postgresql+asyncpg://hubadmin:CHANGEZ_MOI_mot_de_passe_securise@db:5432/hub_auth",
        )
        warns = validate_security_config(make_settings(**opts))
        assert any("base de données" in w for w in warns)

    def test_weak_db_password_blocks_in_production(self):
        opts = dict(
            self.PROD,
            database_url="postgresql+asyncpg://hubadmin:short@db:5432/hub_auth",
        )
        with pytest.raises(ConfigValidationError, match="base de données"):
            validate_security_config(make_settings(**opts))

    def test_default_compromised_db_password_blocks_in_production(self):
        opts = dict(
            self.PROD,
            database_url="postgresql+asyncpg://hubadmin:hub_secure_password_2025@db:5432/hub_auth",
        )
        with pytest.raises(ConfigValidationError):
            validate_security_config(make_settings(**opts))

    def test_skip_password_check_silences_all(self):
        opts = dict(self.DEV, admin_password="admin", skip_password_check=True,
                    database_url="postgresql+asyncpg://hubadmin:password@db:5432/hub_auth",
                    secrets_encryption_key="jUjf0DHpQW3QM5eofdrp_6FEvSr9pI43P-Y33o9Y-TA=")
        warns = validate_security_config(make_settings(**opts))
        assert not warns


from types import SimpleNamespace


def test_build_cors_config_regex_only_does_not_inject_localhost():
    """P3: config regex-only ne doit jamais injecter localhost."""
    from app.config import build_cors_config
    s = SimpleNamespace(
        allowed_origins="",
        allowed_origin_regex=r"^https?://192\.168\.1\.\d{1,3}(:\d+)?$",
        allowed_origins_list=[],
    )
    origins, regex, creds = build_cors_config(s)
    assert "http://localhost" not in origins
    assert origins == []
    assert regex is not None
    assert creds is True


def test_build_cors_config_dev_fallback_allows_localhost_without_credentials():
    """P3: dev (rien configuré) -> localhost mais credentials désactivés."""
    from app.config import build_cors_config
    s = SimpleNamespace(
        allowed_origins="",
        allowed_origin_regex="",
        allowed_origins_list=[],
    )
    origins, regex, creds = build_cors_config(s)
    assert origins == ["http://localhost"]
    assert regex is None
    assert creds is False


def test_build_cors_config_explicit_origins_enable_credentials():
    """P3: origines explicites -> credentials activés."""
    from app.config import build_cors_config
    s = SimpleNamespace(
        allowed_origins="https://hub.local,https://192.168.1.10",
        allowed_origin_regex="",
        allowed_origins_list=["https://hub.local", "https://192.168.1.10"],
    )
    origins, regex, creds = build_cors_config(s)
    assert origins == ["https://hub.local", "https://192.168.1.10"]
    assert creds is True
