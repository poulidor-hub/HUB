# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from functools import lru_cache
from pathlib import Path
import ipaddress
import os
import socket
import logging
import time
import uuid

logger = logging.getLogger(__name__)


def get_server_ip() -> str:
    """Détecte automatiquement l'IP du serveur"""
    try:
        # Crée une connexion UDP factice pour obtenir l'IP locale
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # N'envoie rien, juste pour obtenir l'IP
            return s.getsockname()[0]
    except Exception:
        return "localhost"


# Cache du check « cette IP est-elle sur une interface locale ? » (UDP connect
# sans paquet émis). get_hub_host() est appelé à chaque requête de config :
# sans cache, le check partirait en O(appels) — TTL court suffisant car une
# IP locale ne change que rarement (DHCP renew, déplacement de poste).
_LOCAL_IP_CACHE_TTL = 60.0
_local_ip_cache: dict[str, tuple[bool, float]] = {}
_stale_hub_host_warned: set[str] = set()


def _is_local_ip(ip: str) -> bool:
    """True si `ip` est assignée à une interface locale de ce poste."""
    now = time.monotonic()
    hit = _local_ip_cache.get(ip)
    if hit is not None and now - hit[1] < _LOCAL_IP_CACHE_TTL:
        return hit[0]
    local = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect((ip, 80))
            local = s.getsockname()[0] == ip
    except OSError:
        local = False
    _local_ip_cache[ip] = (local, now)
    return local


def _clear_local_ip_cache() -> None:
    """Vide le cache de localité (réservé aux tests)."""
    _local_ip_cache.clear()


def _running_in_container() -> bool:
    """True si le process tourne dans un conteneur (Docker : /.dockerenv,
    Podman : variable d'environnement `container`)."""
    try:
        if Path("/.dockerenv").exists():
            return True
    except OSError:
        pass
    return bool(os.environ.get("container"))


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


