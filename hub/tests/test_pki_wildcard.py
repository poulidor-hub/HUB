"""Chantier B Task 4 — wildcard PKI : ensure_wildcard_cert(zone).

Les certs vivent dans tmp_path (fixture settings) avec une CA fraîche :
chaque test est autonome, aucune écriture dans certs/ réel.
"""

import datetime

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtensionOID

from app.config import get_settings
import pki

ZONE = "hub.example.lan"


@pytest.fixture
def certs_tmp(tmp_path, settings):
    s = get_settings()
    saved = (s.certs_dir, s.pki_key_size)
    s.certs_dir = str(tmp_path / "certs")
    s.pki_key_size = 2048
    # resynchronisation : un test antérieur peut avoir fait get_settings.cache_clear()
    # → pki.settings (singleton capturé à l'import) serait désynchronisé
    pki.settings = s
    pki.init_ca(force=True)
    yield s
    s.certs_dir, s.pki_key_size = saved
    pki.settings = s


def _dns_names(crt_path):
    cert = x509.load_pem_x509_certificate(crt_path.read_bytes())
    san = cert.extensions.get_extension_for_oid(
        ExtensionOID.SUBJECT_ALTERNATIVE_NAME
    ).value
    return set(san.get_values_for_type(x509.DNSName)), cert


def test_ensure_emits_wildcard_with_expected_san(certs_tmp):
    info = pki.ensure_wildcard_cert(ZONE)

    crt = certs_tmp.certs_dir + "/server/hub-dns-wildcard/server.crt"
    import pathlib
    names, cert = _dns_names(pathlib.Path(crt))
    assert f"*.{ZONE}" in names
    assert ZONE in names
    assert info["cn"] == f"*.{ZONE}"
    assert isinstance(info["expires_at"], datetime.datetime)


def test_ensure_is_idempotent(certs_tmp):
    import pathlib
    first = pki.ensure_wildcard_cert(ZONE)
    crt = pathlib.Path(certs_tmp.certs_dir, "server/hub-dns-wildcard/server.crt")
    serial_first = x509.load_pem_x509_certificate(crt.read_bytes()).serial_number

    second = pki.ensure_wildcard_cert(ZONE)  # même zone → pas de réémission
    serial_second = x509.load_pem_x509_certificate(crt.read_bytes()).serial_number

    assert serial_first == serial_second
    assert second["cn"] == first["cn"]


def test_ensure_reemits_when_zone_changes(certs_tmp):
    import pathlib
    pki.ensure_wildcard_cert(ZONE)
    crt = pathlib.Path(certs_tmp.certs_dir, "server/hub-dns-wildcard/server.crt")
    serial_before = x509.load_pem_x509_certificate(crt.read_bytes()).serial_number

    other = "hub.example.internal"
    info = pki.ensure_wildcard_cert(other)
    names, _ = _dns_names(crt)

    assert x509.load_pem_x509_certificate(crt.read_bytes()).serial_number != serial_before
    assert f"*.{other}" in names
    assert info["cn"] == f"*.{other}"
