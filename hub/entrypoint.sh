#!/bin/bash
set -e

# =============================================================================
# Script d'entrypoint pour le Hub API
# Gère les permissions, la restauration snapshot, et lance l'application
# =============================================================================

echo "🚀 Initialisation du Hub API..."

# Créer les dossiers nécessaires s'ils n'existent pas
mkdir -p /code/secrets/service_accounts /code/storage/backups /code/storage/snapshots

# Ajuster les permissions du dossier secrets (lecture/écriture owner uniquement)
if [ -d "/code/secrets" ]; then
    echo "🔑 Ajustement des permissions des secrets..."
    chown -R appuser:"$(id -gn appuser)" /code/secrets 2>/dev/null || true
    chmod -R u+rwX,go-rwx /code/secrets 2>/dev/null || true
fi

# Ajuster les permissions du dossier storage
if [ -d "/code/storage" ]; then
    echo "💾 Ajustement des permissions du stockage..."
    chown -R appuser:"$(id -gn appuser)" /code/storage 2>/dev/null || true
    chmod -R u+rw /code/storage 2>/dev/null || true
fi

# Ajuster les permissions du volume VPN WireGuard (partagé avec wireguard-server)
if [ -d "/vpn" ]; then
    echo "🛡️ Ajustement des permissions VPN..."
    mkdir -p /vpn/wg_confs
    chown -R appuser:"$(id -gn appuser)" /vpn 2>/dev/null || true
fi

# Ajouter appuser au groupe docker pour accéder au socket Docker
# Le GID du groupe docker est passé via group_add dans docker-compose
if [ -S "/var/run/docker.sock" ]; then
    DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
    echo "🐳 Configuration de l'accès Docker (GID: $DOCKER_GID)..."
    # Créer le groupe docker s'il n'existe pas
    if ! getent group "$DOCKER_GID" >/dev/null 2>&1; then
        groupadd -g "$DOCKER_GID" docker 2>/dev/null || true
    fi
    # Ajouter appuser au groupe docker
    usermod -aG "$DOCKER_GID" appuser 2>/dev/null || true
fi

# Restauration snapshot en attente (apres restart declenche par restore)
if [ -f "/code/storage/pending_restore.txt" ]; then
    echo "📦 Restauration snapshot detectee, lancement de la restauration..."
    gosu appuser python app/restore_snapshot.py || echo "⚠️ Restauration terminee avec des erreurs"
    echo "✅ Restauration terminee, demarrage de l'API..."
fi

# Bootstrap HTTPS : générer CA + cert serveur si absents (mode hybride)
if [ "${HUB_HTTPS_ENABLED:-true}" = "true" ]; then
    mkdir -p /code/certs/ca /code/certs/server/hub /code/certs/server/philia-ng
    chown -R appuser:"$(id -gn appuser)" /code/certs 2>/dev/null || true
    chmod 0710 /code/certs /code/certs/server /code/certs/server/hub /code/certs/server/philia-ng 2>/dev/null || true
    chmod 0700 /code/certs/ca 2>/dev/null || true
    [ ! -f /code/certs/ca/ca.key ] || chmod 0600 /code/certs/ca/ca.key
    [ ! -f /code/certs/server/hub/server.key ] || chmod 0640 /code/certs/server/hub/server.key
    [ ! -f /code/certs/server/philia-ng/server.key ] || chmod 0640 /code/certs/server/philia-ng/server.key
    if [ ! -f /code/certs/server/hub/server.crt ]; then
        echo "🔐 HTTPS activé, certificats absents → génération via pki.py..."
        [ -f /code/certs/ca/ca.crt ] || gosu appuser python pki.py init
        gosu appuser python pki.py cert create hub
        echo "🔐 Certificats générés dans /code/certs/."
    else
        echo "🔐 HTTPS activé, certificats existants → conservés."
    fi

    # Cert PhiliaNG (proxy transparent SNI service.example.org).
    # Auto-généré s'il manque pour que le vhost service.example.org du hub-frontend
    # soit actif PAR DÉFAUT sur tout déploiement — l'utilisation depuis un autre
    # serveur est la norme, pas une exception demandant une manip manuelle.
    # Le domaine doit matcher le server_name de frontend/nginx-philia-ng.conf.
    PHILIA_DOMAIN="${PHILIA_NG_DOMAIN:-service.example.org}"
    if [ ! -f /code/certs/server/philia-ng/server.crt ]; then
        echo "🔐 Certificat PhiliaNG absent → génération ($PHILIA_DOMAIN)..."
        [ -f /code/certs/ca/ca.crt ] || gosu appuser python pki.py init
        gosu appuser python pki.py cert create philia-ng \
            --cn "$PHILIA_DOMAIN" --san "$PHILIA_DOMAIN"
        chmod 0640 /code/certs/server/philia-ng/server.key 2>/dev/null || true
        echo "🔐 Certificat PhiliaNG généré → vhost $PHILIA_DOMAIN actif au prochain démarrage du hub-frontend."
    else
        echo "🔐 Certificat PhiliaNG existant → conservé."
    fi
fi

echo "✅ Initialisation terminée, lancement de l'application..."

# Exécuter la commande en tant que appuser
exec gosu appuser "$@"
