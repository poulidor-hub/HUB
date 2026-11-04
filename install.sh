#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

set -euo pipefail

# =============================================================================
# POULIDOR Hub — Installation
# =============================================================================
# Usage :
#   ./install.sh                          # Mode auto (défaut, zéro question)
#   ./install.sh --interactive            # Mode interactif pas à pas
#   ./install.sh --profile client.json    # Mode non-interactif via profil JSON
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

say()  { echo -e "$1"; }
ok()   { say "  ${GREEN}✓${NC} $1"; }
warn() { say "  ${YELLOW}⚠${NC} $1"; }
err()  { say "  ${RED}✗${NC} $1"; exit 1; }

PROFILE=""
INTERACTIVE=false
FORCE=false
AUTO=true

# ── Parse arguments ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            shift
            PROFILE="${1:-}"
            if [ -z "$PROFILE" ] || [ ! -f "$PROFILE" ]; then
                err "Profil introuvable : ${PROFILE:-'(chemin vide)'}"
            fi
            AUTO=false
            INTERACTIVE=false
            ;;
        --force)
            FORCE=true
            ;;
        --auto)
            AUTO=true
            INTERACTIVE=false
            FORCE=true
            ;;
        --interactive)
            AUTO=false
            INTERACTIVE=true
            ;;
        -h|--help)
            say "Usage : $0 [--interactive] [--profile client.json] [--auto] [--force]"
            say ""
            say "  Mode auto (défaut)      : full automatique, auto-détecte tout, zéro question."
            say "  Mode --interactive      : guide l'utilisateur pas à pas."
            say "  Mode --profile          : lit un fichier JSON et configure sans interaction."
            say "  --force                 : autorise l'écrasement d'un .env existant (mode --profile)."
            say "  --auto                  : force le mode auto (déjà le défaut) + --force implicite."
            say ""
            say "  Exemple de profil : docs/hub-profile.example.json"
            exit 0
            ;;
        *)
            err "Argument inconnu : $1 (utilisez --help)"
            ;;
    esac
    shift
done

say ""
say "${BOLD}${CYAN}══════════════════════════════════════════════════════════${NC}"
say "${BOLD}${CYAN}   POULIDOR Hub — Installation${NC}"
if ! $INTERACTIVE; then
    if $AUTO; then
        say "   Mode : ${GREEN}auto${NC} (full automatique)"
    else
        say "   Profil : ${YELLOW}${PROFILE}${NC}"
    fi
fi
say "${BOLD}${CYAN}══════════════════════════════════════════════════════════${NC}"
say ""

# ── 0. Prérequis ─────────────────────────────────────────────────────────────

say "${BOLD}0. Prérequis${NC}"

if ! command -v docker &> /dev/null; then
    err "Docker n'est pas installé. https://docs.docker.com/engine/install/"
fi
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"

if ! docker compose version &> /dev/null; then
    err "'docker compose' (plugin) non disponible."
fi
ok "Docker Compose $(docker compose version --short)"

PYTHON_CMD=""
if python3 --version &> /dev/null; then
    PYTHON_CMD="python3"
elif python --version &> /dev/null; then
    PYTHON_CMD="python"
fi
if [ -z "$PYTHON_CMD" ]; then
    warn "Python 3 non trouvé — la clé Fernet sera générée via openssl"
    PYTHON_OK=false
else
    ok "Python $( $PYTHON_CMD --version 2>&1 )"
    PYTHON_OK=true
fi

say ""

# ── 1. Configuration ─────────────────────────────────────────────────────────

say "${BOLD}1. Configuration du Hub${NC}"
say ""

