from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path
import socket
import logging

logger = logging.getLogger(__name__)


def get_server_ip() -> str:
    """Détecte automatiquement l'IP du serveur"""
    try:
        # Crée une connexion UDP factice pour obtenir l'IP locale
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # N'envoie rien, juste pour obtenir l'IP
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def generate_rsa_keys() -> tuple[str, str]:
    """
    Génère une paire de clés RSA 2048 bits pour JWT.
    
    Returns:
        tuple: (private_key_pem, public_key_pem)
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    
    # Génération de la clé privée RSA 2048 bits
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    # Sérialisation de la clé privée en PEM
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')
    
    # Extraction et sérialisation de la clé publique
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')
    
    return private_pem, public_pem


def ensure_keys_exist(auto_generate: bool = False) -> tuple[str, str]:
    """
    Vérifie que les clés JWT existent, les génère si autorisé.
    
    Args:
        auto_generate: Si True, génère les clés manquantes (mode DEV uniquement)
    
    Returns:
        tuple: (private_key, public_key)
    
    Raises:
        FileNotFoundError: Si les clés n'existent pas et auto_generate=False
    """
    # Chemins possibles pour les clés
    docker_private = Path("/run/secrets/jwt_private_key")
    docker_public = Path("/run/secrets/jwt_public_key")
    
    # Déterminer le chemin local (dans le dossier du projet)
    # Essayer /code/secrets (Docker) puis le dossier relatif au projet
    if Path("/code").exists():
        local_secrets_dir = Path("/code/secrets")
    else:
        # Mode local : utiliser le dossier secrets relatif au fichier config.py
        local_secrets_dir = Path(__file__).parent.parent.parent / "secrets"
    
    local_private = local_secrets_dir / "jwt_private_key.pem"
    local_public = local_secrets_dir / "jwt_public_key.pem"
    
    private_key = None
    public_key = None
    
    # Essayer de lire depuis Docker secrets d'abord
    if docker_private.exists():
        private_key = docker_private.read_text().strip()
    elif local_private.exists():
        private_key = local_private.read_text().strip()
    
    if docker_public.exists():
        public_key = docker_public.read_text().strip()
    elif local_public.exists():
        public_key = local_public.read_text().strip()
    
    # Si les deux clés existent, les retourner
    if private_key and public_key:
        return private_key, public_key
    
    # Clés manquantes - générer si autorisé
    if not auto_generate:
        missing = []
        if not private_key:
            missing.append("clé privée")
        if not public_key:
            missing.append("clé publique")
        raise FileNotFoundError(
            f"Clés JWT manquantes ({', '.join(missing)}). "
            f"Définissez AUTO_GENERATE_KEYS=true dans .env pour les générer automatiquement (DEV uniquement)."
        )
    
    # Générer les clés
    logger.warning("🔐 AUTO_GENERATE_KEYS=true : Génération automatique des clés JWT...")
    logger.warning("⚠️  Cette option ne doit être utilisée qu'en DÉVELOPPEMENT !")
    
    private_key, public_key = generate_rsa_keys()
    
    # Créer le dossier secrets si nécessaire
    local_secrets_dir.mkdir(parents=True, exist_ok=True)
    
    # Sauvegarder les clés
    local_private.write_text(private_key)
    local_public.write_text(public_key)
    
    logger.info(f"✅ Clé privée générée : {local_private}")
    logger.info(f"✅ Clé publique générée : {local_public}")
    logger.warning("⚠️  En production, utilisez des clés pré-générées et désactivez AUTO_GENERATE_KEYS !")
    
    return private_key, public_key


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://hubadmin:hub_secure_password_2025@db:5432/hub_auth"
    jwt_algorithm: str = "RS256"
    access_token_expire_minutes: int = 15  # Token court (15 min) - refresh fréquent = meilleur tracking sessions
    refresh_token_expire_days: int = 7  # 7 jours pour le refresh token
    session_timeout_minutes: int = 20  # Seuil pour considérer une session comme terminée (sans logout explicite)
    admin_username: str = "admin"
    admin_password: str = "admin123"
    admin_email: str = "admin@local.lan"
    
    # Mode développement - Génération automatique des clés JWT
    auto_generate_keys: bool = False  # True uniquement en DEV !
    
    # Auto-remplacement des applications existantes lors de l'enregistrement
    # Si True : l'ancienne app et son M2M sont supprimés automatiquement
    force_replace_app: bool = False
    
    # Clé de chiffrement des secrets (Fernet - 32 bytes base64)
    # Générée avec: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Si vide, les secrets seront stockés en clair (non recommandé en production)
    secrets_encryption_key: str = ""
    
    # Synchronisation automatique avec Docker (en secondes)
    # 0 = désactivé, 300 = toutes les 5 minutes
    docker_sync_interval: int = 300
    
    # Cooldown pour les notifications SSE d'audit (en secondes)
    # Évite les rafraîchissements trop fréquents de la page audit
    audit_sse_cooldown: float = 5.0
    
    # Rétention des logs d'audit (en jours) - 0 = pas de purge automatique
    audit_retention_days: int = 90
    
    # Rétention des logs d'erreurs (en jours) - 0 = pas de purge automatique
    error_retention_days: int = 30
    
    # Intervalle de purge des logs (en heures)
    log_purge_interval_hours: int = 24
    
    # Whitelist des réseaux autorisés pour l'auto-enregistrement des applications
    # Par défaut : réseaux Docker (172.17-31.*)
    # Format : préfixes IP séparés par des virgules (ex: "172.17.,172.18.,192.168.50.")
    app_whitelist_networks: str = ""
    
    # Host du Hub (IP ou hostname accessible par les clients)
    # Si non défini, sera détecté automatiquement
    hub_host: str = ""
    
    # ===== HTTPS / PKI Configuration =====
    # Activer HTTPS (nécessite certificats dans /certs)
    https_enabled: bool = False
    
    # Port HTTPS (443 par défaut)
    https_port: int = 443
    
    # Cookie settings pour le SSO
    # cookie_secure: automatiquement True si https_enabled
    cookie_httponly: bool = True
    cookie_samesite: str = "lax"  # "strict", "lax", ou "none"
    cookie_domain: str = ""  # Laisser vide pour le domaine actuel
    
    @property
    def cookie_secure(self) -> bool:
        """Cookie secure est automatiquement activé avec HTTPS"""
        return self.https_enabled
    
    # Cache des clés JWT (initialisé au premier accès)
    _jwt_keys_cache: tuple[str, str] | None = None
    
    def get_hub_host(self) -> str:
        """Retourne le host du Hub (configuré ou auto-détecté)"""
        if self.hub_host:
            return self.hub_host
        return get_server_ip()
    
    def _load_jwt_keys(self) -> tuple[str, str]:
        """Charge ou génère les clés JWT (avec cache)"""
        if self._jwt_keys_cache is None:
            object.__setattr__(self, '_jwt_keys_cache', 
                ensure_keys_exist(auto_generate=self.auto_generate_keys))
        return self._jwt_keys_cache
    
    @property
    def jwt_private_key(self) -> str:
        """Retourne la clé privée JWT"""
        private_key, _ = self._load_jwt_keys()
        return private_key
    
    @property
    def jwt_public_key(self) -> str:
        """Retourne la clé publique JWT"""
        _, public_key = self._load_jwt_keys()
        return public_key

    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignorer les variables d'environnement non définies


@lru_cache()
def get_settings() -> Settings:
    return Settings()
