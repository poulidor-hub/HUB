"""PKI interne du Hub : CA + certificats serveur (cryptography)."""
import argparse
import datetime
import ipaddress
import sys
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import get_settings

settings = get_settings()


def _certs_dir() -> Path:
    return Path(settings.certs_dir)


def _ca_paths() -> tuple[Path, Path]:
    d = _certs_dir() / "ca"
    return d / "ca.crt", d / "ca.key"


def _gen_rsa(bits: int) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def _write_key(path: Path, key: rsa.RSAPrivateKey, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    path.chmod(mode)


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    path.chmod(0o644)


def init_ca(force: bool = False) -> None:
    ca_crt, ca_key = _ca_paths()
    if ca_crt.exists() and not force:
        raise FileExistsError(f"CA déjà présente: {ca_crt} (utilisez --force)")
    key = _gen_rsa(settings.pki_key_size)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, settings.pki_ca_common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, settings.pki_ca_organization),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365 * settings.pki_ca_validity_years))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_cert_sign=True, crl_sign=True,
            content_commitment=False, key_encipherment=False, data_encipherment=False,
            key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .sign(key, hashes.SHA256())
    )
    _write_key(ca_key, key, 0o600)
    _write_cert(ca_crt, cert)
