# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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


def _values_to_san(raw: list[str]) -> list:
    """Convertit une liste de chaînes (DNS ou IP) en entrées SAN x509, dédoublonnées."""
    entries: list = []
    seen: set = set()
    for value in raw:
        try:
            obj = x509.IPAddress(ipaddress.ip_address(value))
            key = ("ip", str(value))
        except ValueError:
            obj = x509.DNSName(value)
            key = ("dns", value)
        if key not in seen:
            seen.add(key)
            entries.append(obj)
    return entries


def _san_entries() -> list:
    raw: list = []
    for d in [x.strip() for x in settings.pki_cert_domains.split(",") if x.strip()]:
        raw.append(d)
    for ip in [x.strip() for x in settings.pki_cert_ips.split(",") if x.strip()]:
        raw.append(ip)
    host = settings.get_hub_host()
    if host:
        raw.append(host)
    return _values_to_san(raw)


def create_server_cert(
    name: str = "hub",
    force: bool = False,
    cn: str | None = None,
    san: list[str] | None = None,
) -> None:
    """Crée un certificat serveur signé par la CA.

    Args:
        name: nom logique du cert (sous-dossier de certs/server/).
        force: régénère même si le cert existe déjà.
        cn: Common Name explicite. Par défaut le 1er domaine de PKI_CERT_DOMAINS.
        san: SAN explicite (liste de DNS/IP). Par défaut PKI_CERT_DOMAINS + hub_host.
            Permet de générer un cert pour un domaine précis (ex: PhiliaNG) sans
            fuiter les domaines hub.
    """
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
    common_name = cn if cn is not None else _server_common_name()
    san_entries = _values_to_san(san) if san is not None else _san_entries()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=settings.pki_cert_validity_days))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    # server.key est lisible par le groupe partagé avec le frontend nginx.
    # La ca_key reste en 0o600 car seul le hub-api en a besoin.
    _write_key(srv_key, key, 0o640)
    _write_cert(srv_crt, cert)


def ensure_wildcard_cert(zone: str) -> dict:
    """Chantier B — garantit un cert wildcard *.<zone> signé par la CA interne.

    Réémet si le cert est absent OU si son SAN ne couvre pas la zone demandée
    (changement de zone). N'est jamais appelé au boot sans zone configurée.
    Retourne {cn, issued_at, expires_at} (datetimes UTC, lecture du cert).
    """
    crt_path, key_path = _server_paths("hub-dns-wildcard")
    wanted = [f"*.{zone}", zone]

    def _info(path: Path) -> dict:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
        nb = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before
        na = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
        return {
            "cn": cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
            "issued_at": nb,
            "expires_at": na,
        }

    if crt_path.exists():
        cert = x509.load_pem_x509_certificate(crt_path.read_bytes())
        san = cert.extensions.get_extension_for_oid(
            x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        ).value
        names = set(san.get_values_for_type(x509.DNSName))
        if set(wanted) <= names:
            return _info(crt_path)

    hub_ip = settings.get_hub_host()
    create_server_cert(
        name="hub-dns-wildcard", force=True, cn=f"*.{zone}",
        san=([*wanted, hub_ip] if hub_ip else wanted),
    )
    return _info(crt_path)


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
    p_create.add_argument("--cn", help="Common Name (défaut: 1er domaine de PKI_CERT_DOMAINS)")
    p_create.add_argument(
        "--san", action="append", default=None, metavar="DNS_OR_IP",
        help="SAN explicite (DNS ou IP). Répétable. Défaut: PKI_CERT_DOMAINS + hub_host.",
    )

    sub.add_parser("info", help="Afficher l'empreinte de la CA")

    args = parser.parse_args(argv)
    if args.cmd == "init":
        init_ca(force=args.force)
        print("✅ CA générée")
    elif args.cmd == "cert" and args.cert_cmd == "create":
        create_server_cert(args.name, force=args.force, cn=args.cn, san=args.san)
        print(f"✅ Certificat serveur '{args.name}' généré")
    elif args.cmd == "info":
        info()


if __name__ == "__main__":
    main(sys.argv[1:])
