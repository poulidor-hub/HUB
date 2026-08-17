# POULIDOR Hub — Guide d'installation

Installation complète du Hub POULIDOR + déploiement d'applications sur un serveur client.

---

## Prérequis serveur

| Composant | Minimum |
|---|---|
| OS | Linux (Debian/Ubuntu recommandé) |
| RAM | 2 Go (4 Go avec applications) |
| Disque | 20 Go libres |
| Docker Engine | ≥ 24 |
| Docker Compose | plugin `docker compose` (v2) |
| Réseau | IP fixe sur le LAN, ports 80/443 disponibles |

### Installation rapide des prérequis (Debian/Ubuntu)

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 python3-openssl
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker
```

---

## Installation du Hub

### Méthode 1 : Script interactif (recommandé)

```bash
chmod +x install.sh
./install.sh
```

Le script vous guide pas à pas :
1. Adresse IP du serveur
2. Ports (HTTP / HTTPS / API)
3. Identifiants administrateur
4. Réseaux autorisés (CIDR)
5. Origines CORS
6. Génération automatique des secrets (Fernet, JWT)
7. Démarrage Docker Compose

À la fin, il affiche l'URL d'accès et les identifiants.

### Méthode 2 : Manuelle

```bash
cp .env.example .env
nano .env   # adapter toutes les valeurs
docker compose up -d
```

Vérifiez que la santé du Hub :

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

---

## Première connexion

1. Ouvrez `http://<IP_DU_SERVEUR>` dans un navigateur
2. Connectez-vous avec l'identifiant/mot de passe admin
3. Le Hub force le changement du mot de passe admin au premier login

### Accès HTTPS

Si `HUB_HTTPS_ENABLED=true` et que les certificats existent dans `certs/server/hub/` :
```
https://<IP_DU_SERVEUR>
```

Pour générer les certificats auto-signés (PKI intégrée) :

```bash
docker compose exec hub-api python pki.py init
docker compose exec hub-api python pki.py cert create hub
docker compose restart hub-frontend
```

---

## Déploiement d'une application

### Étape 1 : Créer l'application depuis le template

Chaque application POULIDOR est un projet Docker Compose indépendant.

```bash
# Depuis le méta-repo POULIDOR :
cd APPS/
../scripts/create_app.sh mon-app
cd mon-app
```

L'application est prête à être configurée et lancée.

### Étape 2 : Configurer l'application

```bash
cp .env.example .env
nano .env
```

**Valeurs minimales :**
```env
APP_NAME=Mon Application
APP_SLUG=mon-app
APP_HOST=<IP_DU_SERVEUR>
HUB_HOST=<IP_DU_SERVEUR>
HUB_URL=http://<IP_DU_SERVEUR>:8000
APP_PORT=9000
FRONTEND_PORT=4200
BOOTSTRAP_TOKEN=<token_généré_par_le_Hub>
```

### Étape 3 : Démarrer l'application

```bash
docker compose up -d
```

### Étape 4 : Enregistrer dans le Hub

L'application s'auto-enregistre auprès du Hub au premier démarrage via la variable `BOOTSTRAP_TOKEN`.

Pour générer un bootstrap token :
1. Dans le Hub → Administration → Applications → Ajouter
2. Créer l'application avec le slug correspondant
3. Copier le token de bootstrap généré

**Alternative — sans token bootstrap (auto-détection) :**
L'application détecte automatiquement le Hub et s'enregistre si son IP fait partie du `APP_WHITELIST_NETWORKS`. L'admin du Hub doit ensuite approuver manuellement l'application (statut `pending → active`).

### Étape 5 : Vérifier

Dans le Hub → Dashboard → l'application doit apparaître.

```bash
curl -s http://localhost:9000/api/health | python3 -m json.tool
```

---

## Structure des fichiers

```
HUB_base/
├── install.sh              # Script d'installation interactif
├── .env                    # Configuration (généré à l'installation)
├── docker-compose.yml      # Services Docker (Hub API + DB + Frontend)
├── secrets/
│   └── jwt/                # Clés RSA (générées à l'installation)
├── certs/                  # Certificats TLS (générés via PKI)
└── docs/
    ├── GUIDE_INSTALLATION.md  # Ce document
    └── GUIDE_ADMIN.md        # Guide d'administration
```

---

## Résolution de problèmes

### Le Hub ne démarre pas

```bash
docker compose logs hub-api | tail -50
```

**Erreurs fréquentes :**
- `SECRETS_ENCRYPTION_KEY` manquante → le script la génère automatiquement
- Port déjà utilisé → changer `HUB_*_PORT` dans `.env`
- `ALLOWED_ORIGINS` manquant en production → définir dans `.env`

### L'application ne s'enregistre pas

1. Vérifier que l'IP de l'app est dans `APP_WHITELIST_NETWORKS`
2. Vérifier les logs de l'app : `docker compose logs api`
3. Tester la connexion : `curl http://<IP_HUB>:8000/api/health` depuis le conteneur de l'app

### Les pages du dashboard ne se chargent pas (CORS)

Ajouter l'origine de l'application à `ALLOWED_ORIGINS` dans le `.env` du Hub :

```env
ALLOWED_ORIGINS=https://192.0.2.10,https://192.0.2.10:4243
```

Puis recréer le conteneur (⚠️ `docker compose restart` ne recharge **pas**
le `.env` — il redémarre le conteneur avec sa configuration de création) :

```bash
docker compose up -d hub-api
```

### `allocate-ports` ou l'auto-enregistrement d'une app renvoie 403

L'API n'accepte ces requêtes que depuis les réseaux listés dans
`APP_WHITELIST_NETWORKS` (par défaut : le réseau Docker des apps
`10.42.0.0/16` + le subnet LAN détecté à l'installation). Si le Hub voit
une IP hors liste (message : *« autorisé uniquement depuis le réseau Docker »*) :

1. Ajouter le subnet source dans `HUB_base/.env` :
   ```env
   APP_WHITELIST_NETWORKS=10.42.0.0/16,192.168.1.0/24
   ```
2. Recréer le conteneur : `docker compose up -d hub-api` (pas `restart`).

### L'app répond en HTTP mais pas en HTTPS (port frontend +1)

Le port HTTPS d'une app (ex: 4201) ne répond que si un certificat existe
pour elle dans la PKI du Hub. Sans certificat, seul le port HTTP est servi —
voir la documentation PKI pour en générer un.

---

## Mise à jour

```bash
git pull
docker compose down
docker compose up -d --build
```
