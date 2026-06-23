# 🔄 Système de Snapshot/Sauvegarde HUB

Ce document décrit le système de snapshots complets du Hub : sauvegarde intégrale (PostgreSQL + fichiers + backups apps + config Docker), chiffrée et stockée sur S3/MinIO.

## 📦 Ce qui est inclus dans un snapshot

| Élément | Inclus | Collecteur |
|---------|--------|------------|
| **Toute la base PostgreSQL** | Toutes les tables (users, apps, rôles, profils, messages, audit, etc.) | `pg_dump --format=custom` |
| **Fichiers secrets** | Clés JWT, `.env` des service accounts, clés de chiffrement | `secrets.tar` |
| **Backups applicatifs** | Tous les ZIP de backup stockés dans `storage/backups/` | `storage_backups.tar` |
| **Config Docker** | `docker-compose.yml`, `VERSION`, `env.template` | copie directe |
| **Derniers backups d'apps** | Backup le plus récent de chaque app active | déclenchement via `perform_app_backup()` |

## 🖥️ Interface Web

Menu Admin → **Snapshots** permet de :
- **Créer** un snapshot manuel (bouton "Créer maintenant")
- **Suivre** la progression en temps réel (pg_dump → archivage → apps → chiffrement → upload S3)
- **Lister** les snapshots existants (statut, taille, date)
- **Configurer** le cron, la rétention, et le stockage S3
- **Restaurer** un snapshot sur le Hub existant
- **Télécharger** un snapshot (pour restauration bare-metal)

## 🚀 API REST

| Méthode | Endpoint | Rôle |
|---------|----------|------|
| `GET` | `/api/admin/snapshots` | Liste des snapshots |
| `POST` | `/api/admin/snapshots` | Déclencher un snapshot manuel |
| `GET` | `/api/admin/snapshots/in-progress` | Progression du snapshot en cours |
| `GET` | `/api/admin/snapshots/config` | Lire la configuration |
| `PUT` | `/api/admin/snapshots/config` | Modifier la configuration |
| `GET` | `/api/admin/snapshots/{id}` | Détail d'un snapshot |
| `DELETE` | `/api/admin/snapshots/{id}` | Supprimer un snapshot |
| `POST` | `/api/admin/snapshots/{id}/restore` | Restaurer (hub existant) |
| `POST` | `/api/admin/snapshots/{id}/restore/preview` | Prévisualiser le contenu |
| `GET` | `/api/admin/snapshots/{id}/download` | Télécharger le snapshot |

## ⚙️ Configuration

```bash
# Définir le cron (ex: quotidien à 2h du matin)
curl -X PUT "http://localhost:8000/api/admin/snapshots/config" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"snapshot_cron": "0 2 * * *"}'

# Définir la rétention (max 10 snapshots, âge minimum 48h)
curl -X PUT "http://localhost:8000/api/admin/snapshots/config" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"snapshot_retention_max": 10, "snapshot_retention_min_age_hours": 48}'

# Configurer S3/MinIO
curl -X PUT "http://localhost:8000/api/admin/snapshots/config" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "s3_endpoint": "https://s3.example.com",
    "s3_bucket": "hub-snapshots",
    "s3_region": "eu-west-1",
    "s3_access_key": "xxx",
    "s3_secret_key": "xxx"
  }'
```

## 🔄 Restauration bare-metal

```bash
# 1. Récupérer le snapshot
# Depuis l'UI admin : télécharger le snapshot
# Ou depuis S3 : aws s3 cp s3://bucket/prod/snapshot_xxx.tar.zst.enc .

# 2. Déchiffrer (nécessite la clé privée RSA du Hub)
# Le déchiffrement est intégré dans le flux de restauration API

# 3. Déployer un Hub vierge avec le docker-compose.yml extrait du snapshot
# Le snapshot contient docker-compose.yml + env.template

# 4. Restaurer la base PostgreSQL
pg_restore --clean --if-exists --host=db --username=hub --dbname=hub --no-owner --no-acl full_dump.pgdump

# 5. Extraire les secrets et les backups
tar -xf secrets.tar -C /code/secrets/
tar -xf storage_backups.tar -C /code/storage/
```

## 📋 Rétention

La rétention est appliquée après chaque snapshot réussi :
- **Nombre max** (`snapshot_retention_max`, défaut: 7) : garder les N derniers
- **Âge minimum** (`snapshot_retention_min_age_hours`, défaut: 24) : ne jamais supprimer ceux de moins de X heures
- La suppression supprime le fichier S3 et la ligne en base

## 🔐 Sécurité

- Le snapshot est chiffré **avant** upload S3 (AES-256-GCM + enveloppe RSA)
- S3 ne voit que des données opaques
- La clé privée RSA est nécessaire pour déchiffrer
- Les secrets service accounts (.env) sont préservés dans `secrets.tar`
- Les clés JWT sont incluses dans `secrets.tar`

## 📁 Format de l'archive

```
snapshot_{uuid}.tar.zst.enc
  ├── manifest.json
  ├── postgres/
  │   └── full_dump.pgdump
  ├── files/
  │   ├── storage_backups.tar
  │   └── secrets.tar
  ├── docker/
  │   ├── docker-compose.yml
  │   └── env.template
  └── apps/
      └── {slug}/
          └── backup_{uuid}.zip
```

## 🆚 Ancien système (obsolète)

L'export/import d'environnement JSON (`backup.py`, `export_env.py`, `import_env.py`) et les backup clusters ont été supprimés et remplacés par le système de snapshots. Les backups applicatifs individuels (`app_backups.py`) restent inchangés.
