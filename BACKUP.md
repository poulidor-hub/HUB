# 🔄 Système de Backup/Restauration HUB

Ce document décrit comment sauvegarder et restaurer un environnement HUB complet pour le cloner ou migrer vers un autre serveur.

## 📦 Ce qui est inclus dans un backup

| Élément | Inclus | Sensible |
|---------|--------|----------|
| **Utilisateurs** | ✅ Username, email, hash mot de passe, flags admin/active | 🔐 Hash bcrypt |
| **Applications** | ✅ Toute la configuration (URLs, icône, couleur, rôles) | - |
| **Associations User↔App** | ✅ Quel user a accès à quelle app avec quel rôle | - |
| **Service Accounts** | ✅ Nom, client_id, hash secret, apps autorisées | 🔐 Secret en clair (optionnel) |
| **Clés JWT RSA** | ✅ (optionnel) Clés privée et publique | 🔐 Clé privée |

## 🚀 Utilisation rapide

### Export via CLI (recommandé)

```bash
# Export complet vers un fichier
docker compose exec hub-api python export_env.py -o /code/secrets/service_accounts/hub_backup.json

# Export sans clés JWT (si vous voulez régénérer de nouvelles clés)
docker compose exec hub-api python export_env.py -o /code/secrets/service_accounts/hub_backup.json --no-jwt-keys

# Export sans secrets des service accounts
docker compose exec hub-api python export_env.py -o /code/secrets/service_accounts/hub_backup.json --no-sa-secrets

# Export vers stdout (pour rediriger vers fichier local)
docker compose exec hub-api python export_env.py > ./secrets/hub_backup.json
```

### Import via CLI

```bash
# Import basique (ignore les conflits)
docker compose exec hub-api python import_env.py /code/secrets/service_accounts/hub_backup.json

# Import avec mise à jour des existants
docker compose exec hub-api python import_env.py /code/secrets/service_accounts/hub_backup.json --mode update

# Import avec restauration des clés JWT
docker compose exec hub-api python import_env.py /code/secrets/service_accounts/hub_backup.json --restore-jwt

# Simulation (dry-run) pour voir ce qui serait fait
docker compose exec hub-api python import_env.py /code/secrets/service_accounts/hub_backup.json --dry-run
```

### Export via API

```bash
# Récupérer un token admin
TOKEN=$(curl -s -X POST "http://localhost:8000/api/auth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password&username=admin&password=admin123" | jq -r '.access_token')

# Export complet
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/admin/backup/export" | jq . > hub_backup.json

# Export sans clés JWT
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/admin/backup/export?include_jwt_keys=false" | jq . > hub_backup.json
```

### Import via API

```bash
curl -X POST "http://localhost:8000/api/admin/backup/import" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @hub_backup.json
```

## 📋 Modes d'import

| Mode | Description |
|------|-------------|
| `skip` | Ignore les entités déjà existantes (défaut, le plus sûr) |
| `update` | Met à jour les entités existantes avec les données du backup |
| `error` | Échoue immédiatement si une entité existe déjà |

## 🔐 Considérations de sécurité

### ⚠️ Le fichier de backup contient des données sensibles !

1. **Mots de passe** : Hashés en bcrypt, mais permettent de se connecter si restaurés
2. **Secrets Service Accounts** : En clair si `--no-sa-secrets` n'est pas utilisé
3. **Clé privée JWT** : Permet de signer des tokens valides

### Recommandations

```bash
# Chiffrer le backup après export
gpg -c hub_backup.json  # Crée hub_backup.json.gpg

# Déchiffrer avant import
gpg -d hub_backup.json.gpg > hub_backup.json

# Supprimer le fichier non chiffré
rm hub_backup.json
```

## 📁 Structure du fichier JSON

