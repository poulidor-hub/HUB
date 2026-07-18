# Changelog - Hub d'Authentification

> Ce fichier documente toutes les modifications apportées à la documentation et à l'API.
> Les agents IA peuvent utiliser `/api/docs/changelog` pour consulter cet historique.

---

---

## [3.12.0] - 18 juillet 2026

### ⚡ Dépendances inter-applications et maintenance admin

- **Ajouté** : modèle `AppDependency` et migration `037_add_app_dependencies.sql` pour stocker les déclarations M2M entre applications.
- **Ajouté** : `POST /api/apps/dependencies` (service account), avec conservation de `last_sync_at` pour les relations existantes.
- **Ajouté** : `POST /api/apps/sync-report` (service account) pour remonter la dernière synchronisation, y compris pour une relation encore orpheline.
- **Ajouté** : `GET /api/apps/dependencies` réservé aux administrateurs, pour consulter le graphe complet du parc.
- **Ajouté** : section de maintenance admin dans la SPA et navigation dédiée.
- **Sécurité** : la clé TLS `server.key` passe en `0640` avec groupe hôte partagé ; la CA reste en `0600` et le frontend nginx perd les capacités excessives de contournement des permissions.
- **Tests** : couverture des déclarations, mises à jour, accès service account et consultation admin du graphe.

---

## [3.11.2] - 16 juillet 2026

### ⚡ Correctifs

- **SSE** : le endpoint `/api/events/stream` rejetait tous les tokens JWT avec 401. Le code cherchait `payload.user_id` (absent du JWT, qui ne contient que `sub`). Corrigé en résolvant `sub` → `user_id` via DB lookup, comme le fait `get_current_user`.
- **Register M2M** : une app qui se ré-enregistrait (nouveau conteneur) perdait ses credentials M2M. Le register ne retournait que `client_id`, l'app appelait `regenerate-secret` → 401 (poule-et-œuf). Corrigé en régénérant le secret directement dans le register.

---

## [3.11.1] - 14 juillet 2026

### 🔐 Fix permissions SSL — server.key lisible par le frontend nginx

- **pki.py** : `server.key` désormais générée en `0o644` (était `0o600`).
  Le conteneur frontend (nginx, user non-root, montage `./certs` en `:ro`)
  ne pouvait pas lire la clé, causant un crash-loop sur chaque installation
  fraîche. La `ca.key` reste en `0o600` (seul le hub-api en a besoin).

---

## [3.11.0] - 14 juillet 2026

### 🔒 Cycle de vie du conteneur WireGuard — ne persiste plus seul

