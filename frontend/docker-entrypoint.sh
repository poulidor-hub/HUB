#!/bin/sh
# =============================================================================
# Entrypoint Nginx — sélection HTTP-only vs dual-stack (HTTP+HTTPS)
# =============================================================================
set -e

SSL_CRT=/etc/nginx/ssl/server/hub/server.crt
SSL_KEY=/etc/nginx/ssl/server/hub/server.key

if [ "${HUB_HTTPS_ENABLED:-true}" = "true" ] && [ -f "$SSL_CRT" ] && [ -f "$SSL_KEY" ]; then
    echo "🚀 Démarrage de Nginx (HTTP + HTTPS)..."
    cp /etc/nginx/templates/nginx-ssl.conf /etc/nginx/conf.d/default.conf
else
    echo "🚀 Démarrage de Nginx (HTTP only)..."
    cp /etc/nginx/templates/nginx.conf /etc/nginx/conf.d/default.conf
fi

exec nginx -g "daemon off;"
