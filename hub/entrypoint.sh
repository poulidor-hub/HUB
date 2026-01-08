#!/bin/bash
set -e

# =============================================================================
# Script d'entrypoint pour le Hub API
# Gère les permissions des certificats et lance l'application
# =============================================================================

echo "🚀 Initialisation du Hub API..."

# Créer les dossiers nécessaires s'ils n'existent pas
mkdir -p /code/certs/ca /code/certs/server /code/certs/export /code/secrets/service_accounts

# Ajuster les permissions des certificats pour que appuser puisse les lire/écrire
# Le dossier certs est monté depuis l'hôte, on doit ajuster les permissions
if [ -d "/code/certs" ]; then
    echo "🔐 Ajustement des permissions des certificats..."
    chown -R appuser:appuser /code/certs 2>/dev/null || true
    chmod -R u+rw /code/certs 2>/dev/null || true
    
    # S'assurer que la clé CA est lisible par appuser
    if [ -f "/code/certs/ca/ca.key" ]; then
        chmod 600 /code/certs/ca/ca.key 2>/dev/null || true
        chown appuser:appuser /code/certs/ca/ca.key 2>/dev/null || true
    fi
fi

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
