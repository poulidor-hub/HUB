#!/usr/bin/env bash
# Génère des secrets forts pour le Hub. Copiez les valeurs dans .env.
# DB déjà initialisée ? Après changement, exécutez aussi dans le conteneur db :
#   ALTER USER <POSTGRES_USER> WITH PASSWORD '<nouveau mot de passe>';
set -euo pipefail

echo "POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "ADMIN_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
echo "SECRETS_ENCRYPTION_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
echo
echo "# Mettez à jour DATABASE_URL avec le nouveau POSTGRES_PASSWORD :"
echo "#   DATABASE_URL=postgresql+asyncpg://hubadmin:<POSTGRES_PASSWORD>@db:5432/hub_auth"