def _validate_pem_keys(private_key: str, public_key: str):
    """Valide que les clés PEM sont au format RSA correct. Lève ValueError si invalide."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    try:
        serialization.load_pem_private_key(
            private_key.encode(), password=None, backend=default_backend()
        )
    except Exception as e:
        raise ValueError(f"Clé privée JWT invalide (format PEM incorrect) : {e}")
    try:
        serialization.load_pem_public_key(
            public_key.encode(), backend=default_backend()
        )
    except Exception as e:
        raise ValueError(f"Clé publique JWT invalide (format PEM incorrect) : {e}")


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
        _validate_pem_keys(private_key, public_key)
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
    local_private.chmod(0o600)
    local_public.write_text(public_key)
    local_public.chmod(0o644)
    
    logger.info(f"✅ Clé privée générée : {local_private}")
    logger.info(f"✅ Clé publique générée : {local_public}")
    logger.warning("⚠️  En production, utilisez des clés pré-générées et désactivez AUTO_GENERATE_KEYS !")

    return private_key, public_key


def ensure_backup_keys_exist() -> tuple[str, str]:
    """Paire backup = clé de récupération PRA (chiffrement des snapshots + exports).

    Une génération à chaud en production rend les snapshots chiffrés avec
    l'ancienne paire irrécupérables — d'où le log CRITICAL (spec §4.3).
    """
    local_secrets_dir = _resolve_secrets_dir()

    backup_private = local_secrets_dir / "backup_private_key.pem"
    backup_public = local_secrets_dir / "backup_public_key.pem"

    if backup_private.exists() and backup_public.exists():
        private_key = backup_private.read_text().strip()
        public_key = backup_public.read_text().strip()
        _validate_pem_keys(private_key, public_key)
        logger.info("🔐 Clés de chiffrement backup chargées (récupération PRA : OK)")
        return private_key, public_key

    logger.info("🔐 Génération automatique des clés de chiffrement backup...")
    import os
    if os.environ.get("APP_ENV", "production") == "production":
        logger.critical(
            "Clés backup ABSENTES : paire générée à chaud en production. Tout snapshot "
            "chiffré avec l'ancienne paire devient IRRÉCUPÉRABLE. Restaurez "
            "secrets/backup_*.pem depuis le coffre hubctl avant tout nouveau snapshot."
        )
    private_key, public_key = generate_rsa_keys()
    local_secrets_dir.mkdir(parents=True, exist_ok=True)
    backup_private.write_text(private_key)
    backup_private.chmod(0o600)
    backup_public.write_text(public_key)
    backup_public.chmod(0o644)
    logger.info(f"✅ Clé backup privée générée : {backup_private}")
    logger.info(f"✅ Clé backup publique générée : {backup_public}")
    return private_key, public_key


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://hubadmin:CHANGEZ_MOI_DB_PASSWORD@db:5432/hub_auth"
    jwt_algorithm: str = "RS256"
    access_token_expire_minutes: int = 480  # TTL access token (aligné cookie OIDC 8h ; déploiement local LAN — doit matcher docker-compose/.env)
    m2m_access_token_expire_minutes: int = 60  # TTL court des tokens M2M (client_credentials) — audit 08/11/2026 : 8 h pour un secret machine était trop large
    refresh_token_expire_days: int = 7  # 7 jours pour le refresh token
    refresh_token_grace_seconds: int = 60  # Fenêtre de re-délivrance d'un refresh token récemment rotaté (course inter-onglets)
    session_timeout_minutes: int = 20  # Seuil pour considérer une session comme terminée (sans logout explicite)
    admin_username: str = "admin"
    admin_password: str = ""  # Doit être défini via ADMIN_PASSWORD
    admin_email: str = "admin@local.lan"

    # Environnement applicatif : "production" (strict) ou "development" (permissif).
    # En production, les misconfigs de sécurité empêchent le démarrage.
    app_env: str = "production"

    # CORS — origines autorisées (séparées par des virgules).
    # Vide => refus de démarrage en production (voir validate_security_config).
    allowed_origins: str = ""

    # CORS — regex d'origines autorisées (en plus de allowed_origins).
    # Permet d'ouvrir tout un sous-réseau LAN sans lister chaque IP/port.
    # Ex (LAN 192.168.1.0/24 + localhost) :
    #   ^https?://(192\.168\.1\.\d{1,3}|localhost|127\.0\.0\.1)(:\d+)?$
    allowed_origin_regex: str = ""

    # Limite de taille du body (protection DoS), en MB.
    # - max_body_size_mb : toutes les routes.
    # - max_upload_body_size_mb : routes d'upload de sauvegarde/import
    #   (/api/admin/restore, /api/admin/backup/import[/upload|/preview],
    #    /api/apps/{slug}/backup/import,
    #   /api/apps/{slug}/backup/restore/upload) — backups volumineux
    #   (cf. incident exemple-app > 150 Mo).
    max_body_size_mb: int = 10
    max_upload_body_size_mb: int = 512

    # Mode développement - Génération automatique des clés JWT
    auto_generate_keys: bool = False  # True uniquement en DEV !

    # ===== OIDC Provider (bridge OIDC pour apps externes) =====
    oidc_enabled: bool = True
    oidc_issuer: str = ""  # ex: "https://hub.example.com" - URL publique du HUB (base pour discovery + iss)
    oidc_email_template: str = "{username}@hub.internal"
    oidc_code_ttl_seconds: int = 30
    oidc_id_token_ttl_minutes: int = 10
    oidc_access_token_ttl_minutes: int = 15
    oidc_refresh_token_days: int = 30
    oidc_backchannel_logout_enabled: bool = True
    oidc_slo_enabled: bool = False  # SLO poste partagé — défaut False, activer via .env après e2e atelier
    oidc_slo_iframe_timeout_ms: int = 1500
    # SA interne pour appels M2M HUB→apps (SLO v2, révoke sessions distantes)
    hub_internal_sa_client_id: str = ""
    hub_internal_sa_client_secret: str = ""

    # Auto-remplacement des applications existantes lors de l'enregistrement
    # Si True : l'ancienne app et son M2M sont supprimés automatiquement
    force_replace_app: bool = False
    
    # Clé de chiffrement des secrets (Fernet - 32 bytes base64)
    # Générée avec: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Si vide, les secrets seront stockés en clair (non recommandé en production)
    secrets_encryption_key: str = ""

    # TOTP / récupération de mot de passe
    totp_issuer: str = "HUB"  # Nom affiché dans l'application d'authentification
    recovery_token_ttl_minutes: int = 5  # Durée de vie du jeton de réinitialisation
    password_reset_code_ttl_minutes: int = 15  # Durée de vie d'un code de reset généré par un admin

    # Autorise explicitement les secrets en clair (clé Fernet vide) — DEV uniquement.
    allow_plaintext_secrets: bool = False

    # Désactive le warning des mots de passe faibles (utile pour les déploiements existants
    # qui n'ont pas encore migré leurs secrets).
    skip_password_check: bool = False
    
    # Synchronisation automatique avec Docker (en secondes)
    # 0 = désactivé, 300 = toutes les 5 minutes
    docker_sync_interval: int = 300

    # Activer le scan Docker (socket requis). False = aucun accès au socket (production).
    docker_scan_enabled: bool = False
    
    # Token API GitHub (optionnel, évite la rate-limit sur les appels publics)
    github_token: str = ""

    # Moniteur de vivacité du token GitHub (en secondes)
    # 0 = désactivé, 3600 = toutes les heures
    github_token_check_interval: int = 3600

    # Déploiements simultanés max (petites apps : 3 ; borné 1-6 au runtime).
    # La file d'attente reste en DB (statut 'pending' jusqu'à l'acquisition d'un slot).
    max_concurrent_deployments: int = 3

    # Cooldown pour les notifications SSE d'audit (en secondes)
    # Évite les rafraîchissements trop fréquents de la page audit
    audit_sse_cooldown: float = 5.0
    
    # Rétention des logs d'audit (en jours) - 0 = pas de purge automatique
    audit_retention_days: int = 90
    
    # Rétention des logs d'erreurs (en jours) - 0 = pas de purge automatique
    error_retention_days: int = 30
    
    # Rétention des messages utilisateur (en jours) - 0 = pas de purge automatique
    message_retention_days: int = 90

    workshop_retention_days: int = 30  # purge de l'historique workshop_operations

    # Rétention des sessions utilisateur (en jours) - 0 = pas de purge
    # Audit 08/11/2026 : user_sessions n'avait aucune rétention (37,6 k lignes).
    session_retention_days: int = 180
    # --- Alertes admin sur seuils (audit 08/11/2026) ---
    alert_check_interval_s: int = 60      # 0 = désactivé
    alert_error_rate_10m: int = 30        # erreurs error_logs en 10 min
    alert_auth_failures_10m: int = 20     # échecs 401/403 API en 10 min
    alert_sse_auth_failures_1h: int = 100 # échecs auth SSE en 1 h
    alert_sse_clients_max: int = 500      # capacité SSE
    alert_app_offline_min: int = 5        # app offline persistante (minutes)
    alert_db_write_errors_1h: int = 10    # deadlocks / erreurs d'écriture en 1 h
    alert_cooldown_min: int = 30          # anti-spam par clé d'alerte
    
    # Intervalle de purge des logs (en heures)
    log_purge_interval_hours: int = 24
    
    # Whitelist des réseaux autorisés pour l'auto-enregistrement des applications
    # Par défaut : réseaux Docker (172.17-31.*)
    # Format : préfixes IP séparés par des virgules (ex: "172.17.,172.18.,192.168.50.")
    app_whitelist_networks: str = ""

    # Répertoire de configuration VPN WireGuard (partagé avec le conteneur wireguard-server)
    vpn_config_dir: str = "/vpn"

    # === Pool d'allocation dynamique des ports d'applications ===
    # Plages utilisées par POST /api/apps/allocate-ports.
    # Le frontend est alloué par pas de 2 (frontend_port + 1 réservé pour le HTTPS).
    app_port_api_start: int = 9000
    app_port_api_end: int = 9499
    app_port_frontend_start: int = 4200
    app_port_frontend_end: int = 4699

    # Exclure de l'attribution les ports déjà liés sur l'hôte local par des
    # conteneurs Docker (scan best-effort via le socket Docker, à l'allocation).
    port_scan_docker_enabled: bool = True

    # Exclure de l'attribution les sous-réseaux déjà présents sur l'hôte local
    # (réseaux Docker existants, y compris épinglés manuellement hors Hub).
    # Scan best-effort via le socket Docker, à l'allocation.
    subnet_scan_docker_enabled: bool = True

    # Durée (minutes) d'une RÉSERVATION de ports : allocation sans application
    # associée (prepare du catalogue GitHub, /allocate-ports orphelin). Passé
    # ce délai, ports et sous-réseau redeviennent attribuables et la ligne est
    # purgée paresseusement à l'allocation suivante (bug 2026-10-05 : chaque
    # « Ajouter » abandonné fuyait ports + sous-réseau). 0 = jamais (historique).
    port_reservation_ttl_minutes: int = 30

    # === Pool d'allocation des sous-réseaux Docker des apps ===
    # Attribué par POST /api/apps/allocate-ports (champ `subnet`) : un /N par app.
    # Doit rester DISJOINT des réseaux existants (172.16/12 Docker, 10.42 Hub,
    # 10.44 hub-tools, 10.8 VPN) et du LAN (192.168.1.0/24) + VPN routeur
    # (198.51.100.60/24). 10.50.0.0/16 ÷ /24 = 256 apps.
    app_subnet_pool: str = "10.50.0.0/16"
    app_subnet_size: int = 24

    # === Déploiement automatique des apps ===
    # Chemin absolu du dossier APPS/ — doit être identique côté hôte et côté
    # conteneur hub-api (montage au même chemin) pour que les bind mounts des
    # docker compose des apps soient résolus correctement par le daemon hôte.
    # Obligatoire en runtime : injecté par docker-compose depuis APPS_HOST_PATH
    # du .env (écrit par install.sh avec le chemin réel de l'installation).
    # Le défaut ci-dessous n'est utilisé que hors compose (tests unitaires) :
    # volontairement neutre, JAMAIS un chemin de machine de développement.
    apps_host_path: str = "/data/apps"

    # === Atelier (pilotage par l'agent exemple-app) ===
    # Réservés à la route proxy /api/admin/atelier/proxy (Task 4) : URL du
    # service atelier hôte (hubctl) + fichier du token partagé (monté ro).
    atelier_service_url: str = "http://host.docker.internal:8777"
    atelier_service_token_file: str = "/code/secrets/atelier_token"
    # Timeout du proxy close-all : un `docker compose down -v` d'atelier peut
    # être très long (volumes volumineux) → 10 minutes par défaut.
    atelier_close_timeout: float = 600.0

    # === Vault (coffre hubctl) ===
    # Servi par le MÊME service hôte que l'atelier : l'URL et le fichier token
    # sont RÉUTILISÉS (atelier_service_url / atelier_service_token_file). Seul
    # ce timeout est propre au vault : un backup est borné côté hôte (~110 s)
    # mais on laisse de la marge avant d'abandonner la réponse au client.
    vault_backup_timeout: float = 120.0

    # === Mises à jour du parc (service hôte hubctl-update, port 8778) ===
    # Servi par le service hôte hubctl update --serve, token réutilisé de
    # l'atelier (atelier_service_token_file, monté :ro) — aucune nouvelle
    # setting token. Ce timeout couvre le run (pull + build + up avec
    # rollback auto, potentiellement longs) ET les GETs proxy tolérants
    # status/job : à froid (cache TTL 60 s expiré) la collecte de statut
    # côté hôte (git fetch des dépôts) prend ~10 s sur le parc réel.
    update_service_url: str = "http://host.docker.internal:8778"
    update_timeout: float = 30.0

    # Host du Hub (IP ou hostname accessible par les clients)
    # Si non défini, sera détecté automatiquement
    hub_host: str = ""
    # Chantier B — chemins de publication du routage par noms (volumes compose)
    routing_dns_dir: str = "/code/dns-generated"
    routing_gateway_dir: str = "/code/gateway"

    # Ports d'écoute publics du Hub (API et frontend) — lus depuis HUB_API_PORT /
    # HUB_FRONTEND_PORT. Injectés par docker-compose (defaults 8000/80).
    hub_api_port: int = 8000
    hub_frontend_port: int = 80

    # === HTTPS / PKI interne ===
    hub_https_enabled: bool = True
    hub_https_port: int = 443
    certs_dir: str = "/code/certs"
    pki_ca_common_name: str = "Hub Local CA"
    pki_ca_organization: str = "Hub Internal"
    pki_ca_validity_years: int = 10
    pki_cert_domains: str = "localhost,hub.local,hub-frontend"
    pki_cert_ips: str = ""
    pki_cert_validity_days: int = 365
    pki_key_size: int = 4096

    # Cache des clés JWT (initialisé au premier accès)
    _jwt_keys_cache: tuple[str, str] | None = None
    
    def get_hub_host(self) -> str:
        """Host du Hub : valeur configurée si encore valide, sinon auto-détectée.

        Une IP configurée (env HUB_HOST ou DB app_settings) qui n'existe plus
        sur ce poste — Hub déplacé, cloné ou restauré depuis un PRA — est
        ignorée : on retombe sur l'IP locale détectée plutôt que de pousser
        une ancienne IP à toutes les apps (elles la resynchronisent via
        GET /api/config/hub-host). Un hostname (DNS) reste toujours valable.

        Exception — exécution conteneurisée : l'IP LAN de l'hôte n'est jamais
        assignée aux interfaces du conteneur, le check « interface locale »
        l'invaliderait à tort (bug 2026-10-03 : l'IP interne Docker 10.42.x.x
        était poussée aux apps déployées — health check KO, hub_connected=
        false, aucun auto-enregistrement). En conteneur, la valeur configurée
        (issue du .env du poste hôte) est conservée ; le scénario « Hub
        déplacé » passe par la restauration du .env (hubctl PRA).
        """
        if self.hub_host:
            configured = self.hub_host
            try:
                ipaddress.ip_address(configured)
            except ValueError:
                return configured  # hostname/DNS : indépendant du poste
            if _is_local_ip(configured):
                return configured
            if _running_in_container():
                if configured not in _stale_hub_host_warned:
                    _stale_hub_host_warned.add(configured)
                    logger.info(
                        f"HUB_HOST={configured} conservé (exécution conteneur : "
                        f"l'IP de l'hôte n'est pas une interface locale du conteneur)."
                    )
                return configured
            if configured not in _stale_hub_host_warned:
                _stale_hub_host_warned.add(configured)
                logger.warning(
                    f"⚠️ HUB_HOST configuré ({configured}) absent des interfaces "
                    f"locales de ce poste (Hub déplacé ?) — IP auto-détectée "
                    f"utilisée à la place : {get_server_ip()}"
                )
            return get_server_ip()
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

    @property
    def allowed_origins_list(self) -> list[str]:
        """Liste des origines CORS, nettoyée."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def get_hub_ports(self) -> dict[str, int]:
        """Ports publics du Hub (API HTTP + frontend)."""
        return {"api_port": self.hub_api_port, "frontend_port": self.hub_frontend_port}

    model_config = ConfigDict(env_file=".env", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


_SECRETS_DIR = None  # injectable pour les tests (défaut: résolution standard)

_FALLBACK_INSTANCE_ID: str | None = None  # mémoire process si le fichier n'est pas écrivable


def _resolve_secrets_dir() -> Path:
    """Répertoire secrets : /code/secrets en conteneur, sinon <projet>/secrets."""
    if _SECRETS_DIR is not None:
        return _SECRETS_DIR
    if Path("/code").exists():
        return Path("/code/secrets")
    return Path(__file__).parent.parent.parent / "secrets"


@lru_cache()
def get_instance_id() -> str:
    """Identifiant unique et persistant de cette instance de Hub.

    Distingue deux hubs même-version (coexistence) : généré au premier appel,
    stocké dans secrets/hub_instance_id (chmod 600).
    """
    global _FALLBACK_INSTANCE_ID
    path = _resolve_secrets_dir() / "hub_instance_id"
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    if _FALLBACK_INSTANCE_ID:
        return _FALLBACK_INSTANCE_ID
    value = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        # lecture/écriture impossible (permissions): valeur en mémoire pour ce process
        _FALLBACK_INSTANCE_ID = value
    return value


class ConfigValidationError(Exception):
    """Erreur de configuration de sécurité fatale (empêche le démarrage en production)."""


def is_production(settings: "Settings") -> bool:
    """Retourne True si le Hub tourne en mode production."""
    return settings.app_env.strip().lower() == "production"


def _check_allowed_origins(settings: "Settings") -> list[str]:
    if settings.allowed_origins_list or settings.allowed_origin_regex.strip():
        return []
    if is_production(settings):
        raise ConfigValidationError(
            "ALLOWED_ORIGINS est vide en production. Définissez les origines des frontends "
            "(Hub + apps), séparées par des virgules, dans .env."
        )
    return ["⚠️  ALLOWED_ORIGINS vide (dev) : CORS limité à http://localhost, credentials désactivés."]


def _check_secrets(settings: "Settings") -> list[str]:
    key = settings.secrets_encryption_key.strip()
    if not key:
        if is_production(settings) and not settings.allow_plaintext_secrets:
            raise ConfigValidationError(
                "SECRETS_ENCRYPTION_KEY est vide en production : les secrets seraient stockés en clair. "
                "Générez une clé via Fernet.generate_key() ou définissez ALLOW_PLAINTEXT_SECRETS=true (dev)."
            )
        return ["⚠️  SECRETS_ENCRYPTION_KEY vide : secrets stockés en clair."]
    from cryptography.fernet import Fernet
    try:
        Fernet(key.encode())
    except Exception as exc:
        raise ConfigValidationError(
            "SECRETS_ENCRYPTION_KEY définie mais invalide (pas une clé Fernet). "
            f"Générez-la via Fernet.generate_key(). Détail : {exc}"
        )
    return []


_WEAK_PASSWORDS = {"admin", "password", "admin123", "changez_moi", "changeme", "hub_secure_password_2025", ""}


def _extract_db_password(database_url: str) -> str:
    """Extrait le mot de passe d'une URL postgresql+asyncpg://user:pass@host/db."""
    try:
        after_scheme = database_url.split("://", 1)[1]
        creds = after_scheme.split("@", 1)[0]
        if ":" in creds:
            return creds.split(":", 1)[1]
    except (IndexError, ValueError):
        pass
    return ""


def _check_passwords(settings: "Settings") -> list[str]:
    warns: list[str] = []

    if settings.skip_password_check:
        return warns

    admin = settings.admin_password.strip()
    admin_weak = admin.lower() in _WEAK_PASSWORDS or len(admin) < 8
    if admin_weak:
        msg = ("ADMIN_PASSWORD est faible (valeur par défaut ou < 8 caractères). "
               "Définissez un mot de passe fort dans .env.")
        if is_production(settings):
            raise ConfigValidationError(msg)
        warns.append(f"⚠️  {msg}")

    db_pwd = _extract_db_password(settings.database_url)
    db_weak = db_pwd.lower() in _WEAK_PASSWORDS or "changez_moi" in db_pwd.lower() or len(db_pwd) < 8
    if db_weak:
        msg = ("Mot de passe de la base de données faible/par défaut dans DATABASE_URL. "
               "Régénérez-le (rejouez install.sh) et mettez à jour POSTGRES_PASSWORD + DATABASE_URL.")
        if is_production(settings):
            raise ConfigValidationError(msg)
        warns.append(f"⚠️  {msg}")

    return warns


def validate_security_config(settings: "Settings") -> list[str]:
    """Valide la configuration de sécurité au démarrage.

    Retourne la liste des avertissements non bloquants.
    Lève ConfigValidationError si une erreur fatale est détectée en production.
    """
    warnings_out: list[str] = []
    warnings_out += _check_allowed_origins(settings)
    warnings_out += _check_secrets(settings)
    warnings_out += _check_passwords(settings)
    return warnings_out


def build_cors_config(settings: "Settings") -> tuple[list[str], "str | None", bool]:
    """Calcule la configuration CORS depuis les settings.

    Retourne (allow_origins, allow_origin_regex, allow_credentials).
    - origines explicites (+ regex optionnelle) : credentials activés.
    - regex seule (prod LAN) : pas d'injection de localhost, credentials activés.
    - rien configuré (dev) : localhost sans credentials.
    """
    origins = list(settings.allowed_origins_list)
    regex = settings.allowed_origin_regex.strip() or None
    if origins:
        return origins, regex, True
    if regex:
        return [], regex, True
    return ["http://localhost"], None, False
