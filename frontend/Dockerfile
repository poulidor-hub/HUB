# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

FROM nginx:alpine

# Copier les templates de configuration nginx (HTTP-only, dual-stack, PhiliaNG)
# + le snippet factorisé des headers de sécurité (inclus par les confs, cf.
# security-headers.conf — jamais dans conf.d/*.conf : auto-inclusion http).
COPY nginx.conf nginx-ssl.conf nginx-philia-ng.conf security-headers.conf /etc/nginx/templates/

# Copier le script d'entrypoint + le rendu du vhost PhiliaNG (jeton
# __PHILIA_NG_DOMAIN__ → PHILIA_NG_DOMAIN, cf. docker-entrypoint.sh)
COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY render-philia-conf.sh /usr/local/bin/render-philia-conf.sh
RUN chmod +x /docker-entrypoint.sh /usr/local/bin/render-philia-conf.sh

# Copier les fichiers statiques
COPY public/ /usr/share/nginx/html/

# Assurer que nginx peut écrire dans ses répertoires de cache
RUN chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d /etc/nginx/templates && \
    touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid

EXPOSE 80 443

USER nginx

ENTRYPOINT ["/docker-entrypoint.sh"]
