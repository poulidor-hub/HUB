# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

from pathlib import Path
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

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


def test_create_server_cert_signed_by_ca_with_san(certs, monkeypatch):
    monkeypatch.setattr(pki.settings, "hub_host", "192.0.2.12")
    pki.init_ca()
    pki.create_server_cert("hub")
    srv_crt = certs / "server" / "hub" / "server.crt"
    srv_key = certs / "server" / "hub" / "server.key"
    assert srv_crt.exists() and srv_key.exists()
    # Clé privée TLS : propriétaire RW, groupe lecture Nginx, jamais world-readable.
    assert oct(srv_key.stat().st_mode & 0o777) == "0o640"

    ca = x509.load_pem_x509_certificate((certs / "ca" / "ca.crt").read_bytes())
    srv = x509.load_pem_x509_certificate(srv_crt.read_bytes())
    # Le cert serveur est bien signé par la CA (ne lève pas)
    ca.public_key().verify(
        srv.signature, srv.tbs_certificate_bytes,
        padding.PKCS1v15(), srv.signature_hash_algorithm,
    )
    san = srv.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns = san.get_values_for_type(x509.DNSName)
    ips = [str(i) for i in san.get_values_for_type(x509.IPAddress)]
    assert "localhost" in dns and "hub.local" in dns
    assert "192.0.2.12" in ips


def test_create_server_cert_requires_ca(certs):
    with pytest.raises(FileNotFoundError):
        pki.create_server_cert("hub")


def test_cli_init_then_cert(certs, monkeypatch, capsys):
    monkeypatch.setattr(pki.settings, "hub_host", "10.0.0.9")
    pki.main(["init"])
    pki.main(["cert", "create", "hub"])
    assert (certs / "ca" / "ca.crt").exists()
    assert (certs / "server" / "hub" / "server.crt").exists()
    pki.main(["info"])
    out = capsys.readouterr().out
    assert "ca.crt" in out


def test_create_server_cert_custom_cn_san(certs):
    """Cert PhiliaNG : CN et SAN surchargés (proxy.example.lan).

    Le proxy transparent SNI proxy.example.lan a besoin d'un cert dont le CN et le
    SAN portent ce domaine précis — sans fuiter les domaines hub (localhost,
    hub.local…) qui n'ont rien à faire dans un cert PhiliaNG.
    """
    pki.init_ca()
    pki.create_server_cert("philia-ng", cn="proxy.example.lan", san=["proxy.example.lan"])
    srv_crt = certs / "server" / "philia-ng" / "server.crt"
    assert srv_crt.exists()

    ca = x509.load_pem_x509_certificate((certs / "ca" / "ca.crt").read_bytes())
    srv = x509.load_pem_x509_certificate(srv_crt.read_bytes())
    # Toujours signé par la CA (pas d'auto-signé)
    ca.public_key().verify(
        srv.signature, srv.tbs_certificate_bytes,
        padding.PKCS1v15(), srv.signature_hash_algorithm,
    )
    # CN = proxy.example.lan
    cn = srv.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == "proxy.example.lan"
    # SAN = proxy.example.lan uniquement (pas de fuite des domaines hub)
    san = srv.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns = san.get_values_for_type(x509.DNSName)
    assert "proxy.example.lan" in dns
    assert "localhost" not in dns
    assert "hub.local" not in dns


def test_cli_cert_with_cn_san(certs):
    """CLI : `pki.py cert create philia-ng --cn ... --san ...`."""
    pki.main(["init"])
    pki.main(["cert", "create", "philia-ng", "--cn", "proxy.example.lan", "--san", "proxy.example.lan"])
    srv_crt = certs / "server" / "philia-ng" / "server.crt"
    assert srv_crt.exists()
    srv = x509.load_pem_x509_certificate(srv_crt.read_bytes())
    cn = srv.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == "proxy.example.lan"

