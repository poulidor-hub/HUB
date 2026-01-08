FROM nginx:alpine

# Copier la configuration nginx HTTP
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copier le script d'entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Copier les fichiers statiques
COPY public/ /usr/share/nginx/html/

EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]
