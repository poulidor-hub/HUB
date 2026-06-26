from app.config import Settings


def test_pki_settings_defaults():
    s = Settings()
    assert s.hub_https_enabled is True
    assert s.hub_https_port == 443
    assert s.certs_dir == "/code/certs"
    assert s.pki_key_size == 4096
    assert s.pki_cert_validity_days == 365
    assert "localhost" in s.pki_cert_domains
