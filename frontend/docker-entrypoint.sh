#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

# =============================================================================
# Entrypoint Nginx — sélection HTTP-only vs dual-stack (HTTP+HTTPS)
# + inclusion conditionnelle de PhiliaNG
# =============================================================================
set -e

SSL_CRT=/etc/nginx/ssl/server/hub/server.crt
SSL_KEY=/etc/nginx/ssl/server/hub/server.key
PHILIA_CRT=/etc/nginx/ssl/server/philia-ng/server.crt
PHILIA_KEY=/etc/nginx/ssl/server/philia-ng/server.key

if [ "${HUB_HTTPS_ENABLED:-true}" = "true" ] && [ -f "$SSL_CRT" ] && [ -f "$SSL_KEY" ]; then
    echo "🚀 Démarrage de Nginx (HTTP + HTTPS)..."
    # HUB_HTTPS_PORT = port PUBLIÉ (docker-compose "${HUB_HTTPS_PORT}:443") ;
    # le nginx interne écoute toujours 443 — le placeholder ${HTTPS_PORT} du
    # template construit l'URL de redirection du bloc 80 (301).
    sed -e "s/\${HTTPS_PORT}/${HUB_HTTPS_PORT:-443}/g" \
        /etc/nginx/templates/nginx-ssl.conf > /etc/nginx/conf.d/default.conf
    # PhiliaNG : inclus uniquement si les certificats sont présents.
    # Le modèle porte le jeton __PHILIA_NG_DOMAIN__ : le vhost est rendu au
    # démarrage avec PHILIA_NG_DOMAIN (proxy.example.lan par défaut) — le même
    # nom que celui du certificat, généré par hub-api sur la même variable.
    # Rendu refusé ou impossible : on s'arrête net, sans jamais lancer Nginx sur
    # un vhost absent ou à moitié rendu.
    if [ -f "$PHILIA_CRT" ] && [ -f "$PHILIA_KEY" ]; then
        PHILIA_DOMAIN="${PHILIA_NG_DOMAIN:-proxy.example.lan}"
        if ! /usr/local/bin/render-philia-conf.sh \
                /etc/nginx/templates/nginx-philia-ng.conf \
                /etc/nginx/conf.d/philia-ng.conf "$PHILIA_DOMAIN"; then
            printf "❌ PhiliaNG : rendu du vhost impossible (PHILIA_NG_DOMAIN='%s') — démarrage de Nginx annulé\n" \
                "$PHILIA_DOMAIN" >&2
            exit 1
        fi
        echo "   → PhiliaNG activé ($PHILIA_DOMAIN)"
    else
        echo "   → PhiliaNG désactivé (certificats absents)"
    fi
    # Chantier B : vhosts SNI générés par hub-api (routage par noms des apps
    # et services tiers) — dossier vide tant que la zone n'est pas configurée
    echo 'include /etc/nginx/vhosts.d/*.conf;' > /etc/nginx/conf.d/routing-gateway.conf
else
    echo "🚀 Démarrage de Nginx (HTTP only)..."
    cp /etc/nginx/templates/nginx.conf /etc/nginx/conf.d/default.conf
fi

exec nginx -g "daemon off;"
