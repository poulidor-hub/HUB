"""Déploiement automatique des apps du parc (spec 2026-08-18-auto-deploy-apps).

Pipeline : git clone/pull -> .env -> make build && make up (fallback docker
compose) -> health check. Un déploiement à la fois (lock asyncio global).
"""
import re
import secrets
from pathlib import Path

# Variantes de clés .env rencontrées dans le parc (parité hubctl _write_app_env).
ENV_VARIANTS: dict[str, list[str]] = {
    "APP_SLUG": ["APP_SLUG"],
    "API_PORT": ["API_PORT", "APP_PORT", "APP_PORT_API"],
    "FRONTEND_PORT": ["FRONTEND_PORT"],
    "FRONTEND_HTTPS_PORT": ["FRONTEND_HTTPS_PORT"],
    "HUB_HOST": ["HUB_HOST", "HUB_URL", "HUB_BASE_URL"],
    "APP_HOST": ["APP_HOST", "SERVER_IP", "HOST_IP"],
    "POSTGRES_PASSWORD": ["POSTGRES_PASSWORD"],
    "SECRETS_ENCRYPTION_KEY": ["SECRETS_ENCRYPTION_KEY"],
}

# Clés décisionnelles toujours présentes dans le .env final (« le HUB décide,
# l'app suit »). FRONTEND_HTTPS_PORT n'y figure pas : remplacée seulement si
# présente (le Makefile sync-env la pose sinon).
ENSURE_KEYS = ["APP_SLUG", "API_PORT", "FRONTEND_PORT", "HUB_HOST", "APP_HOST"]


def build_env_values(slug: str, api_port: int, frontend_port: int, hub_host: str) -> dict[str, str]:
    """Valeurs décisionnelles canoniques injectées à chaque déploiement."""
    return {
        "APP_SLUG": slug,
        "API_PORT": str(api_port),
        "FRONTEND_PORT": str(frontend_port),
        "FRONTEND_HTTPS_PORT": str(frontend_port + 1),
        "HUB_HOST": hub_host,
        "APP_HOST": "",  # app colocalisée avec le HUB
    }


def generate_secrets() -> dict[str, str]:
    """Secrets forts, générés uniquement à la création du .env (jamais régénérés)."""
    from cryptography.fernet import Fernet
    return {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "SECRETS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }


def apply_env_values(env_path: Path, values: dict[str, str], ensure: list[str] | None = None) -> None:
    """Remplace dans le .env les variantes de chaque clé canonique.

    - Remplace TOUTES les occurrences des variantes (plusieurs fichiers du parc
      déclarent APP_PORT et APP_PORT_API ensemble).
    - Les clés de `ensure` (défaut ENSURE_KEYS) sont ajoutées en fin de fichier
      si aucune variante n'existe.
    - Si POSTGRES_PASSWORD ∈ values et qu'une ligne DATABASE_URL existe, le mot
      de passe dans l'URL est reconstruit.
    """
    ensure = ENSURE_KEYS if ensure is None else ensure
    lines = env_path.read_text().splitlines()

    for canonical, value in values.items():
        keys = ENV_VARIANTS.get(canonical, [canonical])
        pattern = re.compile(rf"^({'|'.join(re.escape(k) for k in keys)})\s*=.*$")
        found = False
        for i, line in enumerate(lines):
            m = pattern.match(line)
            if m:
                lines[i] = f"{m.group(1)}={value}"
                found = True
        if not found and canonical in ensure:
            lines.append(f"{canonical}={value}")

    text = "\n".join(lines) + "\n"

    if "POSTGRES_PASSWORD" in values:
        pwd = values["POSTGRES_PASSWORD"]
        text = re.sub(r"(://[^:/@\s]+:)[^@\s]*(@)", rf"\g<1>{pwd}\g<2>", text)

    env_path.write_text(text)
