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

echo "✅ Initialisation terminée, lancement de l'application..."

# Exécuter la commande en tant que appuser
exec gosu appuser "$@"
