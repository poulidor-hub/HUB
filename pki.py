#!/usr/bin/env python3
"""
PKI Locale pour Hub - Gestion de Certificats SSL/TLS
====================================================

Ce script permet de gérer une Autorité de Certification (CA) locale
pour sécuriser les communications HTTPS du Hub et de ses applications.

Usage:
    python pki.py init                    # Initialiser la CA (première fois)
    python pki.py status                  # Afficher l'état de la PKI
    python pki.py cert create <nom>       # Créer un certificat serveur
    python pki.py cert list               # Lister les certificats
    python pki.py cert revoke <nom>       # Révoquer un certificat
    python pki.py cert renew <nom>        # Renouveler un certificat
    python pki.py export-ca               # Exporter la CA pour installation
    python pki.py export-ca --format=der  # Exporter en format DER (Windows)

Configuration via .env:
    PKI_CA_VALIDITY_YEARS=10          # Durée validité CA (défaut: 10)
    PKI_CERT_VALIDITY_DAYS=365        # Durée validité certificats (défaut: 365)
    PKI_CA_COMMON_NAME=Hub Local CA   # Nom de la CA
    PKI_CERT_DOMAINS=localhost,hub.local,hub-frontend  # Domaines par défaut
    PKI_CERT_IPS=                      # IPs supplémentaires (auto-détectées sinon)

Auteur: Hub Team
Date: Janvier 2026
"""

import argparse
import json
import os
import sys
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import ipaddress

# Cryptography imports
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

# ===== CONSTANTES =====
ROOT_DIR = Path(__file__).parent
CERTS_DIR = ROOT_DIR / "certs"
CA_DIR = CERTS_DIR / "ca"
SERVER_DIR = CERTS_DIR / "server"
CLIENTS_DIR = CERTS_DIR / "clients"
REGISTRY_FILE = CERTS_DIR / "registry.json"

# Couleurs pour le terminal
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'


def log_info(msg: str):
    print(f"{Colors.GREEN}✅{Colors.END} {msg}")


def log_warn(msg: str):
    print(f"{Colors.YELLOW}⚠️{Colors.END}  {msg}")


def log_error(msg: str):
    print(f"{Colors.RED}❌{Colors.END} {msg}")


def log_step(msg: str):
    print(f"{Colors.BLUE}🔹{Colors.END} {msg}")


# ===== CONFIGURATION =====
def load_env_config() -> dict:
    """Charge la configuration depuis les variables d'environnement ou .env"""
    env_file = ROOT_DIR / ".env"
    config = {}
    
    # Charger depuis .env si présent
    if env_file.exists():
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    config[key.strip()] = value.strip().strip('"').strip("'")
    
    # Les variables d'environnement ont la priorité
    config.update(os.environ)
    
    return {
        'ca_validity_years': int(config.get('PKI_CA_VALIDITY_YEARS', '10')),
        'cert_validity_days': int(config.get('PKI_CERT_VALIDITY_DAYS', '365')),
        'ca_common_name': config.get('PKI_CA_COMMON_NAME', 'Hub Local CA'),
        'ca_organization': config.get('PKI_CA_ORGANIZATION', 'Hub Internal'),
        'cert_domains': [d.strip() for d in config.get('PKI_CERT_DOMAINS', 'localhost,hub.local,hub-frontend').split(',') if d.strip()],
        'cert_ips': [ip.strip() for ip in config.get('PKI_CERT_IPS', '').split(',') if ip.strip()],
        'key_size': int(config.get('PKI_KEY_SIZE', '4096')),
    }


def get_server_ip() -> str:
    """Détecte automatiquement l'IP du serveur"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ===== REGISTRE DES CERTIFICATS =====
def load_registry() -> dict:
    """Charge le registre des certificats"""
    if REGISTRY_FILE.exists():
        with open(REGISTRY_FILE, 'r') as f:
            return json.load(f)
    return {
        'ca': None,
        'certificates': {},
        'revoked': []
    }


def save_registry(registry: dict):
    """Sauvegarde le registre des certificats"""
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_FILE, 'w') as f:
        json.dump(registry, f, indent=2, default=str)


# ===== GÉNÉRATION DE CLÉS =====
def generate_private_key(key_size: int = 4096) -> rsa.RSAPrivateKey:
    """Génère une clé privée RSA"""
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
        backend=default_backend()
    )


def save_private_key(key: rsa.RSAPrivateKey, path: Path):
    """Sauvegarde une clé privée en PEM"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
    # Permissions restrictives sur la clé privée
    os.chmod(path, 0o600)


