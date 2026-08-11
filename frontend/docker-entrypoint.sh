#!/bin/sh
# =============================================================================
# Entrypoint Nginx — sélection HTTP-only vs dual-stack (HTTP+HTTPS)
# + inclusion conditionnelle de PhiliaNG (service.example.org)
# =============================================================================
set -e

SSL_CRT=/etc/nginx/ssl/server/hub/server.crt
SSL_KEY=/etc/nginx/ssl/server/hub/server.key
PHILIA_CRT=/etc/nginx/ssl/server/philia-ng/server.crt
PHILIA_KEY=/etc/nginx/ssl/server/philia-ng/server.key

if [ "${HUB_HTTPS_ENABLED:-true}" = "true" ] && [ -f "$SSL_CRT" ] && [ -f "$SSL_KEY" ]; then
    echo "🚀 Démarrage de Nginx (HTTP + HTTPS)..."
    cp /etc/nginx/templates/nginx-ssl.conf /etc/nginx/conf.d/default.conf
    # PhiliaNG : inclus uniquement si les certificats sont présents
    if [ -f "$PHILIA_CRT" ] && [ -f "$PHILIA_KEY" ]; then
        echo "   → PhiliaNG (service.example.org) activé"
        cp /etc/nginx/templates/nginx-philia-ng.conf /etc/nginx/conf.d/philia-ng.conf
    else
        echo "   → PhiliaNG désactivé (certificats absents)"
    fi
else
    echo "🚀 Démarrage de Nginx (HTTP only)..."
    cp /etc/nginx/templates/nginx.conf /etc/nginx/conf.d/default.conf
fi

exec nginx -g "daemon off;"
