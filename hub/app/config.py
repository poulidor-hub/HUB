from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path
import socket


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


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://hubadmin:hub_secure_password_2025@db:5432/hub_auth"
    jwt_algorithm: str = "RS256"
    access_token_expire_minutes: int = 30  # Réduit à 30 min (refresh token gère les sessions longues)
    refresh_token_expire_days: int = 7  # 7 jours pour le refresh token
    admin_username: str = "admin"
    admin_password: str = "admin123"
    admin_email: str = "admin@local.lan"
    
    # Host du Hub (IP ou hostname accessible par les clients)
    # Si non défini, sera détecté automatiquement
    hub_host: str = ""
    
    # Cookie settings pour le SSO
    cookie_secure: bool = False  # True en production HTTPS
    cookie_httponly: bool = True
    cookie_samesite: str = "lax"  # "strict", "lax", ou "none"
    cookie_domain: str = ""  # Laisser vide pour le domaine actuel
    
    def get_hub_host(self) -> str:
        """Retourne le host du Hub (configuré ou auto-détecté)"""
        if self.hub_host:
            return self.hub_host
        return get_server_ip()
    
    @property
    def jwt_private_key(self) -> str:
        """Lit la clé privée depuis Docker secrets ou fichier local"""
        secret_path = Path("/run/secrets/jwt_private_key")
        if secret_path.exists():
            return secret_path.read_text().strip()
        local_path = Path("/code/secrets/jwt_private_key.pem")
        if local_path.exists():
            return local_path.read_text().strip()
        raise FileNotFoundError("Clé privée JWT non trouvée")
    
    @property
    def jwt_public_key(self) -> str:
        """Lit la clé publique depuis Docker secrets ou fichier local"""
        secret_path = Path("/run/secrets/jwt_public_key")
        if secret_path.exists():
            return secret_path.read_text().strip()
        local_path = Path("/code/secrets/jwt_public_key.pem")
        if local_path.exists():
            return local_path.read_text().strip()
        raise FileNotFoundError("Clé publique JWT non trouvée")

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