def save_certificate(cert: x509.Certificate, path: Path):
    """Sauvegarde un certificat en PEM"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


# ===== AUTORITÉ DE CERTIFICATION =====
def init_ca(config: dict, force: bool = False) -> bool:
    """
    Initialise l'Autorité de Certification locale.
    
    Args:
        config: Configuration PKI
        force: Si True, réinitialise même si une CA existe
    
    Returns:
        True si succès
    """
    ca_key_path = CA_DIR / "ca.key"
    ca_cert_path = CA_DIR / "ca.crt"
    
    if ca_cert_path.exists() and not force:
        log_warn("Une CA existe déjà. Utilisez --force pour la réinitialiser.")
        return False
    
    print(f"\n{Colors.BOLD}🔐 Initialisation de l'Autorité de Certification{Colors.END}\n")
    
    # Générer la clé privée de la CA
    log_step("Génération de la clé privée CA...")
    ca_key = generate_private_key(config['key_size'])
    
    # Créer le certificat auto-signé de la CA
    log_step("Création du certificat racine...")
    
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "FR"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Local"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Internal"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, config['ca_organization']),
        x509.NameAttribute(NameOID.COMMON_NAME, config['ca_common_name']),
    ])
    
    now = datetime.now(timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=config['ca_validity_years'] * 365))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256(), default_backend())
    )
    
    # Sauvegarder
    log_step("Sauvegarde des fichiers CA...")
    save_private_key(ca_key, ca_key_path)
    save_certificate(ca_cert, ca_cert_path)
    
    # Mettre à jour le registre
    registry = load_registry()
    registry['ca'] = {
        'created': now.isoformat(),
        'expires': (now + timedelta(days=config['ca_validity_years'] * 365)).isoformat(),
        'common_name': config['ca_common_name'],
        'organization': config['ca_organization'],
        'serial': str(ca_cert.serial_number),
    }
    save_registry(registry)
    
    log_info(f"CA initialisée avec succès !")
    print(f"\n   📁 Clé privée : {Colors.CYAN}{ca_key_path}{Colors.END}")
    print(f"   📜 Certificat : {Colors.CYAN}{ca_cert_path}{Colors.END}")
    print(f"   ⏰ Validité   : {config['ca_validity_years']} ans")
    print(f"\n   {Colors.YELLOW}⚠️  Gardez ca.key en SÉCURITÉ - ne la partagez JAMAIS !{Colors.END}")
    
    return True


def load_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """
    Charge la CA depuis les fichiers.
    
    Returns:
        tuple: (clé_privée, certificat)
    
    Raises:
        FileNotFoundError: Si la CA n'existe pas
    """
    ca_key_path = CA_DIR / "ca.key"
    ca_cert_path = CA_DIR / "ca.crt"
    
    if not ca_cert_path.exists():
        raise FileNotFoundError("CA non initialisée. Exécutez 'python pki.py init' d'abord.")
    
    with open(ca_key_path, 'rb') as f:
        ca_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
    
    with open(ca_cert_path, 'rb') as f:
        ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())
    
    return ca_key, ca_cert


# ===== CERTIFICATS SERVEUR =====
def create_server_certificate(
    name: str,
    domains: list[str],
    ips: list[str],
    config: dict,
    force: bool = False
) -> bool:
    """
    Crée un certificat serveur signé par la CA.
    
    Args:
        name: Nom du certificat (ex: "hub", "mon-app")
        domains: Liste des noms DNS
        ips: Liste des adresses IP
        config: Configuration PKI
        force: Écraser si existe
    
    Returns:
        True si succès
    """
    cert_dir = SERVER_DIR / name
    key_path = cert_dir / f"{name}.key"
    cert_path = cert_dir / f"{name}.crt"
    
    if cert_path.exists() and not force:
        log_warn(f"Le certificat '{name}' existe déjà. Utilisez --force pour le remplacer.")
        return False
    
    print(f"\n{Colors.BOLD}📜 Création du certificat : {name}{Colors.END}\n")
    
    # Charger la CA
    try:
        ca_key, ca_cert = load_ca()
    except FileNotFoundError as e:
        log_error(str(e))
        return False
    
    # Générer la clé privée du serveur
    log_step("Génération de la clé privée serveur...")
    server_key = generate_private_key(config['key_size'])
    
    # Construire les Subject Alternative Names (SAN)
    san_list = []
    
    # Ajouter les domaines DNS
    for domain in domains:
        san_list.append(x509.DNSName(domain))
    
    # Ajouter les IPs
    for ip in ips:
        try:
            san_list.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            log_warn(f"IP invalide ignorée : {ip}")
    
    # Ajouter l'IP auto-détectée si pas déjà présente
    server_ip = get_server_ip()
    if server_ip not in ips:
        san_list.append(x509.IPAddress(ipaddress.ip_address(server_ip)))
        ips = ips + [server_ip]
    
    # Toujours ajouter localhost et 127.0.0.1
    if "localhost" not in domains:
        san_list.append(x509.DNSName("localhost"))
    if "127.0.0.1" not in ips:
        san_list.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    
    log_step(f"SAN DNS : {', '.join(domains)}")
    log_step(f"SAN IPs : {', '.join(ips)}")
    
    # Créer le certificat
    log_step("Signature du certificat...")
    
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "FR"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, config['ca_organization']),
        x509.NameAttribute(NameOID.COMMON_NAME, domains[0] if domains else name),
    ])
    
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=config['cert_validity_days']))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH,
                ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256(), default_backend())
    )
    
    # Sauvegarder
    log_step("Sauvegarde des fichiers...")
    save_private_key(server_key, key_path)
    save_certificate(cert, cert_path)
    
    # Créer aussi un fichier combiné (utile pour certains serveurs)
    fullchain_path = cert_dir / f"{name}-fullchain.crt"
    with open(fullchain_path, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(CA_DIR / "ca.crt", 'rb') as ca_f:
            f.write(ca_f.read())
    
    # Mettre à jour le registre
    registry = load_registry()
    registry['certificates'][name] = {
        'created': now.isoformat(),
        'expires': (now + timedelta(days=config['cert_validity_days'])).isoformat(),
        'domains': domains,
        'ips': ips,
        'serial': str(cert.serial_number),
        'status': 'valid',
    }
    save_registry(registry)
    
    log_info(f"Certificat '{name}' créé avec succès !")
    print(f"\n   📁 Clé privée : {Colors.CYAN}{key_path}{Colors.END}")
    print(f"   📜 Certificat : {Colors.CYAN}{cert_path}{Colors.END}")
    print(f"   🔗 Fullchain  : {Colors.CYAN}{fullchain_path}{Colors.END}")
    print(f"   ⏰ Validité   : {config['cert_validity_days']} jours")
    
    return True


def list_certificates():
    """Affiche la liste des certificats"""
    registry = load_registry()
    
    print(f"\n{Colors.BOLD}📋 Certificats PKI Hub{Colors.END}\n")
    
    # CA
    if registry.get('ca'):
        ca = registry['ca']
        expires = datetime.fromisoformat(ca['expires'].replace('Z', '+00:00'))
        days_left = (expires - datetime.now(timezone.utc)).days
        status_color = Colors.GREEN if days_left > 30 else Colors.YELLOW if days_left > 0 else Colors.RED
        print(f"{Colors.BOLD}🔐 Autorité de Certification{Colors.END}")
        print(f"   Nom        : {ca['common_name']}")
        print(f"   Créée      : {ca['created'][:10]}")
        print(f"   Expire     : {ca['expires'][:10]} ({status_color}{days_left} jours{Colors.END})")
        print()
    else:
        log_warn("CA non initialisée. Exécutez 'python pki.py init'")
        return
    
    # Certificats
    certs = registry.get('certificates', {})
    if not certs:
        print("   Aucun certificat serveur créé.")
        print(f"   Utilisez : python pki.py cert create <nom>")
        return
    
    print(f"{Colors.BOLD}📜 Certificats Serveur{Colors.END}\n")
    for name, cert in certs.items():
        expires = datetime.fromisoformat(cert['expires'].replace('Z', '+00:00'))
        days_left = (expires - datetime.now(timezone.utc)).days
        
        if cert.get('status') == 'revoked':
            status = f"{Colors.RED}🚫 Révoqué{Colors.END}"
        elif days_left < 0:
            status = f"{Colors.RED}⛔ Expiré{Colors.END}"
        elif days_left < 30:
            status = f"{Colors.YELLOW}⚠️  {days_left}j{Colors.END}"
        else:
            status = f"{Colors.GREEN}✅ {days_left}j{Colors.END}"
        
        print(f"   {Colors.CYAN}{name}{Colors.END}")
        print(f"      Domaines : {', '.join(cert['domains'])}")
        print(f"      IPs      : {', '.join(cert['ips'])}")
        print(f"      Expire   : {cert['expires'][:10]} ({status})")
        print()


def revoke_certificate(name: str) -> bool:
    """Révoque un certificat"""
    registry = load_registry()
    
    if name not in registry.get('certificates', {}):
        log_error(f"Certificat '{name}' non trouvé.")
        return False
    
    registry['certificates'][name]['status'] = 'revoked'
    registry['certificates'][name]['revoked_at'] = datetime.now(timezone.utc).isoformat()
    registry['revoked'].append({
        'name': name,
        'serial': registry['certificates'][name]['serial'],
        'revoked_at': datetime.now(timezone.utc).isoformat(),
    })
    save_registry(registry)
    
    log_info(f"Certificat '{name}' révoqué.")
    log_warn("Note: Les clients doivent redémarrer pour prendre en compte la révocation.")
    return True


def renew_certificate(name: str, config: dict) -> bool:
    """Renouvelle un certificat existant"""
    registry = load_registry()
    
    if name not in registry.get('certificates', {}):
        log_error(f"Certificat '{name}' non trouvé.")
        return False
    
    cert_info = registry['certificates'][name]
    return create_server_certificate(
        name=name,
        domains=cert_info['domains'],
        ips=cert_info['ips'],
        config=config,
        force=True
    )


# ===== EXPORT CA =====
def export_ca(output_dir: Optional[Path] = None, format: str = 'pem'):
    """
    Exporte le certificat CA pour installation sur les clients.
    
    Args:
        output_dir: Dossier de destination (défaut: ./certs/export/)
        format: 'pem' ou 'der' (DER pour Windows)
    """
    ca_cert_path = CA_DIR / "ca.crt"
    
    if not ca_cert_path.exists():
        log_error("CA non initialisée. Exécutez 'python pki.py init' d'abord.")
        return
    
    if output_dir is None:
        output_dir = CERTS_DIR / "export"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with open(ca_cert_path, 'rb') as f:
        ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())
    
    registry = load_registry()
    ca_name = registry.get('ca', {}).get('common_name', 'Hub-CA').replace(' ', '-')
    
    if format == 'der':
        # Format DER (pour Windows)
        output_file = output_dir / f"{ca_name}.crt"
        with open(output_file, 'wb') as f:
            f.write(ca_cert.public_bytes(serialization.Encoding.DER))
        log_info(f"CA exportée (DER) : {output_file}")
        print(f"\n   {Colors.BOLD}Installation Windows :{Colors.END}")
        print(f"   1. Double-cliquez sur {output_file.name}")
        print(f"   2. 'Installer le certificat...'")
        print(f"   3. 'Ordinateur local' > Suivant")
        print(f"   4. 'Placer dans le magasin : Autorités de certification racines de confiance'")
        print(f"   5. Terminer et redémarrer le navigateur")
    else:
        # Format PEM (Linux/Mac)
        output_file = output_dir / f"{ca_name}.pem"
        with open(output_file, 'wb') as f:
            f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
        log_info(f"CA exportée (PEM) : {output_file}")
        
        print(f"\n   {Colors.BOLD}Installation Linux (Ubuntu/Debian) :{Colors.END}")
        print(f"   sudo cp {output_file} /usr/local/share/ca-certificates/{ca_name}.crt")
        print(f"   sudo update-ca-certificates")
        
        print(f"\n   {Colors.BOLD}Installation Linux (RHEL/CentOS) :{Colors.END}")
        print(f"   sudo cp {output_file} /etc/pki/ca-trust/source/anchors/{ca_name}.pem")
        print(f"   sudo update-ca-trust")
        
        print(f"\n   {Colors.BOLD}Installation Mac :{Colors.END}")
        print(f"   sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain {output_file}")
        
        print(f"\n   {Colors.BOLD}Installation Firefox (tous OS) :{Colors.END}")
        print(f"   Préférences > Vie privée > Certificats > Afficher les certificats")
        print(f"   Onglet 'Autorités' > Importer > Sélectionner {output_file.name}")


def show_status():
    """Affiche l'état complet de la PKI"""
    registry = load_registry()
    
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}   🔐 État de la PKI Hub{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}\n")
    
    # État de la CA
    if registry.get('ca'):
        ca = registry['ca']
        expires = datetime.fromisoformat(ca['expires'].replace('Z', '+00:00'))
        days_left = (expires - datetime.now(timezone.utc)).days
        
        print(f"   {Colors.GREEN}✅ CA initialisée{Colors.END}")
        print(f"      Nom     : {ca['common_name']}")
        print(f"      Expire  : {ca['expires'][:10]} ({days_left} jours)")
        
        # Vérifier les fichiers
        ca_files = [CA_DIR / "ca.key", CA_DIR / "ca.crt"]
        all_ok = all(f.exists() for f in ca_files)
        if all_ok:
            print(f"      Fichiers: {Colors.GREEN}OK{Colors.END}")
        else:
            print(f"      Fichiers: {Colors.RED}MANQUANTS{Colors.END}")
    else:
        print(f"   {Colors.RED}❌ CA non initialisée{Colors.END}")
        print(f"      Exécutez: python pki.py init")
        return
    
    # Certificats
    certs = registry.get('certificates', {})
    valid_count = sum(1 for c in certs.values() if c.get('status') != 'revoked')
    revoked_count = len(registry.get('revoked', []))
    
    print(f"\n   📜 Certificats : {valid_count} valide(s), {revoked_count} révoqué(s)")
    
    # Certificats expirant bientôt
    expiring = []
    for name, cert in certs.items():
        if cert.get('status') == 'revoked':
            continue
        expires = datetime.fromisoformat(cert['expires'].replace('Z', '+00:00'))
        days_left = (expires - datetime.now(timezone.utc)).days
        if days_left < 30:
            expiring.append((name, days_left))
    
    if expiring:
        print(f"\n   {Colors.YELLOW}⚠️  Certificats à renouveler :{Colors.END}")
        for name, days in expiring:
            print(f"      - {name} : {days} jours")
    
    # Configuration
    config = load_env_config()
    print(f"\n   ⚙️  Configuration :")
    print(f"      Validité CA      : {config['ca_validity_years']} ans")
    print(f"      Validité certs   : {config['cert_validity_days']} jours")
    print(f"      Domaines défaut  : {', '.join(config['cert_domains'])}")
    print(f"      Taille clés      : {config['key_size']} bits")
    
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}\n")


