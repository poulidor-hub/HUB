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

## Coexistence de deux hubs sur le même host

Le Hub supporte l'installation de **deux instances indépendantes sur la même machine**
(ex. un hub de production et un banc de test). Sans coordination, les deux instances
entrent en collision : mêmes noms de conteneurs, mêmes plages de ports d'applications,
mêmes sous-réseaux Docker (cf. rapport de diagnostic — coexistence sans coordination =
allocations en collision). Pour les isoler, chaque instance doit définir dans son `.env` :

1. **Un nom de projet Compose unique** (obligatoire) :
   ```bash
   COMPOSE_PROJECT_NAME=hub-prod   # l'autre instance : hub-banc
   ```
   Sans lui, le projet implicite est nommé d'après le dossier — identique pour les deux
   instances si elles partagent un nom de dossier → conteneurs/volumes/réseaux en collision.

2. **Des ports Hub disjoints** (`HUB_API_PORT`, `HUB_FRONTEND_PORT`, `HUB_HTTPS_PORT`) :
   ex. 8000/80/443 pour l'un, 8100/4280/4243 pour l'autre.

3. **Des pools de ports applicatifs disjoints** :
   ```bash
   # Instance 1
   APP_PORT_API_START=9000
   APP_PORT_API_END=9499
   APP_PORT_FRONTEND_START=4200
   APP_PORT_FRONTEND_END=4699
   # Instance 2 (décalé)
   APP_PORT_API_START=9500
   APP_PORT_API_END=9999
   APP_PORT_FRONTEND_START=4700
   APP_PORT_FRONTEND_END=5199
   ```

4. **Des pools de sous-réseaux Docker disjoints** :
   ```bash
   APP_SUBNET_POOL=10.50.0.0/16   # l'autre instance : 10.51.0.0/16
   APP_SUBNET_SIZE=24
   ```

À noter : un fichier `docker-compose.override.yml` présent à la racine est chargé
automatiquement par le `Makefile` (s'il existe) et permet de surcharger la configuration
d'une instance sans toucher au `docker-compose.yml` d'origine.

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

**401 — Token M2M requis pour réinscrire cette application**

L'application est déjà inscrite et le Hub refuse de la réinscrire : elle a perdu
ses identifiants M2M ou présente un jeton invalide. Seul le jeton du compte de
service de l'application peut la modifier.

1. Ouvrir **Administration → Comptes de service** (outil « Service Accounts »)
2. Régénérer le secret du compte de l'application — le nouveau secret n'est affiché qu'une fois
3. Placer `M2M_CLIENT_ID` et `M2M_CLIENT_SECRET` dans l'environnement de l'application
4. Redémarrer l'application en recréant son conteneur (`docker compose up -d`) :
   un `restart` ne recharge pas le `.env`

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

> ⚠️ Ne jamais faire `git pull` / `docker compose` directement dans ce sous-module :
> la mise à jour passe par la routine officielle du méta-repo — voir
> `docs/ops/mise-a-jour.md` à la racine du dépôt POULIDOR (`./hubctl update hub`).
