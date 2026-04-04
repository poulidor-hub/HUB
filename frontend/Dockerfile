FROM nginx:alpine

# Copier la configuration nginx HTTP
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copier le script d'entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Copier les fichiers statiques
COPY public/ /usr/share/nginx/html/

# Assurer que nginx peut écrire dans ses répertoires de cache
RUN chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d && \
    touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid

EXPOSE 80

USER nginx

ENTRYPOINT ["/docker-entrypoint.sh"]