- **Modifié** : `wireguard-client.restart` passe de `unless-stopped` à `"no"` — le conteneur ne survit plus aux reboots tout seul (il tournait à vide tant que `vpn_enabled=false`, consommant RAM + exposant une surface `NET_ADMIN`/`SYS_MODULE` inutile).
- **Ajouté** : bandeau informatif (`alert-info`) sur l'onglet VPN — précise que le conteneur démarre au prochain boot du Hub ou via `hubctl vpn sync`, et que le toggle UI exprime l'intention (l'application effective se fait côté hôte).
- **Pourquoi** : le cycle de vie du conteneur est désormais géré au niveau hôte par `hubctl vpn` (côté POULIDOR) + un hook de boot, sans donner le socket Docker à `hub-api` (durcissement préservé).

---

## [3.10.0] - 10 juillet 2026

### ⚡ Export global du catalogue M2M du parc

- **Ajouté** : `GET /api/apps/m2m-catalog/export` (admin-only) — agrège la surface d'API de toutes les apps partagées (`is_shared_service=true`) en un fichier JSON téléchargeable (`m2m-catalog-YYYYMMDD.json`), servant de blueprint pour générer des fausses données en dev via une IA.
- **Ajouté** : module `hub/app/m2m_catalog_export.py` — `build_m2m_catalog_export(db)` (agrégation parallèle via `asyncio.gather`), `apply_heuristic(openapi_spec, slug)` (fallback de tagging pour les apps sans `/api/m2m/catalog`), fetch avec timeout 5s/app et `return_exceptions=True` (une app down ne casse pas l'export).
- **Sécurité** : le token SA minté par le Hub pour appeler chaque app est à usage unique (TTL 5 min), jamais sérialisé dans l'export. L'export ne contient aucun secret (métadonnée d'API uniquement).
- **Robustesse** : normalisation uniforme des `request_body`/`responses` quelle que soit la source (catalog vs heuristique OpenAPI) ; apps injoignables marquées `unreachable` sans bloquer le reste.

---

## [3.9.9] - 10 juillet 2026

### ⚡ Pool SQLAlchemy réduit et aligné sur le template

- **Corrigé** : `database.py` — pool_size réduit de 10→5, max_overflow de 20→10 (15 connexions max au lieu de 30).
- Élimine le risque `too many clients` PostgreSQL, aligne la config avec `HUBapp_TEMPLATE_base`.
---

## [3.9.8] - 7 juillet 2026

### 🩺 Correction vue santé des apps — NaNh + faux « Degraded »

- **Backend (apps_health.py)** : la sonde de santé marquait toute réponse HTTP
  ≥400 comme « degraded ». Les apps sans endpoint `/health` standard (ex:
  Vaultwarden) renvoyaient 404 → faussement « degraded » alors que le serveur
  répond en 9-16ms. Désormais : 5xx = degraded, 3xx/4xx = online (serveur
  joignable).
- **Frontend (apps-health.js)** : le timestamp `checked_at` est déjà envoyé
  au format ISO8601 avec `Z`. Le `+ 'Z'` côté JS créait une date invalide
  `"2025-07-07...ZZ"` → `NaNh` affiché pour toutes les apps.
- **Affichage** : quand une app est online sans endpoint santé, une note
  « Serveur joignable — pas d'endpoint /health standard » remplace les
  champs vides. Version et latence affichées seulement si disponibles.

---

## [3.9.7] - 7 juillet 2026

### 🔧 Quick wins — conventions + sécurité (B7/B8/B10)

- **TZ=UTC postgres** : le conteneur `db` n'avait pas `TZ: UTC` (convention
  obligatoire du parc). Ajouté dans l'environnement du service.
- **Wireguard auto-off** : le service `wireguard-client` démarrait avec
  `network_mode: host` + `NET_ADMIN` + `SYS_MODULE` même quand
  `WG_ENABLED=false`. Ajout d'un profil `wireguard` — démarrage uniquement
  via `docker compose --profile wireguard up`.

---

## [3.9.6] - 7 juillet 2026

### 🛠 Makefile — ajout du target `build` (Lot 2)

- **Makefile** : ajout du target `build` (`docker compose build`). Nécessaire
  pour le flux de mise à jour séquentiel de `hubctl update hub`
  (stop → pull → **build** → up → health). Sans ce target, `make build`
  échouait et déclenchait un rollback à chaque mise à jour du Hub.

---

## [3.9.5] - 7 juillet 2026

### 🔒 /me/regenerate-secret : token M2M obligatoire (H9)

- **`POST /api/apps/me/regenerate-secret`** acceptait l'authentification par IP
  Docker + header `X-Slug`/`X-Client-ID` sans token M2M. N'importe quel
  conteneur sur le réseau Docker pouvait régénérer le secret M2M d'une autre
  app → **lockout + usurpation d'identité M2M** (H9).
- **Fix** : suppression du fallback IP+header. Un token M2M valide
  (`Authorization: Bearer`) est désormais obligatoire pour cette opération
  destructive. L'endpoint est protégé par `verify_token_not_revoked` qui
  valide le token + son jti.

---

## [3.9.4] - 7 juillet 2026

### 🔒 Sécurité auth — injection install.sh + spoofing IP (Lot 3)

- **install.sh (C6)** : le code Python inline faisait `open('$PROFILE')` avec
  interpolation bash du chemin. Un nom de fichier contenant une apostrophe
  permettait l'injection de code Python arbitraire (RCE) au moment de
  l'install. Fix : lecture via `sys.stdin` + redirection `< "$PROFILE"`, le
  chemin n'est plus interpolé dans le code.
- **_get_client_ip / auth.py (H7 + H8)** : trustait `X-Forwarded-For` /
  `X-Real-IP` inconditionnellement. Comme l'API est publiée directement sur
  `:8000` (uvicorn sans `--proxy-headers`), un client externe pouvait spoof
  son IP pour :
  - **H7** : forger une IP fraîche à chaque tentative → bypass du rate-limit
    de login (5/min) → brute-force online.
  - **H8** : spoof une IP Docker (`172.17.0.x`) pour passer
    `is_docker_network_ip()` dans `/token/introspect` → court-circuiter le
    Bearer → introspection non authentifiée (fuite `sub`, `apps`, `jti`).
- **Fix** : n'honorer les headers de proxy que si l'IP TCP directe
  (`request.client.host`) est un proxy de confiance (`is_docker_network_ip` =
  réseau Docker = frontend nginx). Cohérent avec `apps.py` qui utilise déjà
  `request.client.host`. Compatibilité préservée pour les déploiements
  standards (accès direct :8000 ou via nginx — dans les deux cas le
  comportement est inchangé pour les clients légitimes).

## [3.9.3] - 7 juillet 2026

### ⚡ Correctif scheduler backups planifiés (C4)

- **scheduler backups** : `periodic_backup_scheduler` (`app/main.py`) comparait
  `app.next_backup_at` avec `now = utcnow()`. En production (PostgreSQL +
  asyncpg), `next_backup_at` est lu depuis une colonne `TIMESTAMPTZ` → asyncpg
  retourne une datetime **aware**, alors que `utcnow()` (`models.py`) retourne
  une datetime **naive** (`replace(tzinfo=None)`). Cette comparaison levait
  `TypeError: can't compare offset-naive and offset-aware datetimes` à chaque
  itération (toutes les 60 s), silencieusement attrapée par le `except Exception`
  du scheduler → **aucun backup planifié (cron) ne se déclenchait jamais**.
- **Fix** : normalisation de `next_backup_at` via le helper `_naive_utc`
  (`app/routes/app_backups.py`) qui existait déjà et documentait le piège —
  le scheduler était le seul à l'avoir oublié. Ajout de 4 tests de régression
  (`hub/tests/test_backup_scheduler.py`).
- **Racine non traitée** (Lot 5) : le modèle SQLAlchemy `Column(DateTime)` naïf
  vs migration `TIMESTAMPTZ`. Le fix `_naive_utc` est défensif et cohérent avec
  le reste de `app_backups.py`.

## [3.9.2] - 6 juillet 2026

### 🔒 Durcissement sécurité & robustesse (audit)

- **P1 — Reset token rejeté comme access token** : `verify_token()` rejette
  désormais tout token portant `scope="pwd_reset"`. Les reset tokens
  n'avaient ni `jti` ni `type` et pouvaient contourner la vérification de
  révocation (`verify_token_not_revoked`), servant de Bearer sur les
  endpoints protégés pendant `recovery_token_ttl_minutes`. Le flow légitime
  (`decode_reset_token`) n'est pas affecté.

- **P3 — CORS : plus d'injection de localhost** : la configuration CORS est
  extraite dans `build_cors_config()` (testable). En production avec
  uniquement `ALLOWED_ORIGIN_REGEX` (recommandé pour un LAN), `localhost`
  n'est plus injecté dans les origines autorisées. En mode dev (rien
  configuré), `localhost` reste autorisé mais sans credentials.

- **P3 — `except:` nus remplacés** : 3 `except:` nus dans
  `routes/auth.py` (logout, introspect) remplacés par `except Exception:`
  pour laisser `KeyboardInterrupt`/`SystemExit` se propager proprement.

---

## [3.9.1] - 5 juillet 2026

### 🔒 Sécurité — install & secrets (audit)

- **B1 — Chemin des clés JWT corrigé** : `install.sh` écrivait dans
  `secrets/jwt/` mais le code lit `secrets/jwt_private_key.pem` (top-level).
  Conséquence : les clés générées à l'install n'étaient jamais utilisées,
  et `AUTO_GENERATE_KEYS=true` était forcé en plein `APP_ENV=production`
  (régénération 2048 au lieu du 4096 de l'install). `install.sh` écrit
  désormais au bon chemin, avec migration automatique des anciennes clés
  `secrets/jwt/` → `secrets/`.
- **S2 — Mot de passe DB fail-fast** : retrait du fallback compromis
  `hub_secure_password_2025` du `docker-compose.yml` (`${POSTGRES_PASSWORD:?}`)
  et de `config.py`. En production, un mot de passe DB ou admin faible/par
  défaut déclenche désormais un `ConfigValidationError` bloquant au démarrage
  (échappatoire : `SKIP_PASSWORD_CHECK=true`). `hub_secure_password_2025`
  ajouté à `_WEAK_PASSWORDS`.
- **S3 — Permissions restrictives** : toutes les clés privées et secrets
  écrits en `0o600` (clé JWT auto-générée, clés backup, `server.key` TLS
  `pki.py`, `sa_*.env`, configs WireGuard). `entrypoint.sh` passe en
  `chmod -R u+rwX,go-rwx` sur `/code/secrets`. `install.sh` applique
  `chmod 600 .env` et sur les clés JWT.
- **S4 — Introspection RFC 7662** : `/api/auth/token/introspect` n'est plus
  accessible anonymement depuis l'extérieur. Les appels internes (réseaux
  whitelistés = apps du parc) restent permis ; un appel externe doit
  présenter un token Bearer valide, sinon 401.

### 🛡 Garde-fou install.sh

- **`.env` protégé en mode `--profile`** : le script refuse désormais
  d'écraser un fichier `.env` existant quand il est lancé sans
  confirmation interactive. Seul le flag `--force` permet de
  passer outre. Évite la destruction accidentelle d'une config live
  (ex. relancer `install.sh --profile` sur un Hub déjà configuré).
- **`--force`** : nouveau flag pour autoriser l'écrasement en mode
  non-interactif (ATTENTION : écrase `.env` et relance `docker compose up`).

- **m1** : l'URL affichée en fin d'install affichait toujours `https://`
  même avec HTTPS désactivé (expansion `${HTTPS_ENABLED:+s}` fautive).
- **m2** : en mode `--profile`, le CIDR LAN de fallback produisait
  `0.0.0/24` (SERVER_IP non encore défini) — désormais calculé dans le
  parseur Python depuis `server.ip`.
- **m5** : `start_period` du healthcheck `hub-api` réduit de 600s à 120s.

### 🧪 Tests

- `test_security_config.py` : les mots de passe faibles lèvent désormais en
  production (tests `_blocks_in_production`) et ne font plus que warn.
- `test_pki.py` : la clé serveur TLS est attendue en `0o600`.
- `test_api_auth.py` : couverture du blocage introspection externe (401).
- **m7** : `validate_security_config()` s'exécute désormais AVANT
  `create_default_admin()` au bootstrap (`main.py` lifespan).

### 🧹 Nettoyage

- **Cookie dead-code** : suppression des champs `cookie_secure`,
  `cookie_httponly`, `cookie_samesite`, `cookie_domain` (jamais utilisés —
  le Hub est pur JWT/Bearer, aucun `set_cookie`).
- **Cruft dev supprimé** : `index_Test.py`, `present.html`,
  `presentsales.html`, `hub/app_tester.py` (contenait `password="admin123"`
  en dur).
- Ajout de `hub/.dockerignore` (cache Python exclu de l'image).

### ⚠️ Action requise sur les déploiements existants

- Rejouer `./install.sh` (il génère/migre les clés JWT au bon chemin et
  repasse `AUTO_GENERATE_KEYS=false`), puis `docker compose up -d`.

---

## [3.9.0] - 5 juillet 2026

### ⚡ SSE — authentification par query param (?token=)
- Le endpoint `/api/events/stream` accepte désormais un fallback
  `?token=JWT` en query param pour les clients EventSource (navigateur),
  qui ne peuvent pas envoyer de headers HTTP personnalisés.
  Authentification prioritaire : Bearer header, puis `?token=`, puis beacon.
- Filtre `_SuppressLegacySSE401Filter` dans `main.py` : masque le bruit
  `GET /api/events/stream ... 401` dans les logs uvicorn (apps legacy
  qui n'utilisent pas encore le fallback).

### ⚡ Docker info — production durcie
- `get_app_docker_info` renseigne `docker_available: false` avec un
  message explicite quand le socket Docker n'est pas monté (production),
  au lieu de laisser le champ absent ou un timeout.
- UI frontend distingue visuellement "Docker désactivé" (info) vs
  "Conteneur non trouvé" (warning).

### 🧹 Cleanup
- `config.py` : retrait d'un `return` unreachable dans `ensure_backup_keys_exist()`.
- `TODO.md` : entrée backup scheduler marquée résolue (`periodic_backup_scheduler()`
  implémenté dans `main.py:184`).

### ⚡ Nettoyage Git — exclusion des plans d'implémentation

- Ajout de `docs/superpowers/plans/` au `.gitignore` (le pattern `.superpowers/` était déjà présent)
- `git rm --cached` des 8 fichiers de plans auto-générés par le workflow Superpowers
- Les plans restent sur le disque pour référence locale, mais ne sont plus commités

---

## [3.8.0] - 29 juin 2026

### ⚡ Import de configuration VPN par fichier .conf

- Nouvel endpoint `POST /api/vpn/import` : import d'un fichier de configuration
  WireGuard (.conf). Le backend parse, valide (clés base64, CIDR, port), vérifie
  la whitelist de directives (rejet des hooks shell `PostUp`/`PostDown`/`PreUp`/
  `PreDown` → 422 sécurité), écrit le `.conf` tel quel, active le VPN et
  journalise l'audit `VPN_CONFIG_IMPORTED`.
- Helpers `_extract_private_key()` et `_validate_import_conf()` avec whitelist
  `_ALLOWED_WG_DIRECTIVES` / `_FORBIDDEN_WG_DIRECTIVES`. La clé privée n'est
  jamais renvoyée par l'API (seul le hash SHA-256).
- 11 nouveaux tests (helpers + endpoint import).

### 💄 Refonte de la vue VPN WireGuard (thème Hub)

- Interface pensée pour un utilisateur non-informaticien : point focal = import
  du fichier `.conf` par glisser-déposer ou sélecteur de fichier. Résumé d'état
  affiché après import. Paramètres avancés repliés (collapse), pré-remplis après
  import, chaque terme technique expliqué par une infobulle Bootstrap
  (`data-bs-toggle="tooltip"`).
- Styles `vpn-*` alignés sur le thème Hub « Soft & Clean » (tokens `cpw-*`) :
  badge-icône bouclier, dropzone, inputs avancés, résumé vert.
- Méthode `api.importVpnConfig(config)` pour l'appel frontend.

---

## [3.7.2] - 28 juin 2026

### 🗑️ Centralisation du dev-interface (vers POULIDOR)
- Retrait de `opencode.json`, `.github/` (instructions / skills / prompts) et
  `AI_INDEX.md`. Le tooling de dev est centralisé dans le méta-dépôt POULIDOR ;
  l'instruction `hub-application-contract.instructions.md` y a été conservée.

## [3.7.1] - 23 juin 2026

### ⚡ Sauvegardes d'applications asynchrones avec progression temps réel

- Migration `024_add_backup_progress.sql` : nouveaux champs sur `app_backups`
  (`status`, `started_at`, `completed_at`, `duration_seconds`, `progress_step`,
  `progress_percent`, `error_message`) ; `storage_path` devient nullable pour
  permettre les enregistrements `pending`/`running`/`failed`/`timeout`. Index
  partiel `idx_app_backups_running` pour repérer rapidement les sauvegardes
  actives par application.
- `POST /api/apps/{slug}/backup/trigger` retourne désormais `202 Accepted`
  et lance la sauvegarde en arrière-plan via `asyncio.create_task`. Renvoie
  `409 Conflict` si une sauvegarde est déjà en cours pour l'application,
  empêchant tout double déclenchement (anti mis-clic).
- Nouvel endpoint `GET /api/admin/backups/in-progress` : liste les
  sauvegardes en cours avec progression (`progress_step`, `progress_percent`),
  durée écoulée et durée moyenne historique pour estimer une ETA.
- Téléchargement des backups distants protégé par un timeout de 120 s, retenté
  une fois avec timeout étendu à 300 s pour les apps lentes ; en cas de
  second timeout l'enregistrement passe en `status='timeout'` avec le vrai
  message d'erreur stocké dans `error_message` (au lieu d'un message générique).
- Diffusion SSE temps réel : événements `backup_started`, `backup_progress`,
  `backup_completed`, `backup_failed` consommés par le frontend pour mettre
  à jour la barre de progression et afficher des notifications enrichies
  (durée formatée, message d'erreur réel).
- Kill-switch au démarrage du Hub : `cleanup_stale_running_backups()`
  requalifie en `failed` toute sauvegarde restée bloquée en `pending`/`running`
  plus de 30 minutes (interruption Hub pendant un job).
- Frontend : nouvelle section "Sauvegardes en cours" sur l'onglet
  *App Backups* avec barre de progression Bootstrap animée, étape en cours,
  durée écoulée temps réel et ETA. Le bouton de déclenchement est remplacé
  par un badge "En cours…" tant que la sauvegarde n'est pas terminée. La
  liste de rétention affiche désormais la durée formatée (`Durée: 1m 23s`)
  sous l'auteur de chaque backup.

### ✨ Backups planifies coherents pour Vaultwarden

- Migration `023_add_backup_quiesce.sql` : nouveau champ
  `applications.backup_quiesce` (boolean, defaut `false`). Active par defaut
  sur l'app `vaultwarden`.
- Modele `Application.backup_quiesce` et schemas `AppBackupConfig` /
  `AppBackupConfigUpdate` exposent le champ. Endpoint
  `PUT /api/apps/{slug}/backup/config` permet de l'activer.
- `perform_app_backup` envoie `quiesce_vaultwarden=true` (query param) au
  POST `/api/admin/backup` de l'app cible quand `backup_quiesce` est actif.
  L'app stoppe alors son conteneur Vaultwarden pendant le snapshot puis le
  redemarre automatiquement.
- Doc `vaultRestorHUB.md` mise a jour : la procedure de restauration complete
  ne demande plus l'arret manuel du conteneur Vaultwarden quand l'app cible
  supporte le lifecycle automatique (Vaultwarden >= 0.0.2).

---

## [3.7.0] - 11 avril 2026

### 🚀 Rattrapage des évolutions 3.x

> Cette entrée consolide les changements introduits depuis la dernière version documentée dans ce fichier (`2.5.4`), jusqu'à l'état actuel du dépôt.

### 👥 Gestion des utilisateurs, profils et permissions

| Fonctionnalité | Description |
|----------------|-------------|
| **Multi-profils** | Passage à une relation N:N `user_profiles` avec conservation automatique du rôle le plus élevé entre plusieurs profils |
| **Synchronisation des rôles** | Le modal utilisateur passe aux checkboxes et resynchronise les rôles issus des profils |
| **Approbation d'apps synchronisée** | L'approbation d'une application met à jour les permissions utilisateur et force un rafraîchissement de token si nécessaire |
| **Import CSV utilisateurs** | Ajout d'un template, d'un aperçu/validation et d'une fermeture de modale fiable après import |
| **Rôles bulk en M2M** | Nouvel endpoint `/api/users/m2m/app-roles/bulk` pour exposer profils et rôles applicatifs observables |

### 🔔 Messagerie et temps réel

| Fonctionnalité | Description |
|----------------|-------------|
| **Messagerie in-app** | Nouveau module `/api/messages` pour envoi admin ciblé ou broadcast, consultation utilisateur, compteurs non lus, accusés de lecture, export CSV et purge |
| **Envoi applicatif M2M** | Les applications peuvent pousser des messages via `/api/messages/m2m/send` avec un service account |
| **Synchronisation NotifCenter** | Ajout des endpoints `/api/messages/m2m/sync`, `/api/messages/m2m/receipts`, `/api/messages/m2m/stats` et du détail de message pour une synchro incrémentale |
| **Traçabilité des renvois** | Ajout de `target_username`, `is_resend` et `original_message_id` pour le filtrage et l'historique |
| **Panneau de notifications** | Nouveau panneau flottant côté frontend avec cartes de messages utilisateur |

### 🧩 Applications, découverte et intégration

| Fonctionnalité | Description |
|----------------|-------------|
| **Health checker et SSE** | Suivi d'état des applications avec notifications temps réel et ordre stable dans l'interface |
| **Sauvegardes d'applications** | Renforcement des backups/restores, import sélectif et contrôle des permissions de restauration |
| **Discovery enrichie** | Ajout d'une API Discovery pour explorer les endpoints publiés par les applications |
| **Spécifications OpenAPI** | Stockage, exposition et filtrage des endpoints applicatifs via `api_endpoints` |
| **Catégories d'applications** | Nouveau champ `category` pour filtrer et regrouper visuellement les apps |
| **Ouverture directe en HTTPS** | Support de `frontend_https_port` pour éviter la redirection HTTP → HTTPS et exposer le JWT en clair |
| **Ergonomie frontend** | Badge de version des apps, menu mobile responsive et tableaux de bord plus réactifs |

### 🔧 Technique

- Simplification de l'application des migrations avec points de sauvegarde et nettoyage du code obsolète
- Harmonisation des dates sur `utcnow()` pour une meilleure compatibilité Python 3.12+
- Ajout du document `exemple-app.md` pour cadrer l'intégration d'un centre de notifications externe

---

## [2.5.4] - 4 janvier 2026

### 🔄 Resync automatique des APIs partagées lors du `regenerate_secret`

**Amélioration majeure** : Les Service Accounts sont maintenant automatiquement resynchronisés avec les APIs partagées (`is_shared_service=True`) lors de l'appel à `POST /api/apps/me/regenerate-secret`.

| Fonctionnalité | Description |
|----------------|-------------|
| **Resync automatique** | Plus besoin de modifier les applications clientes |
| **Accès aux shared services** | Ajoutés automatiquement au SA lors du regenerate |
| **Fichier .env mis à jour** | Contient maintenant tous les slugs autorisés (app + shared) |

### 🔧 Modifications techniques

| Fichier | Modification |
|---------|-------------|
| `routes/apps.py` | `regenerate_secret` synchronise désormais les apps `is_shared_service=True` |
| `INTEGRATION.md` | Documentation mise à jour pour refléter le comportement automatique |

### ✅ Avantages

- **Zéro modification** des applications clientes existantes
- Le simple redémarrage d'une app resynchronise ses accès M2M
- Plus besoin d'appeler manuellement `update_shared()` dans `hub_client.py`
- Les nouveaux services partagés sont automatiquement accessibles après un redémarrage

---

## [2.5.3] - 3 janvier 2026

### 🆕 Endpoint de découverte M2M

**Nouvelle fonctionnalité** : Les applications peuvent maintenant découvrir dynamiquement les services partagés disponibles via un nouvel endpoint dédié.

| Fonctionnalité | Description |
|----------------|-------------|
| **GET /api/apps/discover** | Endpoint de découverte des APIs partagées (`is_shared_service=True`) |
| **Authentification M2M** | Accessible uniquement avec un token Service Account |
| **Réponse structurée** | Liste des services avec slug, nom, description et URL |

### 🔧 Ajouts techniques

| Élément | Description |
|---------|-------------|
| `AppDiscoveryItem` (schemas.py) | Nouveau schéma pour les infos de découverte d'une app |
| `DiscoverResponse` (schemas.py) | Schéma de réponse avec liste `shared_services` + total |
| `/api/apps/discover` (routes/apps.py) | Implémentation de l'endpoint avec vérification token M2M |

### 📝 Documentation mise à jour

| Section | Modification |
|---------|-------------|
| Version | 2.5.2 → 2.5.3 |
| Section 6 (M2M) | Ajout note vers la découverte des services partagés |
| Section 7 (Services Partagés) | Nouvelle sous-section "Découverte dynamique des services partagés" avec exemples |
| Section 10 (Endpoints) | Ajout table "Découverte M2M" avec le nouvel endpoint |

### 📋 Cas d'usage

L'endpoint permet aux applications de :
- Découvrir les APIs partagées sans configuration manuelle
- Récupérer dynamiquement les URLs des services
- S'adapter automatiquement lors de l'ajout de nouveaux services partagés

**Exemple de réponse :**
```json
{
  "shared_services": [
    {
      "slug": "salarie",
      "name": "API Salariés",
      "description": "Données RH des employés",
      "api_url": "http://192.0.2.11:18087",
      "is_shared_service": true
    }
  ],
  "total_shared": 1
}
```

---

## [2.5.2] - 3 janvier 2026

### 🔄 Resync automatique M2M et robustification

**Nouvelles fonctionnalités** :

| Fonctionnalité | Description |
|----------------|-------------|
| **Resync automatique M2M** | Détection et récupération automatique en cas de secret invalide (401) |
| **Thread-safety** | Protection contre les régénérations concurrentes avec `asyncio.Lock()` |
| **Protection anti-boucle** | Max 3 tentatives + cooldown 30s entre resyncs |
| **Logs verbeux** | Traçabilité complète du processus de resync |

### 🔧 Corrections et améliorations

| Élément | Modification |
|---------|-------------|
| `TokenPayload.is_service_account()` | Détection robustifiée (accepte `type`, `token_type`, rôles) |
| Lecture champ JWT | Accepte `type` ET `token_type` par rétrocompatibilité |
| `docker-compose.yml` | Ajout variable `AUTO_GENERATE_KEYS` + secrets en lecture-écriture |

### 📝 Documentation mise à jour

| Section | Modification |
|---------|-------------|
| Version | 2.5.1 → 2.5.2 |
| Section 3 (hub_client.py) | Ajout constantes `MAX_RESYNC_ATTEMPTS`, `RESYNC_COOLDOWN` |
| Section 3 (hub_client.py) | Nouvelle méthode `_auto_resync()` avec protection |
| Section 3 (hub_client.py) | `get_token()` avec retry automatique après resync |
| Section 11 | Nouvelle sous-section "Resync Automatique M2M (v2.5.2)" |
| Table des matières | Ajout lien vers resync automatique |

### 📋 Architecture de résilience M2M

```
get_token() → Cache valide? → Oui → Retourne token
                    ↓ Non
            POST /token → 200 OK → Retourne token
                    ↓ 401
            _auto_resync() → Lock + Cooldown check
                    ↓
            regenerate-secret → Retry get_token()
```

### 🐳 Docker

- `AUTO_GENERATE_KEYS` maintenant passé au conteneur
- Volume `secrets` en `:rw` (était `:ro`) pour permettre la génération des clés

---

## [2.5.1] - 3 janvier 2026

### 🐛 Correction documentation - Format du champ `apps`

**Problème identifié** : Plusieurs applications ont rencontré des erreurs d'intégration car la documentation indiquait que le champ `apps` du token JWT était toujours un dictionnaire, alors qu'il peut aussi être une liste.

**Corrections apportées** :

| Section | Modification |
|---------|-------------|
| Structure du token JWT | Ajout d'un avertissement et documentation des deux formats possibles (dict et liste) |
| `TokenPayload` (hub_auth.py) | Mise à jour du code pour normaliser `apps` en dictionnaire, gérant le cas liste |
| JavaScript `getAppRole()` | Ajout de la gestion du cas où `apps` est une liste |
| JavaScript | Ajout d'une nouvelle méthode `hasAppAccess()` compatible dict/liste |

**Formats du champ `apps`** :
- **Dictionnaire** : `{"salarie": "admin", "compta": "viewer"}` (tokens utilisateur)
- **Liste** : `["salarie", "exemple-app"]` (certains tokens M2M/service account)

**Code de normalisation recommandé** (Python) :
```python
raw_apps = payload.get("apps", {})
if isinstance(raw_apps, list):
    apps = {app: "service" for app in raw_apps}
else:
    apps = raw_apps
```

---

## [2.5.0] - 2 janvier 2026

### 📝 Nouvelle section documentation

#### Section 11 - Troubleshooting M2M (Erreurs courantes)

Ajout d'une section complète de dépannage pour les erreurs M2M :

| Sous-section | Contenu |
|--------------|--------|
| Erreur 401 - Not Authenticated | Diagnostic token absent/expiré/invalide |
| Erreur 403 - Forbidden | **Cause principale** : Service Account sans accès à l'API cible |
| Erreur 403 - IP non autorisée | Auto-enregistrement hors réseau Docker |
| Tableau récapitulatif | Vue synthétique de toutes les erreurs M2M |

### 🔧 Solutions documentées pour erreur 403 M2M

1. **Option A** : Marquer l'API cible comme `is_shared_service = TRUE`
   - Via SQL : `UPDATE applications SET is_shared_service = TRUE WHERE slug = 'salarie';`
   - Via API : `PATCH /api/apps/{app_id}` avec `{"is_shared_service": true}`

2. **Option B** : Ajouter manuellement l'accès au Service Account
   - Via API : `PUT /api/service-accounts/{sa_id}/apps` avec liste des IDs d'apps

3. **Option C** : Régénérer le Service Account après activation du shared service

### 📋 Modifications structure

| Section | Modification |
|---------|-------------|
| Version | 2.4.0 → 2.5.0 |
| Table des matières | Ajout section 11 Troubleshooting M2M |
| Numérotation | Sections 11-13 → 12-14 (décalage) |
| Section 11 (NEW) | Troubleshooting M2M complet avec solutions |

### 📋 Checklist pour agents IA

- [ ] En cas d'erreur 403 M2M, vérifier le champ `apps` dans le token JWT décodé
- [ ] Si l'API cible n'est pas dans `apps`, activer `is_shared_service` ou ajouter l'accès manuellement
- [ ] Les Service Accounts créés AVANT l'activation de `is_shared_service` ne sont pas mis à jour automatiquement

---

## [2.4.0] - 2 janvier 2026

### ⚠️ BREAKING CHANGE

#### Retrait du header X-Client-ID et du mode rétrocompatible
- **Suppression** du support du header `X-Client-ID` dans les endpoints `/api/apps/me/*`
- **Suppression** du mode rétrocompatible avec credentials statiques (`CLIENT_ID`/`CLIENT_SECRET` dans `.env`)
- Utilisation **exclusive** du header `X-Slug` pour l'identification des applications
- Le `HubClient` n'utilise plus de credentials depuis `.env`, uniquement l'auto-enregistrement

### 📝 Modifications documentation

| Section | Modification |
|---------|--------------|
| Version | 2.3.0 → 2.4.0 |
| Section 2 | Suppression des variables CLIENT_ID/CLIENT_SECRET du .env |
| Section 3 | Suppression de "Mode rétrocompatible (credentials statiques)" |
| Section 3 | Mise à jour HubClient pour utiliser X-Slug |
| Section 10 | Suppression références X-Client-ID, utilisation X-Slug uniquement |
| Table des matières | Suppression lien vers "Mode rétrocompatible" |

### 📋 Migration requise

Les applications doivent :
1. **Retirer** `CLIENT_ID` et `CLIENT_SECRET` de leur `.env`
2. **Utiliser** exclusivement `APP_SLUG` pour l'identification
3. **Mettre à jour** leur `hub_client.py` avec la nouvelle version
4. L'auto-enregistrement via `X-Slug` devient le seul mode supporté

### 📋 Checklist pour agents IA

- [ ] Supprimer toute utilisation de `X-Client-ID` header
- [ ] Remplacer par `X-Slug: <app_slug>` dans tous les appels
- [ ] Supprimer `CLIENT_ID` et `CLIENT_SECRET` des fichiers `.env`
- [ ] Mettre à jour le `HubClient` avec la nouvelle version de la doc

---

## [2.3.0] - 2 janvier 2026

### ✨ Nouvelles fonctionnalités

#### Support du header X-Slug
- Les endpoints `/api/apps/me/credentials` et `/api/apps/me/regenerate-secret` acceptent désormais `X-Slug` en plus de `X-Client-ID`
- Permet aux applications de s'identifier par leur slug au lieu de leur client_id
- Plus simple pour les applications qui ne connaissent pas encore leur client_id

### 📝 Modifications documentation

| Section | Modification |
|---------|--------------|
| Version | 2.2.0 → 2.3.0 |
| Section 10 | Ajout sous-section "Headers d'identification" |

### 🔧 Modifications code

| Fichier | Modification |
|---------|--------------|
| `hub/app/routes/apps.py` | Support X-Slug dans get_app_credentials |
| `hub/app/routes/apps.py` | Support X-Slug dans regenerate_app_secret |

### 📋 Checklist pour agents IA

- [ ] Possibilité d'utiliser `X-Slug: mon-app` au lieu de `X-Client-ID`
- [ ] Utile pour récupérer/régénérer credentials quand client_id inconnu

---

## [2.2.0] - 2 janvier 2026

### ✨ Nouvelles fonctionnalités

#### Système de Changelog pour agents IA
- **Nouveau fichier** : `CHANGELOG.md` - Historique détaillé des modifications
- **Nouveau endpoint** : `GET /api/docs/changelog` - Consulter le changelog
- **Nouveau endpoint** : `GET /api/docs/changelog/versions` - Liste des versions
- **Nouveau endpoint** : `GET /api/docs/changelog/diff` - Comparer deux versions
- Support du format JSON pour parsing automatique

### 📝 Modifications documentation

| Section | Modification |
|---------|--------------|
| Version | 2.1.0 → 2.2.0 |
| Section 10 | Ajout sous-section "Documentation et Changelog" |
| Table des matières | Ajout lien vers Documentation et Changelog |

### 🔧 Modifications code

| Fichier | Modification |
|---------|--------------|
| `hub/app/routes/docs.py` | Ajout endpoints changelog (4 nouveaux) |
| `hub/app/routes/docs.py` | Fonctions parse_changelog_versions, extract_version_changes |

### 📋 Checklist pour agents IA

- [ ] Stocker la version actuelle pour comparaisons futures
- [ ] Utiliser `/api/docs/changelog/diff?from_version=X.Y.Z` pour voir les changements
- [ ] Vérifier `action_required` dans la réponse du diff
- [ ] Traiter les items de `checklist` dans la réponse

---

## [2.1.0] - 2 janvier 2026

### ✨ Nouvelles fonctionnalités

#### Services Partagés M2M
- **Nouvelle section 7** : "Services Partagés (APIs de données)"
- Les applications peuvent accéder automatiquement aux APIs partagées (ex: API Salariés)
- Nouveau champ `is_shared_service` sur le modèle `Application`
- Attribution automatique des droits aux APIs partagées lors de l'auto-enregistrement

#### Documentation dynamique
- **Nouveau endpoint** : `GET /api/docs/integration/version` - Retourne la version actuelle
- **Nouveau endpoint** : `GET /api/docs/integration/sections` - Liste JSON des sections
- **Nouveau endpoint** : `GET /api/docs/changelog` - Historique des modifications
- Les sections sont maintenant parsées dynamiquement depuis `INTEGRATION.md`

### 📝 Modifications documentation

| Section | Modification |
|---------|--------------|
| Table des matières | Ajout section 7, renumérotation 8-13 |
| Section 7 (nouvelle) | Services Partagés - concept, configuration, usage |
| Section 12 (Résumé) | Ajout checklist "APIs partagées accessibles automatiquement" |

### 🔧 Modifications code

| Fichier | Modification |
|---------|--------------|
| `hub/app/models.py` | Champ `is_shared_service` déjà présent (ligne 104) |
| `hub/app/routes/apps.py` | Auto-ajout apps partagées lors création SA |
| `hub/app/routes/service_accounts.py` | Auto-ajout apps partagées lors création SA |
| `hub/app/routes/docs.py` | Refonte complète - parsing dynamique |

### 🗄️ Modifications base de données

```sql
-- Nouveau champ (déjà migré)
ALTER TABLE applications ADD COLUMN is_shared_service BOOLEAN DEFAULT FALSE;

-- Marquer API Salariés comme partagée
UPDATE applications SET is_shared_service = TRUE WHERE slug = 'salarie';
```

### 📋 Checklist pour agents IA

- [ ] Mettre à jour la numérotation des sections (1-13 au lieu de 1-11)
- [ ] Ajouter la gestion de `is_shared_service` si manipulation d'apps
- [ ] Utiliser `/api/docs/integration/version` pour vérifier la version
- [ ] Les nouvelles apps ont automatiquement accès aux APIs partagées

---

## [2.0.0] - 1 janvier 2026

### ✨ Nouvelles fonctionnalités

#### Auto-enregistrement dynamique
- **Nouvelle section 3** : "Auto-enregistrement et récupération dynamique des credentials"
- Les applications peuvent s'enregistrer automatiquement auprès du Hub
- Credentials stockés en RAM (pas dans .env)
- Régénération du secret via `POST /api/apps/me/regenerate-secret`

#### Endpoints auto-enregistrement
- `POST /api/apps/register` - Auto-enregistrement (IP Docker uniquement)
- `GET /api/apps/me/credentials` - Récupérer client_id
- `POST /api/apps/me/regenerate-secret` - Régénérer le secret

### 📝 Modifications documentation

| Section | Modification |
|---------|--------------|
| Section 2 | `.env` simplifié - CLIENT_ID/SECRET optionnels |
| Section 3 (nouvelle) | Auto-enregistrement complet |
| Section 9 | Endpoints auto-enregistrement ajoutés |

### 🔧 Modifications code

| Fichier | Modification |
|---------|--------------|
| `hub/app/routes/apps.py` | Endpoints register, credentials, regenerate-secret |
| `hub/app/schemas.py` | `AppRegisterRequest`, `AppRegisterResponse` |

### 🔒 Sécurité

- Whitelist IP Docker pour auto-enregistrement
- Plages autorisées : `172.17-31.x.x`, `10.0.x.x`, `192.168.x.x`

---

## [1.0.0] - 15 décembre 2025

### 🎉 Version initiale

- Authentification centralisée JWT (RS256)
- Gestion des utilisateurs et rôles
- Service Accounts M2M
- Intégration Frontend/Backend
- Communication inter-API

---

## Format du changelog

Chaque entrée contient :

```markdown
## [VERSION] - DATE

### ✨ Nouvelles fonctionnalités
### 📝 Modifications documentation  
### 🔧 Modifications code
### 🗄️ Modifications base de données
### 🔒 Sécurité
### 🐛 Corrections
### ⚠️ Breaking changes
### 📋 Checklist pour agents IA
```

---

## Comment utiliser ce changelog

### Pour les agents IA

```bash
# Voir tout le changelog
curl http://hub:8000/api/docs/changelog

# Voir les changements depuis une version
curl "http://hub:8000/api/docs/changelog?since=2.0.0"

# Voir uniquement la dernière version
curl "http://hub:8000/api/docs/changelog?latest=true"

# Format JSON structuré
curl "http://hub:8000/api/docs/changelog?format=json"
```

### Comparaison de versions

```bash
# Quelle version ai-je ?
curl http://hub:8000/api/docs/integration/version

# Quels changements depuis ma version ?
curl "http://hub:8000/api/docs/changelog?since=2.0.0"
```
