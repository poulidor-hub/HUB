from pathlib import Path
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes

import pki


@pytest.fixture
def certs(tmp_path, monkeypatch):
    monkeypatch.setattr(pki, "_certs_dir", lambda: tmp_path)
    return tmp_path


def test_init_ca_creates_valid_ca(certs):
    pki.init_ca()
    ca_crt = certs / "ca" / "ca.crt"
    ca_key = certs / "ca" / "ca.key"
    assert ca_crt.exists() and ca_key.exists()
    assert oct(ca_key.stat().st_mode & 0o777) == "0o600"
    cert = x509.load_pem_x509_certificate(ca_crt.read_bytes())
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is True


def test_init_ca_idempotent_without_force(certs):
    pki.init_ca()
    with pytest.raises(FileExistsError):
        pki.init_ca()
    pki.init_ca(force=True)  # ne lève pas
