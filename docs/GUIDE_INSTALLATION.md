# POULIDOR Hub — Guide d'installation

Installation du Hub POULIDOR (Hub + socle applicatif) sur un serveur client, puis
déploiement des applications. Le point d'entrée est **le méta-repo POULIDOR** : son
script `install.sh` pilote l'ensemble (prérequis système, initialisation du Hub,
registre). Toutes les commandes de ce guide se lancent depuis sa racine.

---

## Prérequis serveur

| Composant | Minimum |
|---|---|
| OS | Linux Debian/Ubuntu (requis : `apt-get`) |
| RAM | 2 Go (4 Go avec applications) |
| Disque | 20 Go libres |
| Docker Engine | ≥ 24, plugin `docker compose` (v2) |
| Réseau | IP fixe sur le LAN, ports 80/443 disponibles |

`sudo ./install.sh` installe lui-même les paquets manquants (Docker, GitHub CLI,
git, make, openssl…) — le tableau ci-dessus sert surtout à dimensionner la machine.

Pour préparer la machine à la main (Debian/Ubuntu) :

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 python3-openssl
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker
```

---

## Installation du Hub

### Parcours standard : `sudo ./install.sh` à la racine du méta-repo

```bash
sudo ./install.sh
```

Le script est **idempotent** : relançable sans risque à tout moment (reprise après
un timeout de build, Hub arrêté, etc.).

Ce qu'il fait, dans l'ordre :

1. **Prérequis système** : Docker (daemon + groupe utilisateur), GitHub CLI (`gh`),
   git, make, openssl ;
2. **Pools d'adresses Docker** : `172.16.0.0/12` et `10.200.0.0/14` (en /24 —
   ~5 100 réseaux pour les applications) ;
3. **`./hubctl init`**, le wizard d'initialisation du Hub :
   - génération du `.env` du Hub (`HUB_base/.env`) avec secrets aléatoires
     (Fernet, JWT, mot de passe admin) ;
   - génération des clés JWT (RSA) ;
   - build + démarrage des conteneurs, puis attente du health check ;
   - **PKI automatique** : au premier démarrage HTTPS, la CA interne et le
     certificat serveur du Hub sont générés dans `certs/` par le conteneur —
     aucune commande de certificat à lancer à la main ;
   - registre des applications prêt (approbations, allocations de ports).

Options : `--force` (régénère le `.env` du Hub — le fichier existant est réécrit),
`--runner` (opt-in : installe aussi le runner CI GitHub self-hosted).

### Récapitulatif de fin d'installation

Le script affiche à la fin :

- l'URL du Hub : **`https://<IP_DU_SERVEUR>`** (HTTPS, certificat signé par la CA
  interne — pour supprimer l'avertissement navigateur, importez la CA racine, voir
  [Confiance navigateur](#confiance-navigateur--la-ca-racine)) ;
- le mot de passe admin initial : variable `ADMIN_PASSWORD` dans `HUB_base/.env`.
  Le Hub force son changement au premier login.

### Note avancée : installer le Hub seul (`HUB_base/install.sh`)

Le sous-module `HUB_base` embarque son propre `install.sh` pour poser un Hub sans
le reste du parc (c'est d'ailleurs la voie utilisée en interne par `./hubctl init`) :

- mode **auto** par défaut : full automatique, auto-détecte tout, zéro question ;
- `--interactive` : guide l'utilisateur pas à pas ;
- `--profile client.json` : non-interactif via un fichier de profil JSON
  (`--force` autorise l'écrasement d'un `.env` existant).

Lancer les conteneurs à la main (`cp .env.example .env` + `docker compose up -d`)
est **déconseillé** : vous passez à côté de la génération des secrets, de la PKI
automatique et du registre.

---

## Installer une application

Une application s'installe depuis la racine du méta-repo :

```bash
./hubctl app install <url-git> [url-git…] [--no-deps] [--yes]
```

Le slug de l'application est dérivé du nom du dépôt git (normalisé) ; elle est
clonée dans `APPS/<slug>/`. Pour chaque application, `hubctl` enchaîne :

1. **Précheck** : Docker joignable, Hub en santé, pools d'adresses disponibles ;
2. **Clone** dans `APPS/<slug>/` (dossier déjà présent ⇒ convergence, `.env`
   préservé) ;
3. **Dépendances d'abord** : le `depends.yaml` de l'application est résolu au
   registre (ignorable avec `--no-deps`) ;
4. **`.env` généré** : ports **alloués automatiquement** depuis les pools
   (API + frontend), secrets par défaut **neutralisés**, images du socle
   garanties ;
5. **`make sync-env`** puis **`make build`** (images de l'application) ;
6. **Pré-création approuvée** : l'application est enregistrée d'avance au registre
   du Hub avec un jeton admin, statut **approuvé** — elle naît inscrite, pas « en
   attente » ;
7. **`make up`** puis **health check** ;
8. **Certificat HTTPS signé par la CA du Hub** — le port HTTPS de l'application
   répond immédiatement ;
9. **Rapport final** : URL de chaque application installée.

### Options

- `--no-deps` : ignore `depends.yaml` (l'application seule) ;
- `--yes` : aucune confirmation interactive (scripts, CI) ;
- **Seed YAML** : `./hubctl app install <fichier-seed.yaml>` déploie un lot
  déclaratif d'applications en une seule passe.

### Sans jeton admin : approbation manuelle

Sans mot de passe admin disponible (mode scripté, `--yes` sans
`HUBCTL_ADMIN_PASSWORD`), la pré-création approuvée est sautée : l'application
reste **« En attente »** dans le Hub (elle tourne, mais non approuvée). Pour
l'approuver :

1. Ouvrez **Administration → Applications** ;
2. Filtrez le statut « En attente » ;
3. Cliquez sur **Approuver** sur la ligne de l'application.

### Confiance navigateur : la CA racine

Les certificats (Hub et applications) sont signés par la CA interne du Hub. Pour
supprimer les avertissements navigateur des postes clients, importez la CA racine :

```
GET https://<IP_DU_SERVEUR>/api/pki/ca.crt
```

> Créer une **nouvelle** application depuis le template est un travail de
> développement (outils du méta-repo, cf. `scripts/create_app.sh` et la
> documentation développeur) — ce n'est pas une étape d'installation chez un
> client, qui ne fait que déployer des dépôts existants.

---

## Mise à jour

> ⚠️ Ne jamais faire `git pull` / `docker compose` directement dans ce sous-module :
> la mise à jour passe par la routine officielle du méta-repo — voir
> `docs/ops/mise-a-jour.md` à la racine du dépôt POULIDOR (`./hubctl update hub`).

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
4. **Supprimer le cache d'identifiants de l'application** : sur l'hôte,
   `rm APPS/<slug>/backend/storage/.hub_credentials` (dans le conteneur :
   `/app/storage/.hub_credentials`). Le socle lit ce fichier AVANT l'environnement :
   tant qu'il contient l'ancien secret, le nouveau n'est jamais présenté au Hub
   et l'application continue de recevoir 401. Si `HUB_CLIENT_STORAGE_DIR` est
   défini pour l'application, le fichier se trouve dans ce dossier.
5. Redémarrer l'application en recréant son conteneur (`docker compose up -d`) :
   un `restart` ne recharge pas le `.env`. Le fichier de cache est recréé au
   premier démarrage, avec les identifiants de l'environnement.

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
