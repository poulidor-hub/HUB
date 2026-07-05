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


def _server_paths(name: str = "hub") -> tuple[Path, Path]:
    d = _certs_dir() / "server" / name
    return d / "server.crt", d / "server.key"


def _server_common_name() -> str:
    for d in settings.pki_cert_domains.split(","):
        d = d.strip()
        if d:
            return d
    return "hub"


def _san_entries() -> list:
    raw: list = []
    for d in [x.strip() for x in settings.pki_cert_domains.split(",") if x.strip()]:
        raw.append(d)
    for ip in [x.strip() for x in settings.pki_cert_ips.split(",") if x.strip()]:
        raw.append(ip)
    host = settings.get_hub_host()
    if host:
        raw.append(host)

    entries: list = []
    seen: set = set()
    for value in raw:
        try:
            obj = x509.IPAddress(ipaddress.ip_address(value))
            key = ("ip", value)
        except ValueError:
            obj = x509.DNSName(value)
            key = ("dns", value)
        if key not in seen:
            seen.add(key)
            entries.append(obj)
    return entries


def create_server_cert(name: str = "hub", force: bool = False) -> None:
    ca_crt_path, ca_key_path = _ca_paths()
    if not ca_crt_path.exists() or not ca_key_path.exists():
        raise FileNotFoundError("CA absente — lancez d'abord `pki.py init`")
    srv_crt, srv_key = _server_paths(name)
    if srv_crt.exists() and not force:
        raise FileExistsError(f"Cert serveur déjà présent: {srv_crt} (utilisez --force)")

    ca_cert = x509.load_pem_x509_certificate(ca_crt_path.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
    key = _gen_rsa(settings.pki_key_size)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _server_common_name())]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=settings.pki_cert_validity_days))
        .add_extension(x509.SubjectAlternativeName(_san_entries()), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_key(srv_key, key, 0o600)
    _write_cert(srv_crt, cert)


def info() -> None:
    ca_crt, _ = _ca_paths()
    if not ca_crt.exists():
        print("CA absente. Lancez `python pki.py init`.")
        return
    cert = x509.load_pem_x509_certificate(ca_crt.read_bytes())
    fingerprint = cert.fingerprint(hashes.SHA256()).hex(":")
    print(f"CA: {ca_crt}")
    print(f"Empreinte SHA-256: {fingerprint}")
    print(f"➡️  Importez {ca_crt} dans le magasin de confiance des postes/navigateurs.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pki.py", description="PKI interne du Hub")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Générer la CA interne")
    p_init.add_argument("--force", action="store_true")

    p_cert = sub.add_parser("cert", help="Gestion des certificats serveur")
    cert_sub = p_cert.add_subparsers(dest="cert_cmd", required=True)
    p_create = cert_sub.add_parser("create", help="Créer un certificat serveur")
    p_create.add_argument("name")
    p_create.add_argument("--force", action="store_true")

    sub.add_parser("info", help="Afficher l'empreinte de la CA")

    args = parser.parse_args(argv)
    if args.cmd == "init":
        init_ca(force=args.force)
        print("✅ CA générée")
    elif args.cmd == "cert" and args.cert_cmd == "create":
        create_server_cert(args.name, force=args.force)
        print(f"✅ Certificat serveur '{args.name}' généré")
    elif args.cmd == "info":
        info()


if __name__ == "__main__":
    main(sys.argv[1:])
