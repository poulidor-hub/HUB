#!/bin/bash
set -e

# =============================================================================
# Script d'entrypoint pour le Hub API
# Gère les permissions et lance l'application
# =============================================================================

echo "🚀 Initialisation du Hub API..."

# Créer les dossiers nécessaires s'ils n'existent pas
mkdir -p /code/secrets/service_accounts /code/storage/backups

# Ajuster les permissions du dossier secrets
if [ -d "/code/secrets" ]; then
    echo "🔑 Ajustement des permissions des secrets..."
    chown -R appuser:appuser /code/secrets 2>/dev/null || true
    chmod -R u+rw /code/secrets 2>/dev/null || true
fi

# Ajuster les permissions du dossier storage/backups
if [ -d "/code/storage" ]; then
    echo "💾 Ajustement des permissions du stockage..."
    chown -R appuser:appuser /code/storage 2>/dev/null || true
    chmod -R u+rw /code/storage 2>/dev/null || true
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

echo "✅ Initialisation terminée, lancement de l'application..."

# Exécuter la commande en tant que appuser
exec gosu appuser "$@"
