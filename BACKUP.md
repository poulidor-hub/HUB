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
| **Code des apps (`APPS/`)** | Code + config + `.git` (sans node_modules & artefacts régénérables) | `apps/apps_code.tar` |

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

## 🔄 Restauration bare-metal (nouvelle machine)

> ⚠️ **Prérequis vital** : le snapshot est chiffré par enveloppe hybride
> (AES-256-GCM + RSA). La clé privée RSA `secrets/jwt_private_key.pem` est
> DANS le snapshot chiffré, mais il faut cette clé pour déchiffrer le
> snapshot. **Sans copie hors-ligne de cette clé, aucun snapshot n'est
> récupérable.** Conserver hors de la machine (coffre hubctl
> `export_recovery` + copie Offline) :
> 1. `secrets/` complet (clé JWT RSA incluse)
> 2. Les fichiers `.env` (HUB_base et APPS/*)

### Procédure semi-automatique (recommandée)

```bash
# 1. Reconstruire l'environnement (le compose du HUB n'est PAS dans le snapshot)
git clone <repo POULIDOR> && cd HUB_base
#    Restaurer secrets/ et .env depuis le coffre hubctl (export_recovery)

# 2. Builder et démarrer la stack vierge (DB comprise)
make build && make up-prod          # attendre hub-api healthy

# 3. Déposer l'archive du snapshot (UI admin → Snapshots → ⬇, API download, ou S3)
cp snapshot_{uuid}.tar.zst.enc storage/snapshots/

# 4. Créer la ligne de métadonnées (machine neuve = base vide, l'API ne peut pas le faire)
docker compose exec db psql -U hubadmin -d hub_auth -c \
  "INSERT INTO hub_snapshots (snapshot_id, status, created_by, created_at, local_path)
   VALUES ('{uuid}', 'success', 'pra', now(), '/code/storage/snapshots/snapshot_{uuid}.tar.zst.enc');"

# 5. Demander la restauration et redémarrer
docker exec hub-api sh -c "echo '{uuid}' > /code/storage/pending_restore.txt"
docker compose restart hub-api
```

Au redémarrage, l'entrypoint détecte `pending_restore.txt` et lance
`restore_snapshot.py` : déchiffrement → `pg_restore --clean --if-exists` →
extraction `secrets.tar` + `storage_backups.tar` + **`apps_code.tar` vers
`../APPS`** → (option) reset du mot de passe admin. Les migrations
manquantes sont rejouées automatiquement au boot (`apply_migrations()`) :
restaurer un snapshot ancien sur une image récente est sûr.

💡 **Mot de passe admin perdu** : définir `ADMIN_PASSWORD=nouveau_mdp` dans
le `.env` avant l'étape 5 — le script le réinitialise.

### Redémarrer les apps et réinjecter leurs données

```bash
# 6a. Rebuilder chaque app depuis le code restauré (images non incluses au snapshot)
cd APPS/<app> && make build && make up   # répéter par app

# 6b. Réinjecter les données : UI admin → Backups → Restaurer, ou API :
curl -X POST "http://localhost:8000/api/apps/{slug}/backup/{backup_id}/restore" \
  -H "Authorization: Bearer $TOKEN"
```

Les lignes `app_backups` (et leurs `backup_id`) sont restaurées avec la DB
du HUB à l'étape 5 → l'UI fonctionne directement. Les apps doivent être
**démarrées** pour accepter leur restore (endpoint côté app) : c'est une
étape du runbook, pas du restart automatique.

### Limites connues

| # | Limite | Contournement |
|---|--------|---------------|
| 1 | `docker-compose.yml` du HUB absent du snapshot (le collector cherche `/code/docker-compose.yml`, inexistant dans le conteneur) | `git clone` du repo |
| 2 | Images Docker des apps non capturées (par design) | `make build` depuis le code restauré |
| 3 | Déploiement sans montage `../APPS` : capture ignorée (warning), restauration sautée | Extraction manuelle : voir `apps/apps_code.tar` dans l'archive |
| 4 | Restaurer via l'API (`POST /api/admin/snapshots/{id}/restore`) sur un Hub **en production** réécrit aussi le code APPS/ en place (montage rw) | Réservé au PRA / à assumer explicitement |

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
  │   ├── env.template              ← template .env pour déploy bare-metal
  │   └── VERSION
  └── apps/
      ├── apps_code.tar              ← code + config + .git de APPS/
      └── {slug}/
          └── backup_{uuid}.zip
```

## 🆚 Ancien système (obsolète)

L'export/import d'environnement JSON (`backup.py`, `export_env.py`, `import_env.py`) et les backup clusters ont été supprimés et remplacés par le système de snapshots. Les backups applicatifs individuels (`app_backups.py`) restent inchangés.
