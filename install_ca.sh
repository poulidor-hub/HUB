#!/bin/bash
# ============================================================
# Script d'installation du certificat CA Hub vers les applications
# ============================================================
# Ce script copie le certificat CA racine vers toutes les applications
# qui utilisent l'authentification Hub.
#
# Usage:
#   ./install_ca.sh                    # Toutes les apps HUBapp_*
#   ./install_ca.sh ../MonApp          # Une application spécifique
#   ./install_ca.sh --list             # Lister les apps détectées
#
# Après installation, rebuilder les applications:
#   cd ../HUBapp_TEMPLATE && docker compose up -d --build
# ============================================================

set -e

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Chemins
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_FILE="${SCRIPT_DIR}/certs/ca/ca.crt"
APPS_DIR="${SCRIPT_DIR}/.."

# Fonctions d'affichage
info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
success() { echo -e "${GREEN}✅ $1${NC}"; }
warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
error() { echo -e "${RED}❌ $1${NC}"; exit 1; }

# Afficher l'aide
show_help() {
    echo "Usage: $0 [OPTIONS] [APP_PATH...]"
    echo ""
    echo "Options:"
    echo "  --list, -l     Lister les applications détectées"
    echo "  --help, -h     Afficher cette aide"
    echo "  --all, -a      Installer dans toutes les apps HUBapp_*"
    echo ""
    echo "Exemples:"
    echo "  $0                     # Toutes les apps HUBapp_*"
    echo "  $0 ../MonApp           # Une application spécifique"
    echo "  $0 ../App1 ../App2     # Plusieurs applications"
    echo "  $0 --list              # Lister les apps détectées"
}

# Lister les applications détectées
list_apps() {
    info "Applications détectées dans ${APPS_DIR}:"
    echo ""
    
    local count=0
    for app in "${APPS_DIR}"/HUBapp_*/ "${APPS_DIR}"/hubapp_*/; do
        if [ -d "$app" ]; then
            local app_name=$(basename "$app")
            local has_dockerfile=$([ -f "${app}/Dockerfile.backend" ] && echo "✓" || echo "✗")
            local has_certs=$([ -f "${app}/certs/hub-ca.crt" ] && echo "✓ CA installé" || echo "✗ Pas de CA")
            echo "  📦 ${app_name}"
            echo "     └── ${has_certs}"
            ((count++)) || true
        fi
    done
    
    if [ $count -eq 0 ]; then
        warning "Aucune application HUBapp_* trouvée"
    else
        echo ""
        info "Total: $count application(s)"
    fi
}

# Installer le CA dans une application
install_ca_to_app() {
    local app_path="$1"
    local app_name=$(basename "$app_path")
    
    # Vérifier que c'est un dossier
    if [ ! -d "$app_path" ]; then
        warning "Dossier non trouvé: $app_path"
        return 1
    fi
    
    # Créer le dossier certs si nécessaire
    mkdir -p "${app_path}/certs"
    
    # Copier le CA
    cp "$CA_FILE" "${app_path}/certs/hub-ca.crt"
    
    success "CA installé dans ${app_name}/certs/hub-ca.crt"
    
    # Vérifier si .env existe et contient la config HTTPS
    if [ -f "${app_path}/.env" ]; then
        if ! grep -q "HUB_HTTPS_ENABLED" "${app_path}/.env"; then
            warning "Ajouter ces lignes dans ${app_name}/.env:"
            echo "     HUB_HTTPS_ENABLED=true"
            echo "     HUB_HTTPS_PORT=443"
            echo "     HUB_CA_CERT_PATH=/app/certs/hub-ca.crt"
        fi
    fi
    
    return 0
}

# ============================================================
# MAIN
# ============================================================

echo ""
echo "============================================================"
echo "   🔐 Installation du certificat CA Hub"
echo "============================================================"
echo ""

# Vérifier que le CA existe
if [ ! -f "$CA_FILE" ]; then
    error "Certificat CA non trouvé: $CA_FILE
    
Initialisez d'abord la PKI:
    python3 pki.py init
    python3 pki.py cert create hub"
fi

# Afficher les infos du CA
info "CA source: $CA_FILE"
openssl x509 -in "$CA_FILE" -noout -subject -enddate 2>/dev/null | while read line; do
    echo "   $line"
done
echo ""

# Parser les arguments
case "${1:-}" in
    --help|-h)
        show_help
        exit 0
        ;;
    --list|-l)
        list_apps
        exit 0
        ;;
    --all|-a|"")
        # Installer dans toutes les apps HUBapp_*
        installed=0
        for app in "${APPS_DIR}"/HUBapp_*/ "${APPS_DIR}"/hubapp_*/; do
            if [ -d "$app" ]; then
                install_ca_to_app "$app" && ((installed++)) || true
            fi
        done
        
        if [ $installed -eq 0 ]; then
            warning "Aucune application trouvée"
            echo ""
            info "Spécifiez le chemin manuellement:"
            echo "   $0 ../MonApplication"
        else
            echo ""
            success "CA installé dans $installed application(s)"
        fi
        ;;
    *)
        # Installer dans les apps spécifiées
        for app in "$@"; do
            # Convertir en chemin absolu si relatif
            if [[ "$app" != /* ]]; then
                app="${SCRIPT_DIR}/${app}"
            fi
            install_ca_to_app "$app"
        done
        ;;
esac

echo ""
echo "============================================================"
echo "   📋 Prochaines étapes"
echo "============================================================"
echo ""
echo "1. Vérifier la configuration .env des applications:"
echo "   HUB_HTTPS_ENABLED=true"
echo "   HUB_HTTPS_PORT=443"
echo "   HUB_CA_CERT_PATH=/app/certs/hub-ca.crt"
echo ""
echo "2. Rebuilder les applications:"
echo "   cd ../HUBapp_TEMPLATE && docker compose up -d --build"
echo ""
echo "============================================================"