```json
{
  "version": "1.0",
  "exported_at": "2025-12-31T10:30:00",
  "exported_by": "admin",
  
  "users": [
    {
      "username": "admin",
      "email": "admin@local.lan",
      "hashed_password": "$2b$12$...",
      "is_active": true,
      "is_admin": true,
      "created_at": "2025-12-01T08:00:00"
    }
  ],
  
  "applications": [
    {
      "name": "API Salariés",
      "slug": "salarie",
      "description": "Gestion des salariés",
      "icon": "bi-people-fill",
      "color": "#198754",
      "api_url": "http://host.docker.internal:18087",
      "frontend_url": "http://localhost:4178",
      "callback_url": "http://localhost:4178/callback",
      "is_active": true,
      "requires_auth": true,
      "available_roles": [
        {"name": "admin", "description": "Administrateur"},
        {"name": "user", "description": "Utilisateur"}
      ]
    }
  ],
  
  "user_app_roles": [
    {
      "username": "admin",
      "app_slug": "salarie",
      "role": "admin"
    }
  ],
  
  "service_accounts": [
    {
      "name": "SA API Salariés",
      "client_id": "sa_abc123...",
      "client_secret_hash": "$2b$12$...",
      "client_secret_plain": "secret_en_clair_si_disponible",
      "description": "Service account pour M2M",
      "is_active": true,
      "app_slugs": ["salarie"]
    }
  ],
  
  "jwt_private_key_b64": "base64_encoded_private_key...",
  "jwt_public_key_b64": "base64_encoded_public_key..."
}
```

## 🔄 Scénarios d'utilisation

### 1. Cloner un environnement de dev vers staging

```bash
# Sur le serveur source (dev)
docker compose exec hub-api python export_env.py -o /app/secrets/backup_dev.json

# Copier le fichier vers staging
scp secrets/backup_dev.json staging-server:/path/to/hub/secrets/

# Sur staging
docker compose exec hub-api python import_env.py /app/secrets/backup_dev.json --restore-jwt
docker compose restart hub-api
```

### 2. Migration vers un nouveau serveur

```bash
# Export complet
docker compose exec hub-api python export_env.py -o /app/secrets/migration.json

# Sur le nouveau serveur (après avoir déployé le Hub)
docker compose exec hub-api python import_env.py /app/secrets/migration.json --mode update --restore-jwt
docker compose restart hub-api
```

### 3. Backup régulier (cron)

```bash
#!/bin/bash
# /etc/cron.daily/hub-backup

BACKUP_DIR="/backups/hub"
DATE=$(date +%Y%m%d)

cd /path/to/hub
docker compose exec -T hub-api python export_env.py -o /app/secrets/backup_${DATE}.json

# Garder les 7 derniers backups
ls -t ${BACKUP_DIR}/backup_*.json | tail -n +8 | xargs rm -f
```

### 4. Restauration après incident

```bash
# Arrêter le Hub
docker compose down

# Supprimer la base de données (⚠️ destructif)
docker volume rm hub_postgres_data

# Relancer et importer
docker compose up -d db
sleep 10  # Attendre que PostgreSQL démarre
docker compose up -d hub-api
sleep 5
docker compose exec hub-api python import_env.py /app/secrets/backup.json --restore-jwt
docker compose restart hub-api
```

## ❓ FAQ

### Les utilisateurs devront-ils se reconnecter ?

- **Si les clés JWT sont restaurées** : Non, les tokens existants restent valides
- **Si les clés JWT sont régénérées** : Oui, tous les tokens seront invalides

### Les secrets des Service Accounts sont-ils préservés ?

- **Avec `--no-sa-secrets`** : Non, il faudra régénérer de nouveaux secrets
- **Sans cette option** : Oui, les secrets en clair sont inclus et restaurés

### Puis-je modifier le fichier JSON avant import ?

Oui ! Vous pouvez :
- Changer les URLs des applications
- Modifier les rôles
- Supprimer des utilisateurs
- Adapter la configuration pour le nouvel environnement

### Comment exclure certains utilisateurs ?

Éditez le fichier JSON et supprimez les entrées non désirées des tableaux `users` et `user_app_roles`.
