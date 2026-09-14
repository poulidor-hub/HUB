"""Env allowlistée pour tout sous-processus make/compose lancé par le hub.

Module partagé deploy_service / workshop_service (sans cycle d'import :
workshop_service importe déjà deploy_service).
"""
import os

# Clés transmises aux sous-processus docker compose / make (accès docker +
# locales). JAMAIS l'env complet du hub-api : certaines clés du hub partagent
# leur nom avec des clés des apps mais avec un AUTRE sens — ex. HUB_HTTPS_ENABLED
# (hub : « le hub sert du HTTPS » ≠ app : « joindre l'API hub en HTTPS »). En
# passant l'env hub au compose, l'interpolation ${VAR:-défaut} du
# docker-compose.yml de l'app écrasait le .env de l'app → app atelier en HTTPS
# sur le port HTTP du hub (SSL WRONG_VERSION_NUMBER) : enregistrement + JWKS en
# échec, atelier invisible (« Mode dégradé »). Le compose lit lui-même
# APPS/X/.env (source canonique) : l'OS ne doit rien lui imposer.
COMPOSE_ENV_ALLOWLIST = (
    "PATH", "HOME", "TZ", "LANG", "LC_ALL",
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_API_VERSION",
    "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
)


def compose_base_env() -> dict[str, str]:
    """Env minimal (allowlist) pour tout sous-processus compose/make du hub."""
    return {k: os.environ[k] for k in COMPOSE_ENV_ALLOWLIST if k in os.environ}