if $INTERACTIVE; then
    # --- Mode interactif ---

    DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "")
    if [ -n "$DETECTED_IP" ]; then
        read -p "  Adresse IP du serveur [$DETECTED_IP] : " SERVER_IP
        SERVER_IP="${SERVER_IP:-$DETECTED_IP}"
    else
        read -p "  Adresse IP du serveur : " SERVER_IP
    fi

    read -p "  Port HTTP [80] : " HUB_HTTP_PORT; HUB_HTTP_PORT="${HUB_HTTP_PORT:-80}"
    read -p "  Port HTTPS [443] : " HUB_HTTPS_PORT; HUB_HTTPS_PORT="${HUB_HTTPS_PORT:-443}"
    read -p "  Port API [8000] : " HUB_API_PORT; HUB_API_PORT="${HUB_API_PORT:-8000}"

    read -p "  Admin username [admin] : " ADMIN_USER; ADMIN_USER="${ADMIN_USER:-admin}"
    read -sp "  Admin password (min 8 car.) : " ADMIN_PASS; say ""
    if [ ${#ADMIN_PASS} -lt 8 ]; then err "Mot de passe trop court."; fi
    read -p "  Admin email [admin@local.lan] : " ADMIN_EMAIL; ADMIN_EMAIL="${ADMIN_EMAIL:-admin@local.lan}"

    DOCKER_SUBNET=$(docker network inspect hub-network 2>/dev/null | $PYTHON_CMD -c "import sys,json; print(json.load(sys.stdin)[0]['IPAM']['Config'][0]['Subnet'])" 2>/dev/null || echo "10.42.0.0/16")
    LAN_CIDR="${SERVER_IP%.*}.0/24"
    read -p "  Whitelist réseaux (CIDR) [$DOCKER_SUBNET,$LAN_CIDR] : " WHITELIST
    WHITELIST="${WHITELIST:-$DOCKER_SUBNET,$LAN_CIDR}"

    DEFAULT_ORIGINS="https://${SERVER_IP},http://${SERVER_IP}"
    read -p "  Origines CORS [$DEFAULT_ORIGINS] : " CORS_ORIGINS
    CORS_ORIGINS="${CORS_ORIGINS:-$DEFAULT_ORIGINS}"

    read -p "  Activer HTTPS (TLS) ? [O/n] : " ENABLE_HTTPS
    if [ "$ENABLE_HTTPS" = "n" ] || [ "$ENABLE_HTTPS" = "N" ]; then HTTPS_ENABLED=false; else HTTPS_ENABLED=true; fi

    DB_PASS=$(openssl rand -base64 18 2>/dev/null || $PYTHON_CMD -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || echo "CHANGEZ_MOI_$(date +%s)")
    DOCKER_SCAN_ENABLED=false

    CLIENT_NAME=""
    CLIENT_CONTACT=""
    CLIENT_EMAIL=""

elif $AUTO; then
    # --- Mode full-auto (zéro interaction) ---

    DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "")
    if [ -z "$DETECTED_IP" ]; then
        err "Impossible de détecter l'IP du serveur. Utilisez --profile ou le mode interactif."
    fi
    SERVER_IP="$DETECTED_IP"

    HUB_HTTP_PORT=80
    HUB_HTTPS_PORT=443
    HUB_API_PORT=8000

    ADMIN_USER="admin"
    ADMIN_PASS=$(openssl rand -base64 18 2>/dev/null || $PYTHON_CMD -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || echo "CHANGEZ_MOI_$(date +%s)")
    ADMIN_EMAIL="admin@local.lan"

    DOCKER_SUBNET="10.42.0.0/16"
    LAN_CIDR="${SERVER_IP%.*}.0/24"
    WHITELIST="${DOCKER_SUBNET},${LAN_CIDR}"

    CORS_ORIGINS="https://${SERVER_IP},http://${SERVER_IP}"

    HTTPS_ENABLED=true
    DOCKER_SCAN_ENABLED=false

    DB_PASS=$(openssl rand -base64 18 2>/dev/null || $PYTHON_CMD -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || echo "CHANGEZ_MOI_$(date +%s)")

    CLIENT_NAME=""
    CLIENT_CONTACT=""
    CLIENT_EMAIL=""

    say "  IP détectée  : ${GREEN}${SERVER_IP}${NC}"
    say "  Admin        : ${GREEN}${ADMIN_USER}${NC} (mot de passe auto-généré)"

else
    # --- Mode profil (non-interactif) ---

    # Lecture du profil via stdin (json.load(sys.stdin) + redirection) plutôt
    # que par interpolation du chemin dans le code Python, pour éviter
    # l'injection de commande si le chemin contient une apostrophe (C6).
    VALUES=$($PYTHON_CMD -c "
import json, sys
p = json.load(sys.stdin)

c = p.get('client', {})
s = p.get('server', {})
a = p.get('admin', {})
po = p.get('ports', {})
o = p.get('options', {})

print(c.get('name',''))
print(c.get('contact_name',''))
print(c.get('contact_email',''))
print(s['ip'])
_ip = s['ip'].rsplit('.',1)[0]
print(s.get('lan_cidr') or (_ip + '.0/24') if _ip else '0.0.0.0/0')
print(s.get('docker_subnet','10.42.0.0/16'))
print(a['username'])
print(a['password'])
print(a['email'])
print(po.get('http',80))
print(po.get('https',443))
print(po.get('api',8000))
print(str(o.get('https_enabled',True)).lower())
print(o.get('docker_scan_enabled',False))
" < "$PROFILE" 2>/dev/null) || err "Erreur de lecture du profil JSON '${PROFILE}'"

    CLIENT_NAME=$(echo "$VALUES"    | sed -n '1p')
    CLIENT_CONTACT=$(echo "$VALUES"  | sed -n '2p')
    CLIENT_EMAIL=$(echo "$VALUES"   | sed -n '3p')
    SERVER_IP=$(echo "$VALUES"       | sed -n '4p')
    LAN_CIDR=$(echo "$VALUES"        | sed -n '5p')
    DOCKER_SUBNET=$(echo "$VALUES"   | sed -n '6p')
    ADMIN_USER=$(echo "$VALUES"      | sed -n '7p')
    ADMIN_PASS=$(echo "$VALUES"      | sed -n '8p')
    ADMIN_EMAIL=$(echo "$VALUES"     | sed -n '9p')
    HUB_HTTP_PORT=$(echo "$VALUES"   | sed -n '10p')
    HUB_HTTPS_PORT=$(echo "$VALUES"  | sed -n '11p')
    HUB_API_PORT=$(echo "$VALUES"    | sed -n '12p')
    HTTPS_ENABLED=$(echo "$VALUES"   | sed -n '13p')
    DOCKER_SCAN_ENABLED=$(echo "$VALUES" | sed -n '14p')

    if [ ${#ADMIN_PASS} -lt 8 ]; then err "Mot de passe admin trop court dans le profil."; fi
    if [ -z "$SERVER_IP" ]; then err "server.ip requis dans le profil."; fi

    WHITELIST="${DOCKER_SUBNET},${LAN_CIDR}"
    CORS_ORIGINS="https://${SERVER_IP},http://${SERVER_IP}"
    DB_PASS=$(openssl rand -base64 18 2>/dev/null || $PYTHON_CMD -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || echo "CHANGEZ_MOI_$(date +%s)")
fi

ok "Hôte   : ${GREEN}${SERVER_IP}${NC}"
ok "Admin  : ${GREEN}${ADMIN_USER}${NC}"
ok "Ports  : HTTP ${HUB_HTTP_PORT} / HTTPS ${HUB_HTTPS_PORT} / API ${HUB_API_PORT}"
ok "HTTPS  : ${HTTPS_ENABLED}"
ok "Réseaux: ${WHITELIST}"
if [ -n "$CLIENT_NAME" ]; then
    ok "Client : ${GREEN}${CLIENT_NAME}${NC} — ${CLIENT_CONTACT} (${CLIENT_EMAIL})"
fi

say ""

# ── 2. Générer les secrets ──────────────────────────────────────────────────

say "${BOLD}2. Génération des secrets${NC}"

if $PYTHON_OK; then
    FERNET_KEY=$($PYTHON_CMD -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || \
        $PYTHON_CMD -c "import secrets; import base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
else
    FERNET_KEY=$(openssl rand -base64 32 2>/dev/null | tr '+/' '-_' | tr -d '=')
fi
ok "Clé Fernet générée"

# Clés JWT au chemin attendu par le code : secrets/jwt_private_key.pem
# (top-level dans secrets/, cf. app/config.py → local_secrets_dir / "jwt_private_key.pem")
JWT_PRIVATE="secrets/jwt_private_key.pem"
JWT_PUBLIC="secrets/jwt_public_key.pem"

# Migration : anciennes installs écrivaient dans secrets/jwt/. On les déplace au bon endroit.
if [ -f "secrets/jwt/private_key.pem" ] && [ ! -f "$JWT_PRIVATE" ]; then
    mkdir -p secrets
    mv "secrets/jwt/private_key.pem" "$JWT_PRIVATE"
    [ -f "secrets/jwt/public_key.pem" ] && mv "secrets/jwt/public_key.pem" "$JWT_PUBLIC"
    rmdir secrets/jwt 2>/dev/null || true
    ok "Clés JWT migrées secrets/jwt/ → secrets/ (chemin attendu par le code)"
fi

if [ ! -f "$JWT_PRIVATE" ]; then
    mkdir -p secrets
    if command -v openssl &> /dev/null; then
        openssl genpkey -algorithm RSA -out "$JWT_PRIVATE" -pkeyopt rsa_keygen_bits:4096 2>/dev/null
        openssl rsa -pubout -in "$JWT_PRIVATE" -out "$JWT_PUBLIC" 2>/dev/null
        chmod 600 "$JWT_PRIVATE"
        chmod 644 "$JWT_PUBLIC"
        ok "Clés JWT RSA 4096 → secrets/jwt_private_key.pem (+pub)"
        AUTO_KEYS=false
    else
        warn "openssl absent → AUTO_GENERATE_KEYS activé"
        AUTO_KEYS=true
    fi
else
    chmod 600 "$JWT_PRIVATE" 2>/dev/null || true
    chmod 644 "$JWT_PUBLIC" 2>/dev/null || true
    ok "Clés JWT déjà présentes"
    AUTO_KEYS=false
fi
AUTO_KEYS="${AUTO_KEYS:-false}"

# ── 3. Écrire .env ───────────────────────────────────────────────────────────

say "${BOLD}3. Écriture du .env${NC}"

# Chemin absolu du dossier APPS/ (au-dessus de HUB_base), détecté depuis
# l'emplacement réel du script — jamais du cwd ni d'une valeur en dur.
# Requis par docker-compose : APPS est monté au MÊME chemin côté hôte et côté
# conteneur hub-api (résolution des bind mounts des apps par le daemon hôte).
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APPS_HOST_PATH=$(cd "$SCRIPT_DIR/.." && pwd)/APPS

# Fusion idempotente des clés requises dans un .env EXISTANT conservé
# (jamais d'écrasement) : APPS_HOST_PATH est calculable localement → ajoutée
# automatiquement si absente ou vide. Les secrets (POSTGRES_PASSWORD,
# ADMIN_*, SECRETS_ENCRYPTION_KEY) ne sont pas inventables → erreur explicite
# s'ils manquent. Permet de réparer un serveur installé avant la 3.71.1 en
# relançant simplement install.sh — sans retouche manuelle du .env.
merge_required_env_keys() {
    local key value missing=""
    value=$(sed -n 's/^APPS_HOST_PATH=//p' .env 2>/dev/null | head -n1 | tr -d '[:space:]')
    if [ -z "$value" ]; then
        echo "APPS_HOST_PATH=${APPS_HOST_PATH}" >> .env
        ok "APPS_HOST_PATH ajouté au .env existant : ${APPS_HOST_PATH}"
    fi
    for key in POSTGRES_PASSWORD ADMIN_USERNAME ADMIN_PASSWORD SECRETS_ENCRYPTION_KEY; do
        value=$(sed -n "s/^${key}=//p" .env 2>/dev/null | head -n1 | tr -d '[:space:]')
        [ -z "$value" ] && missing="${missing} ${key}"
    done
    if [ -n "$missing" ]; then
        err "Clés requises absentes du .env conservé :${missing}
  Reprenez un .env complet (.env.old, coffre hubctl) ou relancez avec
  écrasement — ces secrets ne peuvent pas être régénérés sans perte."
    fi
}

if [ -f .env ]; then
    if $INTERACTIVE; then
        read -p "  .env existe. Écraser ? [o/N] : " OVERWRITE
        if [ "$OVERWRITE" != "o" ] && [ "$OVERWRITE" != "O" ]; then
            ok ".env conservé — fusion des clés manquantes requises (aucun écrasement)"
            merge_required_env_keys
            warn "Pour appliquer : make up-prod (ou docker compose up -d)"
            exit 0
        fi
    elif $AUTO; then
        warn ".env existant écrasé (mode auto)"
    elif ! $FORCE; then
        ok ".env conservé (mode profil sans --force) — fusion des clés manquantes requises"
        merge_required_env_keys
        err "Le mode --profile ne réécrit pas un .env existant (sécurité).
  La fusion ci-dessus a complété les clés calculables sans écrasement.
  Pour une régénération complète : relancez avec --force."
    else
        warn ".env existant écrasé (mode profil + --force)"
    fi
fi

cat > .env << EOF
# ======================================
# POULIDOR Hub — Configuration
# Généré le $(date '+%Y-%m-%d %H:%M')
EOF

if [ -n "$CLIENT_NAME" ]; then
    echo "# Client : ${CLIENT_NAME}" >> .env
fi

cat >> .env << EOF
# ======================================

# PostgreSQL
POSTGRES_USER=hubadmin
POSTGRES_PASSWORD=${DB_PASS}
POSTGRES_DB=hub_auth

# Hub API
DATABASE_URL=postgresql+asyncpg://hubadmin:${DB_PASS}@db:5432/hub_auth
JWT_ALGORITHM=RS256
APP_ENV=production
ACCESS_TOKEN_EXPIRE_MINUTES=480

# Admin
ADMIN_USERNAME=${ADMIN_USER}
ADMIN_PASSWORD=${ADMIN_PASS}
ADMIN_EMAIL=${ADMIN_EMAIL}

# Ports
HUB_API_PORT=${HUB_API_PORT}
HUB_HOST=${SERVER_IP}
HUB_FRONTEND_PORT=${HUB_HTTP_PORT}
HUB_HTTPS_PORT=${HUB_HTTPS_PORT}
HUB_HTTPS_ENABLED=${HTTPS_ENABLED}

# Clés JWT
AUTO_GENERATE_KEYS=${AUTO_KEYS}
FORCE_REPLACE_APP=false

# Sécurité
SECRETS_ENCRYPTION_KEY=${FERNET_KEY}
ALLOW_PLAINTEXT_SECRETS=false
SKIP_PASSWORD_CHECK=false

# Réseau
APP_WHITELIST_NETWORKS=${WHITELIST}

# CORS
ALLOWED_ORIGINS=${CORS_ORIGINS}

# Docker
DOCKER_SYNC_INTERVAL=300
DOCKER_SCAN_ENABLED=${DOCKER_SCAN_ENABLED}

# Audit
AUDIT_SSE_COOLDOWN=5.0
AUDIT_RETENTION_DAYS=90
ERROR_RETENTION_DAYS=30
LOG_PURGE_INTERVAL_HOURS=24

# PKI
PKI_CA_COMMON_NAME=Hub Local CA
PKI_CA_ORGANIZATION=Hub Internal
PKI_CA_VALIDITY_YEARS=10
PKI_CERT_DOMAINS=localhost,hub.local,hub-frontend
PKI_CERT_IPS=${SERVER_IP}
PKI_CERT_VALIDITY_DAYS=365
PKI_KEY_SIZE=4096
HOST_UID=$(id -u)
HOST_GID=$(id -g)

# Ports apps
APP_PORT_API_START=9000
APP_PORT_API_END=9499
APP_PORT_FRONTEND_START=4200
APP_PORT_FRONTEND_END=4699
PORT_SCAN_DOCKER_ENABLED=true

# Déploiement auto des apps — chemin absolu réel de APPS/ sur CE serveur
# (montage hôte/conteneur au même chemin ; détecté automatiquement ci-dessus)
APPS_HOST_PATH=${APPS_HOST_PATH}

# CORS regex (optionnel)
ALLOWED_ORIGIN_REGEX=

# Compatibilité legacy (désactivée par défaut)
LEGACY_COMPAT_ENABLED=false

# OIDC Provider (bridge OIDC pour apps externes)
OIDC_ENABLED=true
OIDC_ISSUER=http://${SERVER_IP}:${HUB_API_PORT}
OIDC_EMAIL_TEMPLATE={username}@hub.internal
OIDC_CODE_TTL_SECONDS=30
OIDC_ID_TOKEN_TTL_MINUTES=10
OIDC_ACCESS_TOKEN_TTL_MINUTES=15
OIDC_REFRESH_TOKEN_DAYS=30
OIDC_BACKCHANNEL_LOGOUT_ENABLED=true
OIDC_SLO_ENABLED=false
OIDC_SLO_IFRAME_TIMEOUT_MS=1500

# Service Account interne du HUB (M2M)
HUB_INTERNAL_SA_CLIENT_ID=hub-internal
HUB_INTERNAL_SA_CLIENT_SECRET=$(openssl rand -base64 32 2>/dev/null | tr -d '\n' || $PYTHON_CMD -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || echo "CHANGEZ_MOI_INTERNAL_SA")
EOF

ok ".env écrit"
chmod 600 .env
say ""

# ── 4. Démarrer ─────────────────────────────────────────────────────────────

say "${BOLD}4. Démarrage Docker Compose${NC}"
say ""
docker compose up -d
say ""

# ── 5. Vérification ─────────────────────────────────────────────────────────

say "${BOLD}5. Vérification santé${NC}"
say ""

sleep 5
MAX_WAIT=60
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    if curl -sk "http://localhost:${HUB_API_PORT}/api/health" 2>/dev/null | grep -q '"status":"healthy"'; then
        ok "Hub opérationnel en ${ELAPSED}s"
        break
    fi
    sleep 3
    ELAPSED=$((ELAPSED + 3))
    say "  ... attente ${ELAPSED}s/${MAX_WAIT}s"
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
    warn "Le Hub tarde à démarrer. Logs : docker compose logs hub-api"
fi
say ""

# ── 6. Résumé ───────────────────────────────────────────────────────────────

say "${BOLD}${GREEN}══════════════════════════════════════════════════════════${NC}"
say "${BOLD}${GREEN}   Hub installé !${NC}"
say "${BOLD}${GREEN}══════════════════════════════════════════════════════════${NC}"
say ""
if [ "$HTTPS_ENABLED" = "true" ]; then _SCHEME="https"; else _SCHEME="http"; fi
say "  Accès   : ${BOLD}${_SCHEME}://${SERVER_IP}:${HUB_HTTP_PORT}${NC}"
say "  Login   : ${BOLD}${ADMIN_USER}${NC}"
if $AUTO; then
    say "  Mot de passe : ${BOLD}${RED}${ADMIN_PASS}${NC}  ${YELLOW}(notez-le, il ne sera plus affiché)${NC}"
fi
if [ -n "$CLIENT_NAME" ]; then
    say "  Client  : ${BOLD}${CLIENT_NAME}${NC}"
fi
say ""
say "${BOLD}Prochaines étapes :${NC}"
say "  1. Ouvrez l'URL et connectez-vous"
say "  2. Changez le mot de passe admin"
say "  3. Déployez vos applications"
say ""
say "${BOLD}Commandes utiles :${NC}"
say "  docker compose ps"
say "  docker compose logs -f"
say "  docker compose down / up -d"
say ""
