FROM nginx:alpine

# Copier la configuration nginx (HTTP par défaut)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copier la configuration HTTPS (utilisée si certificats présents)
COPY nginx-https.conf /etc/nginx/nginx-https.conf

# Copier le script d'entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Copier les fichiers statiques
COPY public/ /usr/share/nginx/html/

# Créer le dossier pour les certificats SSL
RUN mkdir -p /etc/nginx/ssl

EXPOSE 80 443

ENTRYPOINT ["/docker-entrypoint.sh"]
