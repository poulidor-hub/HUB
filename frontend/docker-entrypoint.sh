#!/bin/sh
# =============================================================================
# Entrypoint Nginx - HTTPS avec génération automatique des certificats
# =============================================================================
# Ce script génère automatiquement les certificats SSL s'ils n'existent pas,
# puis démarre Nginx en mode HTTPS.
#
# La PKI est initialisée automatiquement si nécessaire.
# =============================================================================

set -e

# Chemins des certificats
SSL_SOURCE_DIR="/etc/nginx/ssl/source"
SSL_CERT="/etc/nginx/ssl/hub.crt"
SSL_KEY="/etc/nginx/ssl/hub.key"
PKI_SCRIPT="/pki/pki.py"
PKI_CERTS_DIR="/pki/certs"
CA_KEY="$PKI_CERTS_DIR/ca/ca.key"

echo "🔍 Vérification de la configuration SSL..."

# Fonction pour générer les certificats
generate_certificates() {
    echo ""
    echo "🔧 Génération automatique des certificats SSL..."
    echo ""
    
    # Vérifier que le script PKI est disponible
    if [ ! -f "$PKI_SCRIPT" ]; then
        echo "❌ ERREUR : Script PKI non trouvé à $PKI_SCRIPT"
        exit 1
    fi
    
    cd /pki
    
    # Initialiser la CA si elle n'existe pas
    if [ ! -f "$CA_KEY" ]; then
        echo "📜 Initialisation de l'autorité de certification (CA)..."
        python3 pki.py init
        echo ""
    fi
    
    # Créer le certificat hub
    echo "📜 Création du certificat 'hub'..."
    python3 pki.py cert create hub --force
    echo ""
}

# Vérifier si les certificats existent dans le dossier source
if [ ! -f "$SSL_SOURCE_DIR/hub.crt" ] || [ ! -f "$SSL_SOURCE_DIR/hub.key" ]; then
    echo "⚠️  Certificats SSL non détectés"
    generate_certificates
fi

# Copier les certificats vers le dossier SSL de Nginx
if [ -f "$SSL_SOURCE_DIR/hub.crt" ] && [ -f "$SSL_SOURCE_DIR/hub.key" ]; then
    cp "$SSL_SOURCE_DIR/hub.crt" "$SSL_CERT"
    cp "$SSL_SOURCE_DIR/hub.key" "$SSL_KEY"
    echo "✅ Certificats SSL prêts"
    echo "   📜 Certificat: $SSL_CERT"
    echo "   🔑 Clé privée: $SSL_KEY"
else
    echo "❌ ERREUR : Impossible de générer les certificats SSL"
    exit 1
fi

# Copier la configuration HTTPS
if [ -f "/etc/nginx/nginx-https.conf" ]; then
    cp /etc/nginx/nginx-https.conf /etc/nginx/conf.d/default.conf
    echo "🔒 Mode HTTPS activé (ports 80 → 443)"
else
    echo "❌ ERREUR : Fichier nginx-https.conf manquant !"
    exit 1
fi

echo ""
echo "🚀 Démarrage de Nginx..."

# Exécuter Nginx
exec nginx -g "daemon off;"