# ===== API JSON (pour le backend) =====
def get_pki_status_json() -> dict:
    """Retourne l'état de la PKI en JSON (pour l'API)"""
    registry = load_registry()
    config = load_env_config()
    
    result = {
        'initialized': registry.get('ca') is not None,
        'ca': None,
        'certificates': [],
        'config': config,
    }
    
    if registry.get('ca'):
        ca = registry['ca']
        expires = datetime.fromisoformat(ca['expires'].replace('Z', '+00:00'))
        days_left = (expires - datetime.now(timezone.utc)).days
        result['ca'] = {
            **ca,
            'days_remaining': days_left,
            'status': 'valid' if days_left > 0 else 'expired',
        }
    
    for name, cert in registry.get('certificates', {}).items():
        expires = datetime.fromisoformat(cert['expires'].replace('Z', '+00:00'))
        days_left = (expires - datetime.now(timezone.utc)).days
        
        status = cert.get('status', 'valid')
        if status != 'revoked' and days_left < 0:
            status = 'expired'
        
        result['certificates'].append({
            'name': name,
            **cert,
            'days_remaining': days_left,
            'status': status,
        })
    
    return result


# ===== CLI =====
def main():
    parser = argparse.ArgumentParser(
        description='PKI Locale pour Hub - Gestion de Certificats SSL/TLS',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python pki.py init                     # Initialiser la CA
  python pki.py cert create hub          # Créer certificat pour le Hub
  python pki.py cert create mon-app -d mon-app.local -i 192.168.1.100
  python pki.py cert list                # Lister tous les certificats
  python pki.py cert renew hub           # Renouveler un certificat
  python pki.py export-ca --format=der   # Exporter CA pour Windows
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Commandes disponibles')
    
    # init
    init_parser = subparsers.add_parser('init', help='Initialiser la CA')
    init_parser.add_argument('--force', action='store_true', help='Réinitialiser même si existe')
    
    # status
    subparsers.add_parser('status', help='Afficher l\'état de la PKI')
    
    # cert
    cert_parser = subparsers.add_parser('cert', help='Gérer les certificats')
    cert_subparsers = cert_parser.add_subparsers(dest='cert_command')
    
    # cert create
    create_parser = cert_subparsers.add_parser('create', help='Créer un certificat')
    create_parser.add_argument('name', help='Nom du certificat')
    create_parser.add_argument('-d', '--domain', action='append', dest='domains', help='Domaine DNS (peut être répété)')
    create_parser.add_argument('-i', '--ip', action='append', dest='ips', help='Adresse IP (peut être répété)')
    create_parser.add_argument('--force', action='store_true', help='Écraser si existe')
    
    # cert list
    cert_subparsers.add_parser('list', help='Lister les certificats')
    
    # cert revoke
    revoke_parser = cert_subparsers.add_parser('revoke', help='Révoquer un certificat')
    revoke_parser.add_argument('name', help='Nom du certificat')
    
    # cert renew
    renew_parser = cert_subparsers.add_parser('renew', help='Renouveler un certificat')
    renew_parser.add_argument('name', help='Nom du certificat')
    
    # export-ca
    export_parser = subparsers.add_parser('export-ca', help='Exporter la CA')
    export_parser.add_argument('--format', choices=['pem', 'der'], default='pem', help='Format de sortie')
    export_parser.add_argument('-o', '--output', type=Path, help='Dossier de sortie')
    
    # json (pour API)
    subparsers.add_parser('json', help='Afficher l\'état en JSON (pour API)')
    
    args = parser.parse_args()
    config = load_env_config()
    
    if args.command == 'init':
        init_ca(config, force=args.force)
    
    elif args.command == 'status':
        show_status()
    
    elif args.command == 'cert':
        if args.cert_command == 'create':
            domains = args.domains or config['cert_domains']
            ips = args.ips or config['cert_ips']
            create_server_certificate(args.name, domains, ips, config, args.force)
        
        elif args.cert_command == 'list':
            list_certificates()
        
        elif args.cert_command == 'revoke':
            revoke_certificate(args.name)
        
        elif args.cert_command == 'renew':
            renew_certificate(args.name, config)
        
        else:
            cert_parser.print_help()
    
    elif args.command == 'export-ca':
        export_ca(args.output, args.format)
    
    elif args.command == 'json':
        print(json.dumps(get_pki_status_json(), indent=2, default=str))
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
