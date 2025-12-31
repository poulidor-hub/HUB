from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://hubadmin:hub_secure_password_2025@db:5432/hub_auth"
    jwt_algorithm: str = "RS256"
    access_token_expire_minutes: int = 480
    admin_username: str = "admin"
    admin_password: str = "admin123"
    admin_email: str = "admin@local.lan"
    
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
