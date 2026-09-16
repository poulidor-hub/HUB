# Changelog - Hub d'Authentification

> Ce fichier documente toutes les modifications apportées à la documentation et à l'API.
> Les agents IA peuvent utiliser `/api/docs/changelog` pour consulter cet historique.

---

## [3.59.3] - 17 septembre 2026

### ⚡ Fix critique — écran de chargement infini du portail (api.js invalide)

| Correctif | Description |
|-----------|-------------|
| **Accolade fermante `class HubAPI` restaurée** | Le retrait des méthodes Droits Philia (3.58.0, commit f25628b) avait supprimé par erreur l'accolade de fermeture de la classe en fin de `frontend/public/js/api.js` : SyntaxError → le navigateur rejetait tout le fichier → `window.api` jamais défini |
| **Symptôme** | Portail bloqué sur l'écran de chargement (logo + spinner) : `init.js` crashait en `ReferenceError: api is not defined` juste après le health-check du badge (`/api/health` répondait, puis plus aucune requête) → ni login ni dashboard, sans erreur backend ni appel réseau visible |
| **Périmètre touché** | Toutes les installs HUB_base ≥ 3.58.0 (main depuis le 15/09), fresh installs incluses ; aucune donnée affectée — bug purement frontend |
| **Test anti-régression** | `hub/tests/test_frontend_js_syntax.py` : `node --check` sur chacun des 40 `.js` du frontend (échouait avant fix, 40/40 après) ; skip propre si Node.js absent ; aurait attrapé la régression à la source |
| **Validation** | Reproduction headless Chromium des 2 erreurs console d'origine (`SyntaxError api.js:1877`, `ReferenceError init.js:209`) ; après fix : loadingScreen retiré du DOM, login affiché, zéro erreur console |

### ⚡ Fix — « Impossible de charger les utilisateurs » (500 sur GET /api/users)

| Correctif | Description |
|-----------|-------------|
| **Cause** | Un user robot créé le 13/09 avec l'email `exports-robot@hub.local` (domaine spécial RFC 6761, rejeté par email-validator ≥ 2.x) faisait échouer TOUTE la liste : `UserResponse.email` est un `EmailStr` qui revalide chaque lecture → ValidationError → 500. Présent depuis le 13/09, indépendant du fix frontend |
| **Fix** | Lecture tolérante : `email: Optional[str]` sur les schémas de réponse (`UserResponse`, `M2MUserSearchItem`, `AccountRequestNotificationAdmin`) — une donnée existante ne doit jamais casser sa lecture. La validation stricte reste à l'écriture (`UserCreate`/`UserUpdate`/`BulkUserCreate`/`AccountRequestCreate` gardent `EmailStr`) |
| **Test** | `test_list_users_tolerates_legacy_invalid_email` : user avec email `@hub.local` en base → `GET /api/users/` 200 (échouait avant fix) |

### ⚡ Perf — ~250 ms gagnés sur chaque requête authentifiée

| Correctif | Description |
|-----------|-------------|
| **Cause** | `jwt_keyring.select_verification_key()` relisait le PEM privé et re-chargeait la clé RSA (`load_pem_private_key` ≈ 250 ms sur RSA-4096 dans le container) À CHAQUE vérification de token, donc sur chaque requête authentifiée (`verify_token`, signature OIDC, JWKS…) — d'où la perte de réactivité générale du portail |
| **Fix** | Cache process `{kid: PEM public}` (`_public_pem_for_kid`) : sûr car `kid` = thumbprint du PEM public (invariant du module, PEM public immuable par kid) ; purge par `reload()` (rotation, mutation externe, tests). Appliqué à `select_verification_key`, `get_public_pem` et `build_jwks` |
| **Test** | `test_select_verification_key_caches_public_pem` : 2 vérifications successives → 1 seule lecture de PEM (échouait avant fix) ; `test_reload_purges_public_pem_cache` : déterminisme après purge |

---

## [3.59.2] - 17 septembre 2026

### 📖 Documentation — README du dépôt

| Fonctionnalité | Description |
|----------------|-------------|
| **README créé** | Le dépôt n'avait aucun README à la racine : rôle du Hub, fonctionnalités (auth JWT/JWKS, OIDC, M2M, messagerie SSE, backups/snapshots, VPN WireGuard, PKI), place dans le parc (diagramme Mermaid), démarrage (`install.sh`, cibles make), tests, table des 9 documents de référence |
| **Stack documentée à la réalité** | Frontend vanilla JS (Bootstrap 5, ECharts, sans build) — pas React ; JWKS public sur `/api/auth/.well-known/jwks.json` |
| **Version synchronisée** | `INTEGRATION.md` et `frontend/index.html` passés à v3.59.2 par `sync_version.py` |

---

## [3.59.1] - 16 septembre 2026

### 📖 Cahier des charges — réponses aux points d'audit (clés, SPOF, quiesce)

| Fonctionnalité | Description |
|----------------|-------------|
| Version documentée | v3.58.0 → **v3.59.0** (état de l'art) |
| Rotation des clés | §5 : trousseau multi-kid documenté (rotation sans rupture, overlap 24 h, révocation d'urgence) ; §9.3 : les apps récupèrent les clés dynamiquement via JWKS (sélection par `kid`, cache local) — aucun redémarrage requis |
| SPOF / reprise | §3.3 : hôte unique assumé, pas de failover — stratégie de reprise explicitée (snapshots autonomes + bare-metal semi-automatique, clés hors ligne, RTO outillé mais non garanti) ; §7.6 : restauration bare-metal |
| Quiesce / volumes | §7.3 : délégation clarifiée — l'app construit son archive (export logique) et gère elle-même son arrêt/relance ; le HUB n'accède jamais aux volumes |
| Divers | Sauvegardes §5 : paire de clés dédiée + chaîne de repli ; glossaire `kid`/`Retiring` ; chiffres à jour (tests ≈1050, 107 fichiers) ; HTML autonome reconstruit |

---

## [3.59.0] - 16 septembre 2026

### 🔐 Rotation multi-kid du JWKS + découplage du chiffrement des snapshots

- **Ajouté** : trousseau de clés JWT (`secrets/jwt_keys/`, PEM + manifest) — vue
  (`GET /api/auth/keys`), rotation sans rupture (`POST /api/auth/keys/rotate`),
  révocation d'urgence (`POST /api/auth/keys/revoke`, 409 sur clé active),
  nettoyage opt-in (`POST /api/auth/keys/cleanup`). Kid dérivé par thumbprint
  RFC 7638 ; la clé existante conserve le kid `hub-jwt-key-1`.
- **Ajouté** : `hubctl jwt keys|rotate|revoke|cleanup`.
- **Modifié** : les snapshots sont chiffrés avec la paire `backup_*.pem`
  (découplage de la clé de signature). Déchiffrement à chaîne de repli
  (backup → trousseau → JWT legacy) : les snapshots antérieurs restent lisibles.
  ⚠️ Forward-only : un snapshot créé avec ≥ 3.59.0 exige ≥ 3.59.0 pour être restauré.
- **Modifié** : JWKS publie les clés active + retiring (overlap 24 h, variable
  `JWT_KEY_OVERLAP_HOURS`) ; vérification kid-aware (tokens d'accès, reset,
  cookie session OIDC, access_token OIDC).
- **Sécurité** : génération à chaud des clés backup en production → log CRITICAL
  (snapshots antérieurs potentiellement irrécupérables).
- Apps : AUCUN changement requis (JWKS multi-clés supporté nativement par
  PyJWKClient et les clients OIDC).

## [3.58.2] - 16 septembre 2026

### 🐛 Flake minuit UTC (tests usage) + hygiène suite de tests

- **Corrigé** : `test_usage_user_day` et `test_usage_user_excludes_workshop_segments`
  échouaient si le run tombait dans les ~10 min avant minuit UTC — le clamp
  `ended = min(raw_end, day_end)` (routes/audit.py) tronquait le segment de 10 min
  traversant minuit (600 s → 330 s). `utcnow` est désormais **gelé à 12:00 UTC** dans
  ces tests (monkeypatch sur les 3 bindings : models, usage_tracking, route).
  Reproduction RED prouvée (gel à 23:54:30 → 2 failed, `assert 330 == 600`), suite verte.
- `.gitignore` : artefacts de couverture (`.coverage`, `htmlcov/`) générés par `make test-cov`.
- Makefile : variable `COMPOSE_TEST` — la chaîne compose de la stack de test n'est plus
  dupliquée 5× dans `test-docker` (expansion vérifiée identique via `make -n`).
- `docker-compose.test.yml` : header à jour (plus de mention `.env.test` / ports décalés —
  la stack ne publie plus aucun port hôte depuis 3.57.0).

---

## [3.58.1] - 15 septembre 2026

### 📖 Cahier des charges mis à jour à l'état de l'art v3.58.0

| Fonctionnalité | Description |
|----------------|-------------|
| Version documentée | `CAHIER_DES_CHARGES.md` passé de v3.37.0 à **v3.58.0** (état de l'art) — delta 3.38→3.57 intégré |
| Nouveaux contenus | Self-service OIDC M2M (§9.4), socle-check / pilotage atelier / moniteur jeton GitHub (§8.2), détection de boucles de requêtes (§8.3), changement d'IP + propagation (§7.6), identité stable + renommage + accès atelier (§7.1), backups chiffrés par mot de passe (§7.3), dépôt Git configurable + pipeline aligné voie manuelle (§7.7) |
| §9.8 Atelier réécrit | Cycle complet depuis la vue Santé, branches → atelier (prod verrouillée sur main), accès par utilisateur, cloisonnement M2M renforcé |
| Aligné 3.58.0 | Retrait de la fonction « Droits Philia » du portail (3.58.0) reflété : plus de mention « droits d'applications relais » (23 modules) |
| Chiffres clés | Tests ≈726→≈1020 (103 fichiers), tables ≈34→36, endpoints ≈250→≈270, domaines 22→23 |
| Corrections au passage | Heartbeat SSE 30 s → 15 s (3.34.1) ; livrable HTML autonome reconstruit |

---

## [3.58.0] - 15 septembre 2026

### ⚡ Retrait de la fonction « Droits Philia » du portail

Fonction métier (gestion des droits PhiliaNG) déplacée exclusivement dans
l'app exemple-app (page `/users-rights`). Le HUB redevient un portail neutre.

- **Frontend** : retrait du bouton nav, du tab pane `#tabPhiliaRights`, de la
  modale de confirmation et du script `admin-philia-rights.js` (fichier
  supprimé) ; retrait de `#tabPhiliaRights` de `ADMIN_SUBTAB_TARGETS`
  (`init.js`) et des 3 méthodes API (`api.js`).
- **Backend** : suppression du routeur proxy `app/routes/philia_rights.py`
  (`/api/philia-rights/*` — ne servait que cette UI) et de son câblage
  (`main.py`, `routes/__init__.py`) ; suppression de
  `tests/test_philia_rights_proxy.py` ; commentaire de `test_socle_check.py`
  nettoyé.
- **Inchangé** : infra SNI/certs `service.example.org` (`pki.py`, confs nginx),
  API métier de l'app (`/api/admin/philia-rights/*` côté exemple-app).
- **Validation** : 1099 tests PASS ; rebuild hub-api + hub-frontend ; vérifié
  live : tab absent du HTML servi, 404 sur le module JS et sur
  `/api/philia-rights/users`, onglets admin restants intacts.

---

## [3.57.1] - 15 septembre 2026

### ⚡ Self-client OIDC : SAs multi-apps (app_slug validé)

- Constat parc : les service accounts ont couramment un périmètre
  multi-applications (19 apps pour certains) — la résolution mono-app de
  3.55.0 répondait 409 sur ces SA, bloquant le self-client.
- `POST|GET /api/oidc/self-client` acceptent désormais `app_slug` (body /
  query) : validé **contre le périmètre du SA** (403 si hors périmètre) ;
  SA mono-app : comportement inchangé (aucun app_slug requis) ; multi-apps
  sans app_slug → 409 explicite.
- `get_current_service_account` retourne (sa, apps) — la sélection de l'app
  appartient à la route (helper `_resolve_self_client_app`).
- 12 tests `test_oidc_self_client.py` (multi-apps + app_slug, hors
  périmètre 403) ; suite complète 1107 passed / 0 failed.

---

## [3.57.0] - 15 septembre 2026

### ⚡ Tests parallélisés (pytest-xdist) + cibles make test

- **Ajouté** : dépendance `pytest-xdist>=3.6` (hub/requirements.txt, installée dans l'image hub-api).
- **Ajouté** : cibles Makefile — `make test` (parallèle `-n auto`, variable `XDIST_WORKERS`), `make test-seq` (séquentiel, debug flaky), `make test-cov` (couverture multi-workers, htmlcov/), `make test-docker` (stack isolée `-p pouly-test`).
- Perf : suite complète 1105 tests en ~88s en parallèle (×6 vs 534s séquentiel), comptes identiques seq/par vérifiés.
- `make test-docker` : tests copiés dans le conteneur via `compose cp` (image runtime sans tests, choix historique conservé), pytest restreint à `tests/`, workers bornés à 4 (`XDIST_WORKERS_DOCKER`, mem_limit 2g), purge `/code/tests` avant copie (anti-nesting au 2e run).
- **Corrigé** : stack de test sans publication de ports hôtes (`ports: !override []`) — compatible prod active (fin de collision 8000/80/443).

---

## [3.56.1] - 15 septembre 2026

### ⚡ Validation prod du pipeline 3.56.0 — restaurations base pour le test

Validation de bout en bout du nouveau pipeline de déploiement sur la prod du
serveur de dev (déclenché via `POST /api/apps/exemple-app/deploy`, id=3 :
clone/pull --ff-only → .env → make build/up → health → success, ~2 min,
app re-mise en ligne sur ses ports d'origine 9002/4204-4205, conteneurs
healthy). Aucun code impacté — entrée opérationnelle.

Restaurations faites en base / sur le dépôt pour rendre le test possible
(l'app exemple-app existait mais son enregistrement était incomplet) :

- `applications.git_repo_url` pour `exemple-app` : `NULL` →
  `https://github.com/Poutchouli/exemple-app.git` (sans lui, le endpoint
  `/deploy` répond 400).
- Ligne `port_allocations` recréée pour `exemple-app` @ 192.0.2.10 :
  (9002, 4204, 4205, subnet `192.168.80.0/20`) — valeurs relevées sur l'état
  courant (registre + conteneurs + réseau Docker), exactement ce que
  l'allocateur idempotent aurait produit. Sans elle, l'allocation aurait
  attribué de NOUVEAUX ports (9002/4204 étant vus occupés) → l'app aurait
  déménagé et les URLs du registre seraient devenues mortes.
- Remote git local de l'app basculé SSH → HTTPS
  (`git@github.com:Poutchouli/exemple-app.git` →
  `https://github.com/Poutchouli/exemple-app.git`) : le conteneur hub-api n'a
  ni client SSH ni clé ; l'auth de déploiement du hub = HTTPS + PAT
  (`http.extraheader`, cf. `deploy_service._git_auth_args`).

Comportement observés conformes à la 3.56.0 : `.env` préservé (secrets non
régénérés, `POSTGRES_PASSWORD` inchangé — app healthy sur sa base, chmod 0600,
normalisation seule du rewrite), `git pull --ff-only` (fast-forward visible
dans les logs), build/up sous env allowlistée.

---

## [3.56.0] - 14 septembre 2026

### ⚡ Déploiement UI des apps aligné sur la voie manuelle

Le déploiement déclenché depuis l'admin (POST `/api/apps/{slug}/deploy`) suit
désormais strictement la voie manuelle de référence (`make sync-env` → `make
build` → `make up` transitivement via le Makefile de l'app) :

- **Plus de fuite d'env du hub** : `make build`/`make up` lancés avec l'env
  allowlistée (`compose_base_env()`, parité atelier/recreate). Auparavant, l'env
  complet du conteneur hub-api était hérité par le make/compose de l'app → les
  `${VAR}` homonymes du compose d'app pouvaient être écrasés (ex.
  `HUB_HTTPS_ENABLED`, cf. bug atelier SSL WRONG_VERSION_NUMBER).
- **Parité `hubctl _write_app_env`** sur le `.env` écrit par le hub :
  `APP_SUBNET` écrit dès le déploiement (si alloué) — sans lui `make build`
  échoue sur `${APP_SUBNET:?}` avant le sync-env de l'app ;
  `SECRETS_ENCRYPTION_KEY` générée à la création est désormais réellement
  persistée (elle était générée puis perdue si absente du `.env.example`) ;
  `.env` restreint à `0600` à chaque déploiement (secrets en clair).
- **Échec explicite sans Makefile** : une app sans Makefile (non conforme au
  template) fait échouer le déploiement avec un message clair — suppression du
  fallback `docker compose` nu (sans sync-env, sans overlay HTTPS, `APP_SUBNET`
  manquant : voué à l'échec silencieux ou au compose cassé).
- **`git pull --ff-only`** (parité action générée `UPDATE`) : plus de merge
  surprise sur un historique divergent.
- Allowlist compose extraite dans `hub/app/compose_env.py` (module partagé
  deploy/workshop, sans cycle d'import ; alias privés conservés dans
  `workshop_service`).
- Tests : 7 nouveaux cas (`test_app_deploy.py`, cycle TDD rouge→vert) ;
  suite complète 1105 passed / 0 failed.

---

## [3.55.0] - 14 septembre 2026

### ⚡ Self-service OIDC M2M — /api/oidc/self-client

- `POST /api/oidc/self-client` (token M2M requis) : crée/régénère le client
  OIDC lié à l'app appelante (FK `oauth_clients.app_id` résolue depuis le
  ServiceAccount — jamais depuis le body). Secret retourné une seule fois
  (stocké bcrypt) ; validation redirect_uris (http/https, 1..5).
- `GET /api/oidc/self-client` : métadonnées du client de l'app, sans secret.
- Nouvelle dépendance `get_current_service_account` (auth.py) — miroir de
  `get_current_admin` ; les routes CRUD admin OIDC restent inchangées
  (JWT admin requis).
- Refus tracés : SA multi-apps → 409 (ambiguïté app_id), JWT utilisateur →
  403. Audit : `oidc_self_client_create|regenerate|get`
  (service_account_id tracé).
- Finalité : premier déploiement d'app 100 % automatique (SSO sans
  HUB_ADMIN_PASSWORD côté app) — cf. spec exemple-app
  `docs/superpowers/specs/2026-09-14-oidc-self-provisioning-design.md`.
- Tests : 10 nouveaux (`test_oidc_self_client.py`) ; suite complète
  1098 passed / 0 failed.

---

## [3.54.1] - 14 septembre 2026

### ⚡ Corrigé — backups d'apps : extraction metadata en streaming + hot-reload retiré du banc prod

- **app_backups.py / extract_metadata_from_zip** : ne plus `json.load()` le
  data.json complet des backups — les backups génériques (format 3.0) peuvent
  dépasser plusieurs centaines de MB (exemple-app : 1M lignes / 53.7 MB zippé) et
  le parse complet en dicts Python dépassait la RAM du conteneur (2 GB) :
  MemoryError avalé par `asyncio.create_task` → backup figé "running" 85 %
  pour toujours. Désormais l'objet `metadata` (petit, en tête du JSON) est
  extrait en streaming (JSONDecoder.raw_decode incrémental, chunks 64 KB),
  la section `data` n'est jamais chargée. Fallback parse complet conservé
  pour les anciens formats sans "metadata".
- **docker-compose.dev.yml** : retrait de `--reload` du command hub-api —
  le watcher WatchFiles surveillait aussi le montage `apps_source` (les apps) :
  chaque écriture d'app (backup zip, upload) déclenchait un reload qui tuait
  les tâches de fond en cours (backups) et figeait le reloader sans respawn
  (hub gelé, incident 2026-09-14 ×3, restauration par docker restart hub-api).
- Preuve : backup exemple-app `success` (1 062 944 rows / 53.7 MB, ~60 s), hub UP
  en continu pendant et après le backup (cf. POULIDOR/docs/migrationv1_rapport/16-exemple-app.md).

---

## [3.54.0] - 12 septembre 2026

### 📣 Pour les utilisateurs
- **Ajouté** : le Hub surveille désormais automatiquement son token GitHub (catalogue des dépôts, vérification des mises à jour). Si le token est refusé par GitHub — révoqué ou expiré — les administrateurs reçoivent une notification immédiate (cloche) et la vue Apps GitHub affiche un avertissement orange. Fini la panne silencieuse : plus aucune fonction GitHub ne se dégrade sans qu'on le sache.

### ⚡ Moniteur de vivacité du token GitHub (alerte admins)
- `app/github_token_monitor.py` : check horaire `GET api.github.com/user` avec le token courant (env → app_settings), transport httpx injectable (tests MockTransport, zéro réseau). États `valid|invalid|missing|error` — les erreurs réseau/5xx sont muettes et sans effet sur l'état d'alerte.
- Alerte UNIQUEMENT sur la transition ok→invalid (anti-spam par état persisté en AppSetting) : `UserMessage` system/warning à chaque admin actif (cloche + toast SSE, lien « Corriger le token ») + `log_action` audit système. Réarmement automatique au retour `valid`.
- Boucle `periodic_github_token_check` (pattern `periodic_docker_sync`) : `GITHUB_TOKEN_CHECK_INTERVAL` (défaut 3600 s, 0 = désactivé), `create_task` au lifespan + cancel au shutdown.
- `GET /api/apps/github/repos` : nouveaux champs `token_valid`/`token_last_checked` ; badge de la vue Apps GitHub orange « token invalide » (dernier contrôle en tooltip).
- Tests : `test_github_token_monitor.py` (16 — décision pure, 401/403/5xx/réseau, fallback DB, transition + anti-spam, kill-switch) ; suite hub complète **1088 passed**.

---

## [3.53.1] - 12 septembre 2026

### 📣 Pour les utilisateurs
- **Corrigé** : le nouveau contrôle du socle de la vue Santé signalait à tort « Health & connexion au Hub » et « Versions & enregistrement » en échec sur des applications pourtant bien connectées. Le rapport est désormais fiable : une application saine affiche 7/7.

### ⚡ Corrigé : socle-check lit le format réel de /api/health/detailed (vérification réelle 3.53.0)
- Le health/detailed réel du socle (`hub-socle` ≥ 2.23.x) expose `hub.connected`
  et `app.version` **imbriqués** — pas de clés top-level `hub_connected`/`version`.
  Le module livré en 3.53.0 lisait les clés top-level (hypothèse reprise telle
  quelle dans les mocks des tests) : contrôles health et versions en fail à tort
  (5/7 observé sur `controle-socle`, app pourtant enregistrée et saine).
- **Corrigé** : `_hub_connected()` / `_app_version_of()` lisent les deux formats
  (imbriqué socle en priorité, top-level conservé pour tolérance).
- Détecté par la vérification en conditions réelles sur `controle-socle`
  (socle 2.23.3) ; 2 tests ajoutés au format réel — suite hub **1072 passed**.

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.53.0] - 12 septembre 2026

### 📣 Pour les utilisateurs
- **Ajouté** : dans la vue Santé, un bouton « Contrôler le socle » (🩺) sur chaque application. En un clic, il vérifie que l'application est bien intégrée au Hub — connexion, authentification des utilisateurs, canal machine-à-machine, préférences, découverte des données, sauvegardes et versions — puis affiche un rapport avec un score sur 7 et le détail de chaque vérification (durées incluses). Simple diagnostic : rien n'est modifié dans l'application.

### ⚡ Socle-check : contrôles d'intégration hub↔app en un clic (module + route + vue Santé)
- `app/socle_check.py` : module des 7 contrôles séquentiels — health détaillé (+ `hub_connected`), auth user JWT via JWKS, canal M2M service account (`/api/m2m/catalog`), round-trip préférences idempotent, discovery hub_data, liste backups (lecture seule), cohérence versions. Timeout 5 s/contrôle, zéro effet de bord, transport httpx injectable (tests MockTransport, zéro réseau).
- Route `POST /api/apps/{slug}/socle-check` (admin, `routes/app_socle_check.py`) : mint de 2 tokens éphémères TTL 5 min — JWT user `admin` sur l'app (discovery/backup) + service account au périmètre minimal `{slug: "service"}` (fidèle à `sa_apps_claim`, AN-2) — exécution contre `app.api_url` ; 404 slug inconnu, 409 sans api_url ; **rapport 200 même en échec** (un contrôle qui tombe est documenté, jamais bloquant).
- Vue Santé : bouton « Contrôler le socle » (🩺) sur chaque carte app + modale rapport — badge score coloré (≥7 vert / ≥4 orange / <4 rouge), versions hub/app, durées, 7 contrôles en pastilles pass/warn/fail ; `api.socleCheck` POST timeout 60 s ; échappement HTML sur tout contenu dynamique ; cache-busting 3.53.0.
- Tests : `test_socle_check.py` (module, 7 tests, transport mocké), `test_socle_check_route.py` (5 tests — tokens audités par décodage RSA réel : type/périmètre/TTL) ; suite hub complète **1070 passed**.

---

## [3.52.2] - 12 septembre 2026

### 📣 Pour les utilisateurs
- **Corrigé** : le bouton « Créer l'atelier » de la vue Santé échouait pour les applications dont le dossier technique ne porte pas le même nom que leur identifiant (ex. Transfert de fichiers). Le passage en atelier depuis la vue Santé fonctionne désormais de bout en bout pour ces applications.

### ⚡ Routes atelier : résolution dossier par slug (passage en atelier depuis la vue santé)
- `routes/workshop.py` : les 5 contrôles git/remote utilisaient `APPS/{slug}/` — pour les apps au dossier ≠ slug (ex. `exemple-app`→`exemple-app`), la création d'atelier répondait 400 « pas de repo git » alors même que le dépôt est correct. Tous les points d'appel passent par le résolveur `_app_dir(slug)` (v3.52.1).
- Tests : `test_create_ok_dir_differs_from_slug` (repo git réel dans un dossier au nom différent, 30 passed).

---

## [3.52.1] - 12 septembre 2026

### 📣 Pour les utilisateurs
- **Corrigé** : dans la vue Santé, l'atelier d'une application dont le dossier technique ne porte pas le même nom que son identifiant (ex. Philia Proxy) apparaissait comme inexistant — le bloc « Atelier : … » avec les actions Mettre à jour / Déployer ne s'affichait pas. Il est de nouveau visible et fonctionnel.

### ⚡ Résolution dossier app par slug (workshop) + nettoyage fiche atelier fantôme
- `workshop_service` : nouveau résolveur `_app_dir(slug)` — cas canonique `APPS/<slug>/` (validé par `APP_SLUG` du `.env`), sinon **scan des `.env`** des apps, repli `APPS/<slug>/` (comportement historique). Corrige la vue Santé et toutes les opérations atelier pour les apps dont dossier ≠ slug (`exemple-app`→`philia-proxy`, `exemple-app`→`exemple-app`, …) : 8 sites d'appel branchés.
- Tests : `test_workshop_state_dir_name_differs_from_slug`, `test_app_dir_resolver` (hub/tests/test_workshop_routes.py, 29 passed).

---

## [3.52.0] - 11 septembre 2026

### 📣 Pour les utilisateurs
- **Corrigé** : une application pouvait recevoir, via le Hub, les informations d'identification d'une **autre application** (cas des applications ayant accès à plusieurs services). Chaque application reçoit désormais **les siennes**, sans exception.
- **Sécurité** : les clés d'appel machine-à-machine d'une application ne donnent **plus accès aux ateliers** (environnements de test) ni aux applications désactivées. Chaque atelier est cloisonné : une fuite de clé ne peut plus exposer le reste du parc.
- **Nettoyage** : les accès croisés hérités du passé (13 constatés sur l'instance) ont été retirés automatiquement.

### 🔐 Sécurité M2M : identité d'app fiable (`/me/credentials`) + périmètre minimal des service accounts (AN-1/AN-2, banc 2026-09-10)

Traitement des 2 tickets 🔴 critiques issus de la campagne de banc du socle
2.23.1 (apps controle-socle / exemple-app vs Hub .68).

| Élément | Description |
|---------|-------------|
| **AN-1 — `/api/apps/me/credentials`** | L'endpoint résolvait l'app par `sa.applications[0]` (relation N-N **non ordonnée**) : tout SA multi-apps recevait les credentials d'une app arbitraire (observé : SA de `controle-socle` → `exemple-app-ws`). Désormais : app propriétaire du SA (convention de nommage existante), sinon unique app liée, sinon **409** (jamais d'app arbitraire). Appliqué au path token M2M **et** au path déprécié `X-Client-ID` |
| **AN-2 — périmètre M2M (Option B validée)** | Nouveau module `app/m2m_scope.py` : (1) `grant_shared_apps_to_sa` — les services partagés (`is_shared_service`, approuvés) sont accordés aux SA avec **parité de contexte atelier** (SA prod → services prod, SA atelier → ateliers) ; (2) `sa_apps_claim` — le claim JWT `apps` n'émet plus que les apps **actives + approuvées + du contexte du SA** (défense en profondeur) |
| **Harmonisation des 6 sites de copie** | Approbation (source du banc), création admin, auto-register, batch-import, regenerate-secret : parité atelier systématique via le helper central ; self-healing T1 et création SA manuelle factorisés sur le même code |
| **Purge one-shot** | `scripts/purge_workshop_m2m_leaks.py` (dry-run par défaut, `--apply`) : retire les liens N-N croisés atelier↔prod ; SA ambigus intacts et signalés. Exécutée sur la prod : **13 liens retirés** (SA Contrôle Socle/exemple-app −`exemple-app-ws` ; SA atelier `exemple-app-ws` −11 apps prod). Le SA de `exemple-app` **prod** (id 53) est intact |
| **Impact parc / écosystème** | ⚠️ Les SA d'ateliers n'ont **plus** d'accès M2M aux apps prod (et inversement) : toute app atelier qui testait du cross-apps vers des prods doit passer par une élévation explicite. Les apps prod continuent de découvrir les services prod partagés (M2M discovery préservé). Les tokens déjà émis gardent leur claim jusqu'à expiration (≤8 h) ; le garde runtime `m2m_authorization` bloquait déjà le cross-workshop |
| **Tests** | TDD : 15 nouveaux tests (`test_me_credentials_owner_resolution.py`, `test_m2m_scope_parity.py` — repro banc exact, parité sur 6 points d'entrée, claim JWT, purge, helper). Suite complète **1055 pass**. Validé en réel sur hub-api : repro banc AN-1 corrigé (`controle-socle` retourné) ; claim M2M de controle-socle passé de 13 apps (1 atelier) à 12 apps (0 atelier) |

---

## [3.51.0] - 11 septembre 2026

### 🛡️ Page admin « Droits Philia » — gestion des droits PhiliaNG depuis le Hub (phase 1)

Nouvel onglet Admin > « Droits Philia » : un administrateur consulte les
périmètres PhiliaNG d'un utilisateur (habilitations `mo` + droits d'agence
avec leurs groupes) et supprime un droit (groupe sur une agence), l'action
étant exécutée dans le SI source via l'app `philia-proxy`. Phase 2 (ajout de
droits) à venir.

| Fonctionnalité | Description |
|----------------|-------------|
| **Proxy `/api/philia-rights/*`** (admin) | Le Hub appelle l'app `philia-proxy` (résolue par slug, `api_url`) avec un **token de service signé** TTL 90 s (`apps={philia-proxy: admin}`, cache 60 s) — aucun endpoint ouvert, contrairement au pattern découverte `tags.py`. Erreurs mappées : app absente → 404, injoignable/5xx → 502, 4xx app forwardé |
| **Audit systématique** | Chaque suppression est tracée dans l'audit Hub (`philia_right_removed` : username admin + `user_ng/agence/groupe`) ; l'app audite de son côté |
| **UI — onglet admin** | `admin-philia-rights.js` + section `index.html` (onglet persistant `#tabPhiliaRights`) : sélection d'un utilisateur connu (sessions proxy avec id PhiliaNG) ou saisie manuelle d'un id NG, carte identité, blocs « Habilitations (mo) » et « Agences » (table + recherche client-side), confirmation forte avant suppression (modale), toast résultat, re-fetch ciblé du bloc agences |
| **Client API** | `api.getPhiliaRightsUsers()`, `api.getPhiliaPerimeters()`, `api.removePhiliaRight()` |
| **Tests** | 8 nouveaux (`hub/tests/test_philia_rights_proxy.py`, `httpx.MockTransport` — zéro réseau) : claims du token service, mapping d'erreurs, audit écrit ; suite complète 1040 tests verts |

---

## [3.50.10] - 11 septembre 2026

### 🖼️ Fix : heatmap « écrasé » au retour sur la vue d'ensemble de l'Audit

| Élément | Description |
|---------|-------------|
| Symptôme | Après consultation d'un autre onglet de l'Audit, le retour sur « Vue d'ensemble » affichait le heatmap « Activité du parc » réduit à quelques pixels de large |
| Cause racine | Deux défauts combinés : (1) le heatmap est **disposé** quand on quitte l'onglet (anti-fuite), (2) un re-render déclenché pendant l'onglet caché (SSE `audit_updated`) initialisait ECharts dans un conteneur `display:none` → canvas à largeur ~0. Le listener `shown.bs.tab` ne gérait que l'onglet Uptime : aucun rattrapage au retour |
| Fix | `shown.bs.tab` re-rend `loadAuditPageOverview()` (Vue d'ensemble) et `loadAuditPageApps()` (Par Application — même maladie) : le rendu se fait dans un conteneur visible → dimensions correctes + données fraîches. L'état « écrasé » ne peut plus persister |
| Validé | Reproduction manuelle (Audit → autre onglet → retour Vue d'ensemble) : heatmap pleine largeur |
| Fichiers | `frontend/public/js/modules/audit-page.js` |

---

## [3.50.9] - 11 septembre 2026

### 🔔 Alerte audit : détection automatique des boucles de requêtes répétées

| Élément | Description |
|---------|-------------|
| Comportement | Nouveau type d'alerte `error_loop` dans `GET /api/audit/overview/alerts` : signale N+ requêtes **identiques** (méthode + chemin + code HTTP + IP) dans une fenêtre glissante courte — détecte automatiquement le scénario des 422 en boucle (une app qui retente son enregistrement toutes les 15 s, 5 810 erreurs avant intervention humaine) |
| Paramétrage | `loop_window_minutes` (défaut 15) et `loop_threshold` (défaut 10), en query params comme `auth_threshold` |
| Affichage | Carte danger dans l'aperçu Audit (icône ↻, méthode+chemin+code, IP, occurrences) ; bouton « Détails » → onglet Erreurs préfiltré (chemin + code HTTP) |
| Validé en prod | Sur les données historiques : la boucle register/422 détectée à 5 664 occurrences (fenêtre 24 h) ; silencieuse en fenêtre 15 min depuis la résolution — pas de faux positif persistant |
| Tests | `test_overview_alerts_error_loop` (seuil atteint → 1 alerte, bruit sous seuil ignoré, deeplink path+code), `test_error_loop_window_excludes_old_occurrences` (fenêtre courte exclut les occurrences vieilles) |
| Fichiers | `hub/app/routes/audit.py` (`get_overview_alerts`), `frontend/public/js/modules/audit-page.js` (`renderAuditAlerts`, `auditAlertDetails`), `hub/tests/test_api_audit_alerts.py` |

---

## [3.50.8] - 10 septembre 2026

### 🎯 Exclusion des ateliers de l'uptime utilisateur

| Élément | Description |
|---------|-------------|
| Comportement | `GET /api/audit/usage/user/{id}` exclut désormais les segments dont le slug se termine par « -ws » (ateliers) : timeline, « Répartition du temps par application » et temps total ne comptent que l'usage **production**. Avant, une session atelier apparaissait comme une app séparée (ex. 2 lignes « exemple-app-7 » = prod + atelier) |
| Implémentation | Filtre SQL `~AppUsageSegment.app_slug.endswith("-ws")` dans `get_user_usage_day` (la convention `{slug}-ws` est réservée aux ateliers : hubctl + workshop_service) |
| Tests | `test_usage_user_excludes_workshop_segments` (régression : segments prod + atelier → seul le prod compte dans segments/totals/day_total) ; suites usage/SLO 18/18 |
| Fichiers | `hub/app/routes/audit.py`, `hub/tests/test_api_usage_endpoints.py` |

---

## [3.50.7] - 10 septembre 2026

### 🐛 Fix : graphique « Répartition du temps par application » (uptime utilisateur)

| Élément | Description |
|---------|-------------|
| Symptôme | Les noms à gauche du graphique ne correspondaient pas aux barres (ex. label « Philia Proxy » mais tooltip « Annuaire Téléphonie ») et les durées semblaient incohérentes avec l'activité du jour |
| Cause racine | Le rendu utilisait **une série bar par app** (valeurs null partout sauf à sa position). Avec N séries bar sur un axe catégorie, ECharts place les N barres **côte à côte dans un cluster** par bande de 30 px : dès 2-3 apps, les barres (18 px) débordent de leur bande et atterrissent visuellement à côté du mauvais label. Les données backend étaient correctes (vérifié en prod : totaux = somme exacte des segments) — c'était purement le rendu |
| Fix | **Une seule série** : chaque data item porte sa valeur, sa couleur et son `decal` (motif différenciant les couleurs dupliquées, via `itemStyle` de l'item — ECharts 5.5) ; tooltip par `dataIndex`. Chaque barre est désormais centrée exactement sur son label |
| Fichiers | `frontend/public/js/modules/audit-charts.js` (`renderUserDayBreakdown`) |

### 🧪 Fix : test `test_slo_defaults` échouait sur les machines au .env activé

| Élément | Description |
|---------|-------------|
| Cause racine | Le test instanciait `Settings()` qui lit aussi le `.env` local — or l'activation d'`OIDC_SLO_ENABLED=true` après validation e2e est le mécanisme prévu : le test vérifiait la config de la machine au lieu du défaut du code (échec sur tout serveur au .env activé, y compris la prod) |
| Fix | Isolation : `Settings(_env_file=None)` + `delenv` des deux vars → le test vérifie le défaut DU CODE ; les tests d'activation/désactivation via env existent déjà (`test_slo_enabled_via_env` / `test_slo_disabled_via_env`) |
| Fichiers | `hub/tests/test_slo_config.py` |

---

## [3.50.6] - 10 septembre 2026

### 🌿 Branches → atelier + refonte de la vue Santé des applications

| Élément | Description |
|---------|-------------|
| Lookup des branches | `GET /api/apps/{slug}/branches` (admin) : liste les branches du dépôt GitHub de l'app, branche par défaut en tête, cache mémoire 30 s (convention anti-spam GitHub du parc), erreurs typées (token, rate limit, dépôt introuvable, non-GitHub) |
| Branche → atelier | `{ref}` optionnel sur `POST /{slug}/workshop` et `/workshop/update` : `validate_ws_ref` (format sûr, jamais en shell), checkout `origin/{ref}` dans `_op_create` (avant écriture `WS_REF`/`WS_COMMIT`) et `_op_update` (idempotence + rollback inchangés). **Garde-fou intact** : le ship reste verrouillé sur `origin/main` — une branche atteint la prod uniquement après merge (release-on-main) |
| Cartes « ops console » | Refonte validée en maquette : bandeau d'en-tête teinté selon le statut, badge uppercase, métriques en valeurs fortes (Latence/Uptime/Services), barre d'actions unifiée (▶ ■ ⟳ 📋 🌿), suppression des emojis |
| Slots fixes | Layout invariant : grille 3 métriques toujours rendue (« — » si absent), version `v—`, `Hub —`, statut `…` (checking), min-height carte — aucun déplacement visuel si des valeurs manquent |
| Panneau branches 🌿 | Chargé à la demande (cache client 30 s, spinner, refresh ↻) — zéro fetch au rendu de la page. Bouton « Déployer ▸ » : envoie la branche choisie en atelier (création ou maj selon l'état) avec suivi via le modal d'opération existant. App sans dépôt → bouton « Configurer le dépôt git » |
| Loading non bloquant | `loadAppsHealth` rend immédiatement les cartes depuis `/apps/all` (état « vérification… », carte HUB injectée) puis complète avec le sondage santé en arrière-plan ; compteur de séquence anti-course. Fin de l'écran vide « stun locked » pendant les timeouts |
| Filtres client | Barre instantanée (aucun appel API) : recherche nom/slug, chips statut Tous/Online/Degraded/Offline, switch « atelier actif » (attribut `data-ws` tenu à jour par `loadWorkshopStates`) |
| Tests | +11 tests routes (`ref` threading, refs invalides → 400 avant gardes d'état, parse branches, cache 30 s, fallback sans dépôt) ; suites workshop/github 117/117, globale 800/801 (échec pré-existant `test_slo_defaults` hors périmètre, prouvé par stash) |

---

## [3.50.5] - 9 septembre 2026

### ⚡ Fix : import des Service Accounts existants lors de la self-restauration

| Élément | Description |
|---------|-------------|
| Cause racine | `_import_service_accounts` (branche update) assignait `existing.applications = apps` sans précharger la relation → `lazy='raise'` (`InvalidRequestError`) → **chaque SA existant du backup était skippé** à la restauration (32 erreurs observées sur un restore hub réel). La branche création n'était pas affectée — trou de test : seule la création était couverte |
| Fix | `selectinload(ServiceAccount.applications)` sur la requête du SA existant dans `hub_restore.py` ; test de régression `test_import_sa_update` avec `expire_all()` pour simuler le cas prod (SA venu d'une requête sans préchargement) |
| Tests | `test_hub_restore.py` 20/20 ; non-régression backups/snapshots/network/propagation 84/84 |
| Impact | La self-restauration du hub met désormais à jour correctement les SA existants (secret, nom, is_active, applications) au lieu de les ignorer silencieusement. Suites : 3.50.4 (auto-création du SA - Hub) |

---

## [3.50.4] - 9 septembre 2026

### ⚡ Fix : self-restauration du Hub — « Aucun service account trouvé »

| Élément | Description |
|---------|-------------|
| Cause racine | L'app système `hub` créée par `ensure_hub_system_application` n'avait aucun `ServiceAccount` associé, et la fonction faisait un `return` anticipé dans la branche de création. Le flux de restauration self-backup (POST `/{slug}/backups/{id}/restore`, restore upload, import+restore) exige un SA de l'app cible pour générer son token M2M → échec systématique « Aucun service account trouvé pour cette application » sur toute instance où le SA n'avait pas été créé manuellement |
| Fix | `ensure_hub_system_application` garantit désormais app **+ SA actif associé** : suppression du `return` anticipé, auto-création d'un « SA - Hub » (client_id `sa_<hex>`, secret aléatoire hashé, idempotent — aucun doublon si un SA est déjà associé). Le hub signe ses propres tokens M2M (`is_service_account=True`), le secret n'a pas besoin d'être partagé |
| Fichiers | `hub/app/routes/app_backups.py` (`ensure_hub_system_application`, import `get_password_hash`) ; `hub/tests/test_hub_restore.py` (+2 tests de régression : création du SA, non-duplication) |
| Tests | `test_hub_restore.py` 19/19 ; non-régression backups/snapshots/network/propagation 83/83 |
| Migration | Aucune. Au premier accès à la page sauvegardes (ou tout appel à `get_app_or_404("hub")`), le SA manquant est auto-créé — correctif effectif sans intervention sur les instances existantes |

---

## [3.50.3] - 8 septembre 2026

### 🧪 Suite de tests : 11 échecs résiduels corrigés — 1015/1015 verte

| Élément | Description |
|---------|-------------|
| `croniter` (8 tests) | Déclaré dans `requirements.txt` mais absent de l'env Python locale de dev → `ModuleNotFoundError` sur tout `TestSnapshotCronDue`. Installé (`pip --user`) : aucun changement code, l'env locale est réalignée sur la source de vérité |
| Snapshots (1 test) | `test_create_snapshot_apps_code_only` dépendait des chemins container `/code/*` : `SECRETS_DIR` (constante module, monkeypatchable) et `/code/storage/snapshots` en dur dans `create_snapshot` → extraction en constante module `SNAPSHOT_STORAGE_DIR` (comportement inchangé) ; le test génère désormais une clé RSA jetable dans `tmp_path` et redirige les deux chemins → portable hors container |
| OIDC launch (1 test) | Test obsolète face à `ff8d2a4` (redirection forcée `{frontend_url}/logout?provider=hub` pour purger la session Vikunja résiduelle) : fixture aligné sur la config réelle (frontend_url = racine) + assertion mise à jour |
| hub-host (1 test) | `configured: bool(settings.hub_host)` ne peut être True que si `HUB_HOST` est injecté (docker-compose en prod) → le test simule `HUB_HOST` sur le singleton Settings (déterministe hors container) |
| Validation | Suite complète : **1015 passed, 0 failed** (avant : 1004 passed, 11 failed) |

---

## [3.50.2] - 8 septembre 2026

### 🐛 Allocation de sous-réseaux : les réseaux Docker épinglés manuellement sont désormais exclus

| Élément | Description |
|---------|-------------|
| Incident | `POST /api/apps/allocate-ports` a attribué `10.50.5.0/24` à une app alors que `exemple-app-network` l'occupait déjà (épinglage manuel hors Hub) → `docker compose up` échouait avec « invalid pool request: Pool overlaps with other one on this address space » |
| Cause racine | Les **ports** étaient filtrés par un scan Docker du réel (`get_used_host_ports`), mais les **sous-réseaux** n'étaient consultés que dans la table `port_allocations` : tout réseau Docker créé manuellement (ou par un stack tiers) était invisible de l'allocateur |
| Fix scanner | `DockerScanner.get_used_network_subnets()` : scan best-effort des sous-réseaux (IPAM) de tous les réseaux Docker existants, symétrique de `get_used_host_ports` — ne lève jamais, `set()` si Docker indisponible |
| Fix allocateur | `docker_occupied_subnets()` (flag `subnet_scan_docker_enabled`, host local uniquement, exécution en thread) fusionné dans `allocate_ports_for_app` : nouvelle allocation **et** backfill paresseux des allocations antérieures sans subnet |
| Tests | 6 nouveaux tests (exclusion, host distant ignoré, flag désactivé, fallback échec scan, backfill) ; suite port_allocation 26/26 verte |
| Déploiement | Rebuild `hub-api` ; vérifié en réel : allocation exemple-app renvoie désormais `10.50.8.0/24` en sautant 10.50.5/.6/.7 épinglés manuellement |

---

## [3.50.1] - 7 septembre 2026

### 📣 Pour les utilisateurs
- **Corrigé** : le numéro de version affiché en haut à gauche du Hub (à côté du logo) est de nouveau fiable — il reste correct même après une mise à jour du Hub, sans avoir à recharger plusieurs fois la page.

### 🐛 Badge de version navbar : lit enfin la source de vérité (/api/health)

| Élément | Description |
|---------|-------------|
| Incident | Le badge de version de la navbar affichait une version périmée après un bump de `VERSION` + restart API : le badge n'était mis à jour que par le fallback statique baked dans l'image frontend au build |
| Cause racine | `updateHubVersionBadge()` (init.js) fetchait `/health` ; nginx ne proxifie que `/api/*` → le fallback SPA servait `index.html` en 200 `text/html` → `resp.json()` levait une SyntaxError → catch silencieux → fallback statique |
| Fix badge | `init.js` fetch désormais `/api/health` (même handler FastAPI, proxifié par nginx, convention `/api/*` du codebase) : le badge reflète le `VERSION` monté dans l'API, sans rebuild de l'image frontend |
| Fix collatéral | `admin-snapshots.js` (polling post-restore) avait le même fetch `/health` : nginx répondait 200 sur du HTML même API down → « Hub opérationnel » affiché immédiatement, à tort. Même correction `/api/health` : 502 pendant l'arrêt API → le polling attend désormais le vrai retour |
| Déploiement | Rebuild `hub-frontend` requis (fichiers statiques baked) ; vérifié en prod locale : `/api/health` 200 JSON, badge `v3.50.1` servi dans index.html |

---

## [3.50.0] - 7 septembre 2026

### ⚡ Sync tokens : fin des fermetures d'onglets (401 refresh) — grâce 60 s + access token 8 h

| Élément | Description |
|---------|-------------|
| Incident | Les onglets des apps se déconnectaient (401 « Refresh token invalide ou expiré » → `logout()` → fermeture d'onglet) : deux onglets d'une même app partagent le refresh token (localStorage) ; l'onglet A rotate, l'onglet B re-présente l'ancien token déjà révoqué → 401. Déclencheur fréquent : l'événement SSE `user_permissions_changed` atteint tous les onglets simultanément |
| Grâce rotation | `rotate_refresh_token` mémorise désormais le remplaçant (`refresh_tokens.replaced_by_token`, migration 059 idempotente `ADD COLUMN IF NOT EXISTS`) : si l'ancien token est re-présenté < 60 s (`refresh_token_grace_seconds`, nouveau réglage), le Hub **re-délivre le même remplaçant** au lieu de renvoyer 401 (pattern OAuth BCP). Au-delà de 60 s, la réutilisation reste un 401 (détection de vol préservée). `cleanup_expired_tokens` nullifie `replaced_by_token` après la fenêtre |
| Endpoints | La grâce s'applique aux deux endpoints : `POST /api/auth/token` (grant `refresh_token`) et `POST /api/auth/token/refresh` (JSON). Path de re-délivrance en lecture seule : idempotent sous concurrence ; un `revoke_all_user_tokens` coupe la grâce (logout everywhere inchangé) |
| Config | `ACCESS_TOKEN_EXPIRE_MINUTES` 30 → **480** (8 h, aligné cookie OIDC) sur les 4 sources (code, docker-compose, install.sh, .env.example) — valable **uniquement en déploiement LAN** ; revert à 30 si exposition externe (note de décision dans `SECURITY_AUDIT.md` §9, checklist `GUIDE_ADMIN.md` mise à jour) |
| Sécurité | `replaced_by_token` stocké en clair en DB mais nullifié après 60 s par le cleanup ; lecture DB = compromis total de toute façon (chiffrement Fernet en backlog) |
| Tests | 4 tests unitaires (`test_auth.py::TestRefreshTokenGrace` : stockage remplaçant, re-délivrance idempotente, 401 après grâce, cleanup) + 2 tests API (`test_api_auth.py::TestRefreshGraceAPI` : double refresh 200/200 même token sur les 2 endpoints) ; suite complète 998 passed, 11 échecs préexistants sur main (snapshots/health, non liés) |
| Terrain | Hub déployé localement depuis cette branche : TTL émis vérifié 28800 s ; double refresh simultané réel → 200/200 sans déconnexion ; post-grâce −61 s → 401 |

---

## [3.49.2] - 7 septembre 2026

### ✨ Modale droits : layout compact une ligne (variante B — companion validé)

| Élément | Description |
|---------|-------------|
| Changement | Chaque ligne : checkbox + nom (ellipsis ; mini-badges profil/override en icônes avec infobulles) + bouton reset si override + rôle (110 px) + « Atelier » — tout tient sur une ligne, aligné, même avec profil + override |
| Détails | `app-name` shrinkable (nowrap + ellipsis) ; badge override conserve `data-override-badge` (reset) ; bouton reset hors du label (pas de toggle parasite) ; choix validé par l'utilisateur via companion visuel |

---

## [3.49.1] - 7 septembre 2026

### 🐛 Modale droits : ligne app/rôle/Atelier ne s'écrase plus (fix UI)

| Élément | Description |
|---------|-------------|
| Incident | La colonne « Atelier » (3.48.3) faisait déborder `.app-role-row` dans la modale (~500 px) : nom + rôle + Atelier + « Profil : … » + badge « Modifié » se compressaient, texte en wrap vertical |
| Fix | `.app-role-row` en flex-wrap avec gaps ; nom d'app shrinkable (`flex: 1 1 170px`, min 150 px), rôle et Atelier en `flex: 0 0 auto` + nowrap ; indice profil + badge « Modifié » + reset regroupés dans `.role-row-badges` (`flex-basis: 100%`) = 2e ligne dédiée quand présents |
| Compat | Handlers `data-override-badge` / `resetAppRoleToProfile` : sélecteurs globaux, insensibles à la nouvelle hiérarchie DOM |

---

## [3.49.0] - 7 septembre 2026

### ✨ Admin — onglets à 2 niveaux (lisibilité)

| Élément | Description |
|---------|-------------|
| Ajouté | Page Administration : 4 onglets principaux (Applications, Sauvegardes, VPN, Configuration) + 5 sous-onglets en pilules derrière « Configuration » (Groupes, Service Accounts, Clients OIDC, Apps GitHub, Réseau). Le dernier sous-onglet utilisé est mémorisé (localStorage `hub.admin.subtab`) |
| Conservé | Panes, lazy-load, `currentPage`, badges (`pendingAppsBadge`, `networkPendingBadge`), listener VPN et workspace Sauvegardes inchangés — aucune modification des modules métier |

---

## [3.48.3] - 7 septembre 2026

### ✨ Accès atelier par utilisateur (`user_workshop_access`) — visibilité dynamique des ateliers

| Élément | Description |
|---------|-------------|
| Ajouté | Un admin peut activer, par utilisateur et par app de base, la visibilité de l'atelier `{slug}-ws` — indépendamment du rôle sur la prod (un testeur peut avoir l'atelier sans la prod). Migration 058 (socle 3.48.0), règles à la lecture : dès que l'atelier est créé il apparaît (SSE `apps_updated`), s'il est supprimé il disparaît, l'activation persiste |
| Backend | `list_apps` non-admin : les ateliers autorisés sont listés (`my_role="user"`, badge ATELIER). Les 7 sites d'émission de JWT (login, refresh, launch, sso, auto-login…) portent `{slug}-ws: "user"`. Gardes `/api/auth/apps/{slug}/launch` et `/api/auth/sso` étendus (403 → 200 avec activation via `workshop_role_for_app`) |
| API admin | `POST /api/users/{id}/apps` : `workshop_access: [app_id…]` autoritaire (full-sync, ids d'ateliers ignorés, absent = inchangé) ; `GET` renvoie le tableau. SSE `user_permissions_changed` enrichi (`workshop_access_count`) |
| Frontend | Modale des droits : checkbox « Atelier » indépendante du rôle (tooltip), état initial depuis `GET /users/{id}/apps`, envoi via `api.setUserApps` étendu ; `api.getUserApps` |
| Sécurité | Opérations atelier (créer/maj/supprimer/déployer) toujours admin uniquement ; isolation M2M inchangée ; atelier invisible si inactif/absent |
| Tests | 17 tests dédiés (`test_workshop_access.py`) + suite intégrale : 1000 passed (2 échecs préexistants liés au `.env` de prod, reproduits sur main) |

---

## [3.48.2] - 7 septembre 2026

### ⚡ « Mon rôle » : 500 sous concurrence — endpoint idempotent (fix)

| Élément | Description |
|---------|-------------|
| Incident | Double-émission du sélecteur « Mon rôle » → deux `PUT /api/apps/me/role/{slug}` quasi simultanés ; le second SELECT s'exécute avant le commit du premier → INSERT → `UniqueViolationError` sur `uq_user_app_roles_user_app` → 500 |
| Fix | `update_my_role` : `try/except IntegrityError` autour du commit → rollback → reprise de la ligne créée par la requête concurrente → mise à jour du rôle → **200** (idempotent). Ligne introuvable après rollback (cas anormal) → erreur relancée |
| Subtilité async | Ids/nom capturés avant le `try` : après rollback, `current_user`/`app` sont expirés — tout accès attribut lève `MissingGreenlet` dans le chemin d'exception |
| Tests | Course réelle reproduite (2 connexions sqlite même fichier, INSERT concurrent pendant la fenêtre) — rouge sur l'ancien code, vert avec le fix ; idempotence séquentielle (2× PUT → 200/200) ; relance si ligne absente. 140 tests role/apps/users passés |

---

## [3.48.1] - 7 septembre 2026

### 🐛 Messages : `expires_at`/`scheduled_at` avec timezone faisaient échouer l'envoi

| Élément | Description |
|---------|-------------|
| Fix tz-aware | `POST /api/messages/send` (admin) et `/api/messages/m2m/send` (apps) échouaient en `asyncpg DataError: can't subtract offset-naive and offset-aware datetimes` quand `expires_at` (ou `scheduled_at`) était envoyé en ISO 8601 avec suffixe `Z`/offset : Pydantic v2 produit un datetime aware (`tzinfo=TzInfo(0)`) que asyncpg refuse d'encoder pour les colonnes `TIMESTAMP WITHOUT TIME ZONE` (convention parc : datetimes naive UTC en DB). Tout broadcast/ciblage admin avec expiration — et tout message M2M avec `expires_at` — était rejeté (500, message non créé). |
| Normalisation à la source | Helper `to_naive_utc()` dans `schemas.py` + `field_validator` sur `MessageCreate` (`expires_at`, `scheduled_at`) et `MessageM2MCreate` (`expires_at`) : vrai décalage `astimezone(utc)` puis retrait du tzinfo (un `+02:00` est converti, pas tronqué) ; valeurs naive et `None` inchangées. Couvre les 3 sites d'INSERT (broadcast admin, ciblé admin, M2M) sans modification des routes. |
| Pourquoi invisible en tests | Suite de tests sur SQLite in-memory : l'encodage strict asyncpg (PostgreSQL) n'intervient jamais. Aucun test ne passait par le parsing Pydantic de ces champs. |
| Tests | `test_message_schemas_tz.py` : 7 tests (suffixe `Z`, offset `+02:00`, objet datetime aware, naive inchangé, `None` — admin + M2M). Repro : 4 failed avant fix → 7 passed après. Non-régression : 62 tests messages/M2M passants. |

---

## [3.48.0] - 6 septembre 2026

### 🏭 Atelier : accès par utilisateur — socle données (lot 1)

| Élément | Description |
|---------|-------------|
| Migration 058 | Table `user_workshop_access` (user_id × app_id, UNIQUE, FK `ON DELETE CASCADE`, 2 index) — l'activation référence **toujours l'app de base** (jamais la `-ws`) : elle survit à la suppression/recréation de l'atelier. Idempotente (`IF NOT EXISTS`). |
| Modèle + helpers | Relation SQLAlchemy `UserWorkshopAccess` (+102 l. models) ; helpers `workshop_role_for_app()` et `get_apps_with_roles_and_workshops()` pour la visibilité des apps `-ws` par utilisateur. |
| Spec | Design validé : `docs/superpowers/specs/2026-09-06-atelier-user-access-design.md` (API `workshop_access` en tableau, full-sync) — lots suivants : endpoints + UI. |
| Tests | `test_workshop_access.py` : 9 tests (socle données/helpers). |

> Merge de la branche `feat/atelier-user-access` (rebasée sur 3.47.5) — travaux parallèles validés : 27 tests de l'état fusionné passants.

---

## [3.47.5] - 6 septembre 2026

### 🐛 Snapshots : la propre ligne du snapshot n'embarque plus dans le dump

| Élément | Description |
|---------|-------------|
| Fix auto-référence | `run_pg_dump` ajoute `--exclude-table=hub_snapshots` : la ligne du snapshot en cours (statut `running` au moment du dump) n'est plus capturée dans l'archive. Auparavant, au restore, le kill-switch la requalifiait `failed` (« Snapshot interrompu ») — cosmétique mais trompeur dans la liste des snapshots du hub restauré. |
| Effet bord assumé | L'historique des snapshots ne transite plus par l'archive (les fichiers `.enc` de `storage/snapshots` n'y transitaient de toute façon pas). Sur restore bare-metal, la ligne insérée manuellement par le runbook **survit** désormais au `pg_restore --clean` (table hors dump). |
| Tests | `test_pg_dump_excludes_own_snapshots_table` : assertion du flag dans la commande pg_dump (4/4 collecteurs). |

---

## [3.47.4] - 6 septembre 2026

### 🛡️ Imports de backups > 1 Mo (413) + restore robuste des snapshots legacy

| Élément | Description |
|---------|-------------|
| Fix 413 nginx | Aucun `client_max_body_size` n'était défini dans les server blocks du hub (`nginx.conf` :80, `nginx-ssl.conf` :80/:443) → défaut nginx 1 Mio : tout import de backup chiffré > 1 Mo était rejeté en 413 par le reverse proxy avant d'atteindre l'API. Fix : `client_max_body_size 50M` (cohérent avec `nginx-philia-ng.conf`). Rebuild image `hub-frontend` requis. |
| Réconciliation snapshots legacy | Nouvelle étape dans `restore_snapshot.py` (après `pg_restore`, avant le boot) : `_reconcile_forked_migrations()` détecte les migrations homonymes divergentes entre dépôts (fourche 027 : `add_port_allocations` ici vs normalisation de données sur un hub legacy) via l'absence de la table attendue, et purge la ligne de `schema_migrations` → le boot rejoue notre 027 idempotente. **No-op sur restore normal** (tables présentes), extensibles via `FORKED_MIGRATIONS`. Nécessaire au scénario de migration legacy → hub neuf. |
| Tests | 3 nouveaux tests SQLite (`test_restore_snapshot.py`) : purge si table absente, no-op si table présente, tolérance DB sans `schema_migrations` (6/6). |

---

## [3.47.3] - 6 septembre 2026

### ⚡ Atelier : fuite d'env hub vers les apps + fiches -ws fantômes (fix)

| Élément | Description |
|---------|-------------|
| Fix env | `_ws_compose_env` ne transmet plus l'env du conteneur hub-api au `docker compose` atelier : allowlist `_COMPOSE_ENV_ALLOWLIST` (PATH/HOME/locales + DOCKER_*) via le nouveau paramètre `env_base` de `_run_cmd` (défaut `os.environ`, rétrocompatible). Cause : collision de nom `HUB_HTTPS_ENABLED` (hub = « le hub sert du HTTPS » ≠ app = « joindre l'API hub en HTTPS ») → app atelier en HTTPS sur le port HTTP du hub (`SSL WRONG_VERSION_NUMBER`), enregistrement + JWKS en échec, atelier invisible (« Mode dégradé ») |
| Périmètre env | Couvre create/update/remove/ship (compose atelier, make prod et rollback) ET `_op_recreate` (même classe de fuite vers les apps prod recréées depuis l'UI) |
| Fix fantômes | Suppression d'atelier (remove et ship) : la fiche `Application {slug}-ws` enregistrée par l'app est désormais supprimée (`_delete_workshop_app_row`, garde `is_workshop=True`, réutilise `delete_app_cascade` : SA owner, ports, cascades), appel best-effort non bloquant. Sans cela, les lignes fantômes pointaient sur des ports réattribués à un autre atelier → mauvaise app ouverte depuis le hub |
| Tests | 10 nouveaux (allowlist, non-fuite create/ship/recreate, suppression fiche nominale/garde prod/absente/échec non bloquant) — 43/43 workshop, suite complète verte |
| Remédiation prod | Atelier exemple-app-ws recréé avec env sain (enregistrement OK) ; 4 fiches fantômes nettoyées en DB (exemple-app-ws, canari-ws, exemple-app-api-v3-ws, test-template-ws) |
| Limites | hubctl (CLI hôte) garde `{**os.environ, ...}` : sans risque sauf si le shell hôte exporte des clés en collision ; parité à prévoir (sous-module hubctl_src) |

---

## [3.47.2] - 5 septembre 2026

### 🔁 SLO poste partagé : revoke distant normalisé (username courant + oidc_sub) (fix)

| Élément | Description |
|---------|-------------|
| Fix | `propagate_remote_revoke` résout l'identifiant du cookie (désormais un `oidc_sub` immuable) vers le user, et révoque par **username courant d'abord puis oidc_sub** (double appel best-effort dédupliqué) |
| Cas | User renommé : le revoke M2M ne manque plus la session indexée sous le nouveau nom ; user classique : comportement identique (appel unique) |
| Hygiène | Param renommé `old_identifier`, logs récap par traducteur, best-effort préservé (404/500 loggés, jamais bloquants) |

## [3.47.1] - 5 septembre 2026

### 🛡️ Backup/restore : les ancres identitaires sont exportées et restaurées (fix)

| Élément | Description |
|---------|-------------|
| Export | `UserExport` porte désormais `uuid`, `oidc_sub`, `prenom`, `nom` |
| Restore | Création : ancres exportées respectées si présentes ; fallback génération sinon (backups legacy rétrocompatibles) |
| Restore | Mise à jour (match username) : inchangée — jamais d'écrasement des ancres existantes |
| Impact | Un disaster recovery ne casse plus les liaisons exemple-app-16 (`user_uuid`) ni les liaisons OIDC des renommés ; prénom/nom préservés |

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.47.0] - 5 septembre 2026

### 🔗 OIDC : `sub` immuable (`oidc_sub`) — rattachement automatique des clients après renommage (minor)

| Élément | Description |
|---------|-------------|
| Nouveau | `users.oidc_sub` : identifiant OIDC immuable (backfill = username — liaisons clients existantes intactes) |
| Émission | `id_token`/`access_token`/`userinfo` émettent `sub = oidc_sub` ; email synthétique figé `{oidc_sub}@hub.internal` |
| Rename | Ne touche jamais `oidc_sub` — Vikunja & co conservent la liaison sans intervention |
| Compat | Cookie session OIDC et back-channel logout résolus par `oidc_sub` avec fallback `username` (sessions pré-déploiement) ; migration additive automatique |

---

## [3.46.0] - 5 septembre 2026

### 🆔 Identité utilisateur : UUID stable, prénom/nom éditables, renommage du login (minor)

| Élément | Description |
|---------|-------------|
| Nouveau | `users.uuid` (identifiant stable immuable, backfillé au démarrage), `users.prenom`, `users.nom` |
| JWT/OIDC | Claims `uid`, `prenom`, `nom` ajoutés aux tokens utilisateur (`sub` inchangé) ; claim OIDC `name` = « Prénom Nom » |
| API | `PATCH /api/users/{id}` accepte `prenom`/`nom` ; nouveau `POST /api/users/{id}/rename` (admin, unicité 409, révocation tokens, audit, force_logout) |
| UI | Champs Prénom/Nom dans la modale admin + bouton « Renommer l'identifiant » avec suggestion pnom et confirmation |
| Compat | Migration additive automatique au démarrage (aucune action manuelle) ; `sub` = username inchangé |

---

## [3.45.1] - 3 septembre 2026

### 🐛 Dropdown rôle du dashboard : fermeture immédiate + toggles checkboxes (fix)

| Élément | Description |
|---------|-------------|
| Symptôme | Dashboard admin : la liste native du `<select>` « Mon rôle » se refermait aussitôt l'ouverture — il fallait maintenir le bouton enfoncé |
| Cause racine | Le délégué click global (`app.js`) dispatchait l'action de tout élément `data-action`, y compris les `<select>` ; or un select natif émet un `click` à l'ouverture → `updateMyAppRole()` partait avec la valeur inchangée et `loadApps()` reconstruisait la grille (`innerHTML`), remplaçant le `<select>` pendant que sa liste était ouverte |
| Correctif | Le délégué click ignore désormais les champs de formulaire (`SELECT`, `OPTION`, `OPTGROUP`, `DATALIST`, `INPUT`, `TEXTAREA`), comme il ignorait déjà `<form>` — leurs actions restent couvertes par les délégués `change`/`input`/`keydown` |
| Effet secondaire corrigé | Le `preventDefault()` du délégué click bloquait le toggle natif des checkboxes `data-action` (sélection messages, select-all docker, confirmations restore…) et déclenchait leurs actions sur un état périmé ; le toggle natif + `change` sont rétablis |
| Périmètre | ~60 champs de formulaire porteurs de `data-action` passés en revue : tous en sémantique change/input, aucun ne dépendait du dispatch click |

---

## [3.45.0] - 1 septembre 2026

### 🛡️ Anti-abus `/api/oidc/token` : throttle sur les échecs, par client (minor)

| Élément | Description |
|---------|-------------|
| Audit sécurité | Après le retrait du rate-limit IP (3.44.4), analyse des vecteurs résiduels : guessing client_secret/codes/refresh tokens invraisemblable (bcrypt, 384 bits, one-shot, PKCE) ; risque résiduel réel = hammering sans frein (bcrypt ~100 ms + query DB par requête, bruit logs) |
| Correctif | Throttle **sur les échecs** (400/401) par `client_id` : 30 échecs / 60 s → 429 + `Retry-After`. Les **succès ne sont jamais comptés** et réinitialisent le compteur → le SSO légitime ne peut plus être pénalisé (garantie structurelle : un burst de connexions valides = ~0 échec) |
| Garbage | Requêtes sans `client_id` (scans) plafonnées par IP — aucun trafic légitime n'omet le client_id (form ou Basic header) |
| Isolation | Le throttle d'un client n'affecte pas les autres clients |
| Observabilité | Log WARNING au déclenchement du throttle (clé + nb d'échecs) |
| Tests | 4 nouveaux : 429 au 31e échec, reset au succès, isolation par client, throttle IP du garbage. Suite OIDC/auth : 121 passés (1 échec pré-existant `test_oidc_launch`, vérifié au stash) |
| Docs | `CAHIER_DES_CHARGES.md` + `.html` : ligne « Limitation de débit » mise à jour |

**Pourquoi pas de throttle par utilisateur** : à `/token`, la requête n'est pas authentifiée utilisateur — l'identité ne se matérialise qu'après présentation d'un code/refresh valide (donc après un succès légitime). Le « sujet » de cet endpoint est le client OAuth : c'est la bonne clé de throttle.

---

## [3.44.4] - 1 septembre 2026

### 🐛 Retrait du rate-limit IP sur `/api/oidc/token` (429 « Too Many Requests » en SSO)

| Correctif | Description |
|-----------|-------------|
| Bug utilisateur | Les utilisateurs recevaient `429 Too Many Requests` dès ~10 connexions Vikunja rapprochées : l'échange code→token est fait **server-to-server par le backend Vikunja** (une seule IP conteneur), donc le plafond de 10 req/min/IP était mutualisé par tous les utilisateurs |
| Correctif | Suppression du limiteur `_check_oidc_token_rate_limit` dans `routes/oidc.py` (consts, dict, appel) + fixture `conftest.py` mise à jour. Le check était placé **avant** l'authentification du client : même les échecs d'auth consommaient le quota |
| Sécurité | Inchangée : client confidentiel (`client_secret`), codes d'autorisation **one-shot** à durée courte, **PKCE**, session Hub obligatoire (accès hub-only). Le rate-limit 5/min/IP sur les endpoints de saisie de mot de passe (`/login`, `/recover/*`) est conservé |
| Tests | `TestTokenEndpointRateLimit` (429 attendu à la 11e requête) remplacé par `TestTokenEndpointNoRateLimit` (15 requêtes → jamais de 429) |
| Docs | `CAHIER_DES_CHARGES.md` + `.html` : section « Limitation de débit » mise à jour (principe expliqué) |

**Pourquoi pas de plafond IP sur le pont OIDC** : en architecture hub-only, le trafic OIDC légitime est concentré sur l'IP du backend de chaque app consommatrice ; tout plafond par IP punit mécaniquement les connexions simultanées d'utilisateurs différents. La protection anti brute-force repose sur le secret client + codes one-shot + PKCE, pas sur le débit IP.

---

## [3.44.3] - 31 août 2026

### 🧹 Hygiène : `secrets/hub_instance_id` retiré du dépôt

| Correctif | Description |
|-----------|-------------|
| Artefact runtime | `secrets/hub_instance_id` (UUID d'identité, unique par installation) ne doit jamais être versionné — retrait du dépôt + règle `.gitignore` dédiée. Non sensible (UUID public affiché par /api/health) mais sa présence casserait l'unicité par installation |

---

## [3.44.2] - 31 août 2026

### 🐛 Détection d'IP : renvoie l'IP LAN du serveur (pas l'IP Docker du conteneur)

| Fonctionnalité | Description |
|----------------|-------------|
| Détection host | `GET /api/config/network/detect-host-ip` (admin) : conteneur jetable en `--network host` (image alpine:3, pattern clone-db du workshop) + `ip -4 route get 8.8.8.8` avec iproute2 ajouté à la volée → renvoie l'IP LAN du serveur (ex. 192.0.2.10) alors que la socket UDP du conteneur ne voit que son IP Docker (10.42.0.x) |
| UI | Le bouton « Détecter » du modal d'IP appelle ce endpoint ; avertissement « IP Docker » retiré (obsoleted par la détection fiable) et ligne trompeuse « Auto-détectée » retirée de la carte |
| Parse | La sortie brute de `ip route get` est parsée côté Python (le champ `src` est en 7ᵉ position selon le format — plus d'awk positionnel fragile) |

---

## [3.44.1] - 31 août 2026

### 🐛 Badge de version navbar : rendu dynamique depuis VERSION

| Fonctionnalité | Description |
|----------------|-------------|
| Badge dynamique | La navbar affiche désormais la version servie par `/health` (fichier `VERSION`, source unique de vérité) — le texte statique de `index.html` redevient un fallback de build ; plus jamais un badge figé sur un déploiement qui ne passe pas par `sync_version.py` |
| sync_version.py durci | `NAVBAR_VERSION_RE` et `get_version_from_navbar` tolèrent désormais des attributs entre `class` et le contenu (ex. le nouvel `id="navbarVersionBadge"`) — l'ancien pattern perdait silencieusement le badge |

------

## [3.44.0] - 31 août 2026

### 🌐 Changement d'IP du Hub validé par l'admin — propagation aux applications

| Fonctionnalité | Description |
|----------------|-------------|
| État réseau | `GET /api/config/network` (admin) : IP effective, source (`admin DB` / `env HUB_HOST` / auto-détectée), IP auto-détectée, ports, `instance_id` |
| Changement d'IP | `PUT /api/config/network/hub-host` (admin) : validation IP RFC / hostname RFC 1123, persistance `app_settings["hub_host"]`, mutation à chaud du singleton settings (effet immédiat sur `/api/config/hub-host`, credentials M2M, OIDC, deploy) ; `DELETE` = retour env / auto-détection |
| Override boot | Le lifespan restaure la décision admin persistée — elle prime sur l'env `HUB_HOST` et sur l'auto-détection |
| Plan de propagation | `GET /api/config/network/propagation-plan` : apps v2 enregistrées (ateliers et apps système exclus et documentés), statut `up_to_date` / `pending` / `no_env` + IP `HUB_HOST` de leur `.env` |
| Application | `POST /api/config/network/propagation-plan/apply {slugs}` : la liste explicite EST la validation admin — réécriture `HUB_HOST` (variantes `HUB_URL`/`HUB_BASE_URL` incluses) via `apply_env_values`, reste du `.env` et `HUB_INSTANCE_ID` intacts |
| Recréation | `POST /api/apps/{slug}/recreate` : opération tracée (moteur workshop_operations) — `docker compose -p <slug> up -d --force-recreate` + health-check sur `API_PORT` (60 s) ; gardes 401 / 404 / 400 atelier & app système / 409 opération en cours |
| UI admin | Onglet « Réseau » : carte IP (badge source, modal Modifier avec détection et avertissement IP Docker en conteneur, retour auto-détection), plan de propagation (badges, cases, « Propager la sélection » avec confirmation modale), bouton « Recréer » par app avec polling, badge d'apps à propager |
| Sécurité | Chemin `.env` des apps toujours construit depuis le slug en DB (aucune traversal possible) ; `HUB_INSTANCE_ID` jamais touchée par la propagation — la garde sync-env reste effective |
| Tests | 31 nouveaux (`test_network_config`, `test_propagation_plan`, `test_app_recreate`), suite réseau complète 38 passed ; E2E live sur banc de coexistence (changement d'IP, restart, override boot, apply sélectif, recreate) |

---

## [3.43.0] - 31 août 2026

### 🔧 Gestion correcte des ports (diagnostic changement de port)

| Fonctionnalité | Description |
|----------------|-------------|
| Injection compose | `HUB_API_PORT`, `HUB_FRONTEND_PORT`, `APP_PORT_*`, `APP_SUBNET_POOL/SIZE`, `PORT_SCAN_DOCKER_ENABLED` injectés dans hub-api — le `.env` était ignoré pour l'allocation (defaults code 9000/4200/10.50 utilisés) |
| Settings | Nouveaux champs `hub_api_port`/`hub_frontend_port` ; `routes/apps.py` n'utilise plus de fallback/littéral `8000` (credentials M2M avec le vrai port) |
| Identité du hub | `instance_id` (UUID persistant dans `secrets/hub_instance_id`) exposée par `/api/health` et `/api/config/hub-host` — distingue deux hubs même-version (coexistence) |
| hub-host | Renvoie désormais `api_port` + `frontend_port` (consommé par sync-env des apps) |
| Makefile | `docker-compose.override.yml` rechargé par `make` (`-f` explicite le désactivait) |
| Hygiène | `.gitignore` couvre `.env.*` ; `COMPOSE_PROJECT_NAME` documenté (coexistence) |

---

## [3.42.0] - 31 août 2026

### ✨ Badge de version dans la navbar

| Fonctionnalité | Description |
|----------------|-------------|
| Badge navbar | Affichage de la version du Hub (`vX.Y.Z`) à côté de la brand dans la navbar (`<small class="navbar-version">`) |
| Style | `.navbar-version` dans `style.css` : petit corps (0.7rem), blanc translucide, sans retour à la ligne |
| `sync_version.py` | Le script maintient désormais le badge : nouveau pattern `NAVBAR_VERSION_RE`, mise à jour lors des `bump`/`set`, vérification `check_versions` (alerte si le badge diverge de `VERSION`), message d'avertissement si le badge est introuvable dans `index.html` |

---

## [3.41.0] - 31 août 2026

### 🔐 Export de backups chiffré par mot de passe + 409 sur double-import

| Fonctionnalité | Description |
|----------------|-------------|
| Export mot de passe (portable) | `POST /api/apps/{slug}/backup/{backup_id}/export` (remplace le GET) : chiffre l'export avec un mot de passe saisi (`password` + `password_confirm`, min. 8 caractères) au lieu de la clé RSA — format `.encrypted` v2 (AES-256-GCM + PBKDF2-HMAC-SHA256 600k) **portable** vers tout hub ; validation avant lecture fichier ; erreur 500 générique sans fuite d'interne |
| 409 sur double-import | Ré-importer un `.encrypted` dont le `backup_id` existe déjà renvoie **409** avec message clair (détection early alignée sur la contrainte d'unicité globale + filet IntegrityError avec rollback et purge du fichier orphelin) au lieu d'un 500 |
| UI export | Nouveau modal « Exporter chiffré par mot de passe » (mot de passe + confirmation) branché sur le POST, téléchargement du `.encrypted`, message rappelant de conserver le mot de passe |
| Compatibilité | L'import (`/import`, `restore/upload`) continue d'accepter les `.encrypted` v1 RSA (clé backup/JWT locale) et v2 mot de passe |
| Tests | `tests/test_app_backups_password.py` porté à 17 tests (export POST mot de passe, validations, 403 non-admin, double-import 409) |

---

## [3.40.0] - 31 août 2026

### 🔐 Import de backups externes chiffrés par mot de passe (migration)

| Fonctionnalité | Description |
|----------------|-------------|
| Déchiffrement format v2 | `decrypt_encrypted_package(package, password=None)` reconnaît `encryption_mode: "password"` : clé AES-256 dérivée via PBKDF2-HMAC-SHA256 (600 000 itérations, sel 16 B) ; chemin v1 RSA (clé backup + fallback JWT) inchangé ; checksum v2 (`checksum`) ou v1 (`original_checksum`) |
| Endpoint `POST /api/apps/{slug}/backup/import` | Admin-only (multipart) : déchiffre un `.encrypted` externe (v2 mot de passe ou v1 RSA) ou accepte un `.zip` brut ; `store_backup=true` enregistre le ZIP déchiffré comme backup de l'app (métadonnées extraites du ZIP, `app.last_backup_*` mis à jour) ; `restore_now=true` restaure immédiatement vers l'app (SA → token M2M → `POST {api_url}/api/admin/restore`) avec audit `log_restore_audit` ; réponse `BackupImportResponse` (`success`, `backup_stored`, `backup_id`, `decrypted_size_bytes`, `restore`) |
| restore/upload mot de passe | `POST /api/apps/{slug}/backup/restore/upload` accepte un champ `password` optionnel pour déchiffrer un `.encrypted` v2 |
| Garde-fous sécurité | `kdf_iterations` borné (1..600000) contre les DoS par header forgé ; mot de passe incorrect et fichier corrompu renvoient la même erreur générique `PASSWORD_DECRYPT_ERROR` (pas d'oracle) ; mot de passe jamais dans les logs, les URLs ni l'audit |
| UI import externe | Modal d'import renommé « Importer un backup externe » : champ mot de passe + interrupteurs « Conserver le backup déchiffré » (défaut coché) et « Restaurer immédiatement vers l'application » ; message de succès enrichi depuis `result.restore` |
| Schéma | Nouveau `BackupImportResponse` dans `schemas.py` |
| Tests | `tests/test_app_backups_password.py` (12 tests : roundtrip v2, erreurs génériques, bornes `kdf_iterations`, checksum, import store-only, restore/upload) |
| Usage | Migration app-par-app depuis le hub legacy : export chiffré par mot de passe sur l'ancien hub → transfert manuel du `.encrypted` → import ici avec le même mot de passe |

---

## [3.39.0] - 27 août 2026

### ✨ Configurer le dépôt git d'une app depuis l'UI

| Fonctionnalité | Description |
|----------------|-------------|
| Endpoint `git-config` | `POST /api/apps/{slug}/git-config` (admin) : enregistre l'URL en DB (`git_repo_url`), configure `origin` du repo local `APPS/X/`, puis **teste immédiatement** la connexion (`ls-remote`). Validation : **HTTPS/HTTP uniquement** (SSH `ssh://`/`git@`/`git://` refusé en 400), **409** si `APPS/X` n'est pas un repo git |
| Modale configurer-et-tester | Bouton **« Configurer le dépôt git »** contextuel dans la vue Santé (apps **sans remote**, avec ou sans atelier — débloque maj/ship) + modale pré-remplie depuis l'état, verdict du test affiché à l'enregistrement |
| Sync best-effort du form admin | Le PATCH admin (`update_app`) resynchronise le remote local quand `git_repo_url` est fourni — **jamais bloquant** : résultat dans `git_sync` de la réponse (`"ok"` ou `"warning: …"` si repo local absent/cassé) |
| PAT GitHub dans les fetch de l'atelier | **Corrige les repos privés** : `ls-remote` (test connexion), `git-config` et les `fetch` du cycle atelier (`--maj`/`--prod`) passent désormais par le PAT GitHub existant (`github_token` en DB settings — jamais affiché) au lieu d'échouer en auth sur un repo privé |
| Verdict d'échec lisible | Le verdict `ls-remote` en échec **conserve la cause git utile** (auth, repo introuvable, réseau) sans le préfixe technique interne |
| État atelier | `GET /api/apps/{slug}/workshop` expose `git_repo_url` (valeur DB, pré-remplissage de la modale) |
| Tests | **+15** (7 service `_configure_remote`/test connexion/PAT + 8 routes git-config/409/400/state/`git_sync`) — **848 passed au total** (dont 2 pré-existants marqués env) |

---

## [3.38.2] - 27 août 2026

### ⚡ Perf page Audit : heatmap quasi instantanée + plage limitée au mois

| Correctif | Description |
|-----------|-------------|
| Heatmap O(n) (`usage_stats.py`) | `compute_activity_heatmap` réécrit en **O(sessions + heures couvertes)** (marquage direct des buckets recouverts en microsecondes entières) au lieu de O(jours × sessions × 24) — mesuré sur la prod (~3 700 sessions) : **13,3 s → 5 ms** à 90 j, 4,3 s → 4 ms à 30 j. Équivalence stricte avec l'ancien algo garantie par un test de propriété (cas aléatoires + edge cases : minuit, durée nulle, session ouverte, à cheval sur la fenêtre, sous-secondes) |
| Hors event loop (`routes/audit.py`) | `/api/audit/usage/heatmap` exécute l'agrégation CPU-bound via `anyio.to_thread` (même pattern que `/usage/apps`) — l'API ne gèle plus pendant le calcul |
| Vue d'ensemble non bloquée (`audit-page.js`) | La heatmap est **réellement** non bloquante : KPIs/alertes/sessions live s'affichent immédiatement, le graphique arrive dès sa réponse (un `.catch` dans le `Promise.all` attendait la plus lente des requêtes) + garde anti-réponse-périmée si la période change entre-temps |
| Plage limitée à 30 j (`index.html`) | Bouton **90j retiré** du sélecteur de période (24h/7j/30j) — les agrégats au-delà du mois n'apportaient que des temps d'attente ; l'endpoint API reste inchangé (`days ≤ 90`) |
| Tests | +2 dans `test_usage_stats.py` : équivalence vs référence naïve (propriété) et garde-fou perf 90 j / 4 000 sessions < 1 s |

---

## [3.38.1] - 27 août 2026

### 🔧 Correctifs atelier (post-3.38.0, retour terrain canari)

- `workshop_service.update` : garde `MultipleResultsFound` (limit 1) sur la lecture
  de l'opération courante — cause racine d'un update affiché en double dans la modal,
  bilan des versions au ship, rollback de création, placeholder UI.
- Cartes santé : boutons **maj/ship désactivés** + note explicite quand l'app n'a pas
  de remote git (au lieu d'un échec au clic) — retour terrain canari.

## [3.38.0] - 27 août 2026

### ✨ Pilotage de l'atelier (workshop) depuis la vue Santé — parité hubctl v0.6.1

| Fonctionnalité | Description |
|----------------|-------------|
| Service `workshop_service.py` | Le **cycle complet de l'atelier** (créer clone DB / mettre à jour / déployer en prod / supprimer) est exécutable par `hub-api` lui-même, en parité comportementale avec `hubctl atelier` v0.6.1 : allocation ports + subnet, build/up du projet `-ws`, health check, rollbacks (update → rollback git, ship → rollback prod) |
| 6 endpoints admin | `GET /api/apps/{slug}/workshop` (état + contrat `.workshop.env` + git_ok), `POST .../workshop` (create), `POST .../workshop/update`, `POST .../workshop/ship`, `DELETE .../workshop` (remove), `GET .../workshop/status` (opération courante + historique). Erreurs typées : **409** (atelier déjà existant/absent, une opération à la fois, ship d'un ref ≠ main), **400** (app sans repo git/remote), **503** (pool de subnets épuisé) |
| Une opération à la fois | Pattern `deploy_service` : garde-fou global (toutes apps), exécution en tâche de fond sous lock, **suivi pas-à-pas en DB** (table `workshop_operations`, migration 057 : steps + logs + statut + déclencheur), **purge après 30 j** (`workshop_retention_days`), opérations en vol **annulées au shutdown** de l'API |
| Dualité UI/CLI | `.workshop.env` (7 clés) = **contrat partagé** entre hubctl et `workshop_service`, figé par des **contract tests des deux côtés** (hubctl reste l'outil de secours HUB-down). Warning journalisé si `.workshop.env` a moins de 60 s (mtime) — pas de lockfile inter-outils en v1 : ne pas lancer hubctl et l'UI simultanément sur la même app |
| Garde-fous ship identiques | `origin/main` **uniquement** (409 si l'atelier teste un autre ref — mergez via release-on-main d'abord), commit testé exigé (refus si main a avancé), **atelier conservé** en cas d'échec du déploiement prod (diagnostic) |
| Allocation ressources | Mêmes plages que hubctl : API `[10000-10499]`, frontend `[5200-5699]`, subnet `/24` du pool `10.210.0.0/24`..`10.210.63.0/24` avec overlap-check des réseaux docker existants et des autres ateliers |

### 🎨 Bloc atelier dans la vue « Santé des Applications » (admin)

| Fonctionnalité | Description |
|----------------|-------------|
| Bloc atelier sur les cartes santé | État de l'atelier (ref + commit testés, ports, subnet) + les 4 actions (créer / mettre à jour / déployer / supprimer) avec confirmations explicites — visible uniquement par les admins |
| Modal de progression | Polling 2 s de `.../workshop/status` (pattern admin-app-deploy) : étape courante + logs en direct pour les 4 opérations ; garde anti-timer-zombie à la fermeture (✕) et tolérance de 5 erreurs de poll transitoires |

---

## [3.37.1] - 25 août 2026

### 📖 Cahier des charges mis à jour à l'état de l'art v3.37.0

| Fonctionnalité | Description |
|----------------|-------------|
| Version documentée | `CAHIER_DES_CHARGES.md` passé de v3.29.0 à **v3.37.0** (état de l'art) |
| Nouveaux modules | §7.7 Déploiement automatique des applications, §8.4 Suivi d'usage et présence (uptime) |
| Mises à jour | §7.1 override des rôles de profils, §7.2 groupes d'applications + sous-réseaux Docker, §7.6 snapshots incluant le code source, §9.2 self-healing M2M, Annexe A +3 domaines |
| Chiffres clés | Tests ≈630→726 (78 fichiers), tables 32→34, domaines 19→22 |
| Livrables | Rebuild `CAHIER_DES_CHARGES.html` (autonome) |

---

## [3.37.0] - 23 août 2026

### ✨ Traçage SSO OIDC dans l'uptime

| Fonctionnalité | Description |
|-------------|-------------|
| Helper `ensure_session_presence` | Heartbeat + segment d'usage ; recrée une session si aucune n'est ouverte (utilisateur dont la session HUB a expiré mais qui passe le SSO) |
| `/oidc/authorize` tracé | Le passage SSO maintient la session et ouvre le segment d'usage de l'app liée au client OIDC (ex : Vikunja) |
| `/api/oidc/launch/{client_id}` tracé | Le lancement d'une app SSO = activité sur cette app (session maintenue + segment ouvert) |

**Limite assumée** : trace le *passage* SSO, pas l'activité continue dans l'app tierce. Les apps issues du template (≥ 2.17.0) bénéficient quant à elles de la présence continue via SSE (v3.36.0).

### 🎨 Graphiques uptime adaptés à l'usage parallèle multi-apps

| Fonctionnalité | Description |
|-------------|-------------|
| « Activité de la journée » en heatmap | Remplace la timeline à rectangles : **lignes = applications** (nom en étiquette, identification structurelle indépendante des couleurs), **colonnes = 8 tranches de 3 h**, intensité de la cellule = proportion de présence dans la tranche (opacité de la couleur de l'app), cellule inactive grisée. Tooltip = durée exacte + % de la tranche. Les timestamps naïfs du backend sont désormais interprétés en **UTC explicite** (cohérent avec le badge UTC de la page — l'ancien rendu les lisait en heure locale du navigateur) |
| Répartition multi-lignes | La « Répartition du temps » passe d'une barre empilée unique à **une barre horizontale par application** (nom sur l'axe, durée en bout, tooltip avec % du total et % de la journée — l'usage parallèle de plusieurs apps pouvant dépasser le temps réel) |
| Différenciation des couleurs dupliquées | Sur la répartition : les apps partageant la même couleur reçoivent un **motif decal** (points, tirets, hachures — contraste calculé selon la luminance) ; la 1re occurrence d'un groupe reste pleine. La heatmap n'en dépend plus (nom de ligne structurel) |
| KPI renommé | « Bascules » → « Segments » (avec les segments parallèles par app, ce ne sont plus des bascules) |

---

## [3.36.0] - 23 août 2026

### ✨ Présence utilisateur par application via SSE (uptime)

| Fonctionnalité | Description |
|-------------|-------------|
| Segments d'usage parallèles | `touch_app_presence` remplace `record_app_switch` : un segment par couple (session, app), plusieurs apps utilisables simultanément (multi-onglets) sans se fermer l'une l'autre. La raison `switch` n'est plus écrite (reste lisible sur l'historique) |
| Param `?app=<slug>` sur `/api/events/stream` | Le heartbeat SSE (15 s) propage le slug de l'app → le segment d'usage s'ouvre et reste vivant tant que l'onglet de l'app garde sa SSE ouverte. Slug validé (app existante + active), sinon ignoré avec warning — jamais d'échec de stream. Rétrocompatible : param optionnel, ignoré par les HUB anciens côté apps |
| Fermeture ciblée `disconnect` | À la coupure de la dernière SSE d'un couple (user, app), SON segment se ferme (raison `disconnect`) après un délai de reconnexion de 5 s — sans toucher aux segments des autres apps ni à la session utilisateur |
| Timeline uptime multi-rangées | Une rangée par application dans la timeline du jour (hauteur dynamique, `animation: false` pour la stabilité) : les usages parallèles ne se chevauchent plus |

**Sémantique** : `day_total_seconds` = somme des temps par app (l'usage parallèle de 2 apps compte 2×). **Côté apps** : template 2.17.0 — l'URL SSE construite par les apps porte `&app=<slug>` (`VITE_APP_SLUG`).

---

## [3.35.1] - 23 août 2026

### 🐛 Stabilisation du suivi d'uptime utilisateur (sessions/segments)

| Correctif | Description |
|-----------|-------------|
| `limit(1)` sur requêtes de session active | `MultipleResultsFound` silencieux dès 2 sessions ouvertes (heartbeats perdus, launch app en 500) — `end_session` et `update_session_activity` ciblent désormais toujours la plus récente |
| Commit de la fermeture SSE | `handle_user_disconnect` ne committait pas : fermetures `sse_disconnect` perdues → sessions zombies. Vérifié E2E : la fermeture est désormais persistée ~5 s après la coupure du flux |
| Heartbeat à cadence fixe | Le ping 15 s n'était émis qu'en cas de silence SSE : `last_activity_at` non rafraîchi sous trafic continu → sessions d'utilisateurs actifs fermées en "timeout". Le heartbeat DB bat désormais à cadence fixe quel que soit le trafic (`HEARTBEAT_INTERVAL_S = 15.0`, `_emit_heartbeat`) |
| Clamp des `logout_at` | Fin virtuelle `last_activity + 20 min` clampée à `now()` dans `start_session` : plus de fermetures dans le futur (session #409 en prod) |
| Refresh recrée la session | Race SSE (fermeture à tort) → trou d'uptime permanent réparé au refresh (< 30 min) : un refresh token valide recrée la session ouverte |
| Cleanup au boot + dédoublonnage | Nettoyage des sessions toutes les 2 min dès le démarrage (plus conditionné aux clients SSE — fini les sessions zombies post-crash) ; au plus 1 session ouverte par utilisateur restaurée à chaque cycle |
| UI uptime | Toast d'erreur au chargement de la liste users (avant : page vide muette) + polling 60 s de secours quand l'onglet Uptime est visible |

**Infra de tests** : fixture autouse `_patch_session_maker` (conftest) redirigeant `async_session_maker` vers la DB de test — a exposé et corrigé un test backup qui passait pour la mauvaise raison (écritures en vraie DB pendant les tests) ; fix d'une race pré-existante du harness SSE.

---

## [3.35.0] - 23 août 2026

### ✨ Refonte UI de la vue Préférences (visible par tous les utilisateurs)

| Fonctionnalité | Description |
|----------------|-------------|
| **Composants de sélection à seuil auto** | Catégories ≤ 12 valeurs : chips cliquables (surface entière, état actif plein bleu, coche, inputs natifs masqués → clavier et lecteurs d'écran natifs). Catégories > 12 valeurs (ex: 50+ villes) : sélecteur avec recherche instantanée insensible à la casse/accents (normalisation NFD), chips de sélection retitrables (✕), compteur « X/Y sélectionnés » et « X résultats sur Y », « Tout effacer » (multi), liste scrollable 240px. |
| **Autosave** | Fini le bouton « Enregistrer mes choix » sur la page : chaque interaction est sauvegardée (debounce 600 ms, objet complet envoyé à `PUT /api/tags/my`, dernière écriture gagnante) avec indicateur d'état discret dans l'en-tête (« Enregistrement… » / « ✓ Enregistré » / « ⚠ Échec » + toast). Le popup d'onboarding garde son bouton (sémantique d'action délibérée + acquittement des catégories). |
| **Structure page** | Sommaire sticky à gauche (clic = scroll doux, pastille remplie quand la catégorie a un choix, section active surlignée via IntersectionObserver), cartes par catégorie à droite. Sous `lg` le sommaire disparaît (scroll naturel). |
| **Onglets admin-only** | Les non-admin ne voient plus la barre d'onglets orpheline : contenu « Mes préférences » direct, titre neutre (« Préférences » / « chaque changement est enregistré automatiquement »). Admin : 3 onglets inchangés, « Mes préférences » = nouveau layout. |
| **Fix toggle chips** | Le délégué global `data-action` (app.js) fait `preventDefault()` sur tout clic `[data-action]` : posé sur les inputs checkbox/radio, il annulait le toggle natif (sélection figée). Les inputs restent natifs, la synchro passe par un listener `change` dédié dans `user-tags.js`. |
| **Fix SSE `tags_updated` bruyant** | Chaque autosave émet `tags_updated` : le handler SSE déclenchait un spinner navbar « Sync... » (`refreshTokenAfterAppChange` non silencieux) + re-render complet de la page (scroll/recherche/focus écrasés). Le refresh token reste (les tags sont dans les claims du JWT, `auth.py`) mais devient **silencieux** ; le re-render ne se fait plus que sur les actions `category_*` (création/modif/suppression/import), plus sur les écritures de l'utilisateur lui-même. |
| **Fix création catégorie 422** | Régression préexistante (`211d622`) : `TagCategoryCreate.m2m_endpoint` est `str` non-Optional côté backend, mais le frontend envoyait toujours `m2m_endpoint: null` pour les catégories CSV → 422 systématique à la création manuelle. La clé est désormais omise quand nulle (le backend applique son default). |
| **Cohérence** | Popup d'onboarding et modale admin « préférences d'un utilisateur » utilisent les mêmes composants (chips/sélecteur) via `buildTagsFormHTML` (wrapper sur `buildPrefFieldHTML`). Aucun changement backend. |

---

## [3.34.3] - 23 août 2026

### ⚡ Régression opérations en masse + complétion de la migration CSP (data-action)

| Fonctionnalité | Description |
|----------------|-------------|
| **Fix bulk users** | Les 5 opérations en masse (assigner rôles, retirer accès, créer, import CSV, supprimer) étaient inutilisables depuis `1f18ceba` (27/06) : `data-args` avec apostrophes résiduelles (`"'assign-roles'"` → aucun `case` du switch), attribut fusionné `disableddata-action` (bouton « Analyser » CSV mort) et handlers inline générés dynamiquement bloqués par la CSP. Les 2 liens audit (`'errors'`/`'sessions'`) touchés par la même coquille sont corrigés. |
| **Migration CSP complétée** | **156 handlers inline** restants (15 `index.html` + 141 dans 24 modules) migrés vers la délégation `data-action` — tous étaient morts depuis le retrait de `'unsafe-inline'` (dashboard filtres workshop, bloc GitHub, modals tags, paginations audit, messages, santé apps, VPN peers, etc.). **176 attributs collés** `"data-action` normalisés, escape-hatch `onChange`/`new Function` (bloqué sans `unsafe-eval`) remplacé par `onBackupCronPresetChange`. |
| **Délégateur étendu** | `parseArgs` supporte les attributs positionnels `data-arg1..9` (valeurs brutes sans split ni éval, keywords `$el`/`event`) — obligatoire pour les contenus dynamiques contenant `|` ou quotes. Répare au passage des bugs préexistants : usernames/noms contenant `'` cassaient les anciens `onclick` (entités HTML décodées avant exécution). |
| **Fix coercions d'ids** | Les ids passés via `data-arg` arrivent en chaîne : **16 récepteurs** comparant en strict `===` (`editUserPage`, `editApp`, `editAppGroup`, `editOidcClient`, `editServiceAccount`, `editUser`, `showProfileModal`, `showApplyProfileModal`, `showBackupClusterModal`, `deleteBackupCluster`, `triggerBackupCluster`, `toggleFavorite`, `openAppGroupModal`, `loadTemplate`, `selectInboxMessage`, `confirmDeleteAppGroup`) font désormais `parseInt(…, 10)` en entrée — boutons « Modifier » muets sinon. |
| **UX backup apps lien only** | `is_unmanaged` exposé dans `AppBackupStatusResponse` (overview) et `AppBackupListResponse` ; l'UI Sauvegardes affiche un badge « Lien only », désactive « Déclencher » et masque « Configurer » pour ces apps (le backend 400 reste le garde-fou). Avant : bouton actif → 400 « application non gérée » pour les apps lien only (ex. vikunja). |
| **Pages autonomes** | Page d'erreur de lancement d'app (`document.write`) : « Ouvrir le Hub » devient un `<a href>` (zéro JS) et « Fermer » passe par le nouveau `js/window-close.js` self-hosted — les handlers inline y étaient morts (CSP héritée). Bouton « Déconnecté » (init.js) idem via `closeWindow`. |
| **Divers** | Bouton mort `data-action="handleAddPeer"` retiré (modal VPN ouvert par Bootstrap seul) ; sélecteur interne `closest('[onclick=…]')` du picker d'icônes migré ; wrapper `openNotificationAction` (le `preventDefault` du délégateur bloquait les `<a>` d'action notifications) ; `noop` (remplace `event.stopPropagation()` inline). |

---

## [3.34.2] - 22 août 2026

### ⚡ Self-healing M2M — ré-enregistrement trusted fiable

| Fonctionnalité | Description |
|----------------|-------------|
| **Sélection du SA propriétaire** | `POST /api/apps/register` (branche ré-enregistrement) sélectionne désormais le SA *propriétaire* via la convention nominale de `delete_app_cascade` (helper partagé `is_owner_service_account`). Corrige un bug préexistant : une app partagée rotationnait le secret du premier SA venu de la relation N-N — c'est-à-dire le SA d'une *autre* app (cas réel : exemple-app-7 a reçu le SA de Philia Proxy, invalidant son secret). |
| **Recréation du SA disparu** | Une app existante dont le SA a été supprimé en cascade (ex. `delete_app` d'une inscription doublon) retrouve un SA dédié recréé automatiquement au ré-enregistrement (actif, lié à l'app + shared services, parité atelier), tracé en audit `m2m_sa_recreated`. Elle n'est plus muette à vie. |
| **Sécurité inchangée** | `regenerate-secret` sans token M2M reste 401 (H9) ; garde `is_docker_network_ip` sur `/api/apps/register` inchangée ; le secret n'est jamais loggé. |

---

## [3.34.1] - 21 août 2026

### ⚡ Stabilité SSE — heartbeat accéléré + frontend temps réel allégé

- **Corrigé (SSE)** : heartbeat serveur 30 s → **15 s** (`events.py`, `asyncio.wait_for(queue.get())`). Firefox coupe une connexion SSE active ~25-29 s après le dernier octet reçu — avec un ping à 30 s, chaque ping arrivait trop tard et la connexion mourait entre deux pings (déconnexions toutes les ~55 s en boucle, reconnexion automatique 5 s plus tard). Reproduit en labo (Firefox headless profil vierge, indépendant de nginx/hub-api — curl stable) et bisectionné : ping 10 s et 20 s survivent, 30 s meurt. Coût : ~24 octets tous les 15 s par client.
- **Ajouté (test)** : `hub/tests/test_api_events_sse.py` — régression sur la cadence du premier ping (< 22 s). Pilote l'app en **ASGI brut** : le `receive()` d'httpx ASGITransport renvoie `http.disconnect` dès la fin du body d'un GET, faux positif qui tuait artificiellement le générateur via `is_disconnected()` (piège documenté dans le docstring du test).
- **Optimisé** : polling de badges 30 s en **repli uniquement pendant les coupures SSE** (activé par `onerror`, coupé par `onopen`, tick ignoré si `readyState === OPEN`). En régime nominal, fini les rafales permanentes de 5 XHR toutes les 30 s et leurs écritures audit en cascade.
- **Corrigé** : suppression de la **double reconnexion** post-coupure — `refreshAfterReconnect()` rouvrait systématiquement la connexion via `initEventSource(false)`, tuant la connexion saine qui venait de s'ouvrir (connexion fantôme de ~50 ms dans les logs). Le beacon_token est déjà renouvelé avant reconnexion par le handler `onerror` ; il est simplement mis en cache pour la prochaine fois.
- **Corrigé (UI)** : `admin-user-tags.js` — suppression de la référence à `tagFormM2mEndpoint`, champ disparu du modal lors du passage au sélecteur table+colonne (8849ada) ; il provoquait un TypeError au clic « Nouvelle/préférence existante ». Crosscheck effectué : plus aucun `getElementById` du fichier ne référence un ID absent d'`index.html`.

---

## [3.34.0] - 18 août 2026

### 🔓 Override des rôles dérivés des profils

- **Ajouté** : les admins peuvent modifier le niveau de droits d'une app couverte par un profil (override) — auparavant verrouillé côté UI. L'override est matérialisé par `user_app_roles.source = "override:<profile_id>"`, survit à la synchronisation du profil jusqu'à réinitialisation explicite, et disparaît quand son profil d'origine ne couvre plus l'app (recréation du rôle attendu si un autre profil couvre l'app).
- **Ajouté** : visibilité des droits modifiés — filtre « Droits modifiés » dans la liste utilisateurs (`?app_role_override=`), badge « Modifié » sur les lignes concernées (`has_role_overrides`), marqueur par app « rôle · modifié (profil : X) » dans la ligne extensible, et bouton « Réinitialiser selon profil » dans la modal utilisateur.
- **API** : `UserAppRoleResponse` enrichi (`profile_role`, `overridden`) ; `POST /api/users/{id}/apps` et `POST /api/users/bulk/roles` acceptent `reset` par assignment ; endpoint `GET /api/users/{id}/apps` expose `profile_role`/`overridden`.
- **Corrigé** : le filtre obsolète `source == "profile"` (valeur morte depuis la migration 011, réelle `profile:<id>`) ne matche jamais — un appel API direct pouvait créer une ligne `manual` dupliquée sur une app profilée. Remplacement par une logique upsert + contrainte d'unicité.
- **Migration 055** : contrainte unique `uq_user_app_roles_user_app` sur `user_app_roles(user_id, app_id)` + dédoublonnage des lignes existantes (priorité à la ligne profil).

---

## [3.33.2] - 18 août 2026

### ⚡ Déploiement auto — schéma HTTP du HUB forcé

- **Corrigé** : certaines apps (cas exemple-app) joignent l'API du HUB en `https://…:8000` alors que ce port est servi en HTTP (SSL : WRONG_VERSION_NUMBER → JWKS inaccessible → 503 « Service d'authentification non disponible »). Le pipeline injecte désormais `HUB_HTTPS_ENABLED=false` dans le `.env` généré (remplacement si présent + ajout sinon : « le HUB décide, l'app suit »).

---

## [3.33.1] - 18 août 2026

### ⚡ Déploiement auto — table admin corrigée

- **Corrigé** : le badge « Non déployée » et le bouton Déployer n'apparaissaient pas dans la table admin : `GET /api/apps/all` (serializer `AppWithUsersAndDocker`) n'exposait pas `git_repo_url` ni `last_deployment`, contrairement à `GET /api/apps/` (dashboard). Les deux champs sont ajoutés au serializer, et le chargement des derniers déploiements est factorisé (`_load_last_deployments`).

---

## [3.33.0] - 18 août 2026

### 🚀 Déploiement automatique des applications (spec 2026-08-18)

- **Ajouté** : `POST /api/apps/{slug}/deploy` (admin) déclenche le déploiement complet d'une app depuis son `git_repo_url` : clone/pull → création/injection du `.env` (slug, ports, HUB_HOST, secrets générés à la création) → `make build && make up` (fallback `docker compose build/up -d` sans Makefile) → health check. Non-bloquant, 1 déploiement à la fois (lock global), PAT GitHub transmis uniquement en mémoire (`http.extraheader`).
- **Ajouté** : `GET /api/apps/{slug}/deploy/status` (admin) — statut courant, étape, logs (cappés 100 Ko), historique (max 10). Table `app_deployments` (migration 056), persistance inter-restarts, annulation propre au shutdown du HUB.
- **Ajouté** : champ `last_deployment` dans `GET /api/apps` (dernier déploiement success/failed par app).
- **Ajouté** : image hub-api enrichie de `git`, `make`, CLI docker + compose v2 (binaires statiques pinés) ; montage `APPS/` au même chemin absolu hôte/conteneur (`APPS_HOST_PATH`) pour la résolution des bind mounts par le daemon. `cap_drop: ALL` et socket `:ro` inchangés.
- **Modifié (UI)** : après validation du formulaire d'ajout d'app avec URL Git, le déploiement se lance automatiquement et une modal « Déploiement » suit les 5 étapes en temps réel (polling 2 s, logs, bouton Réessayer). Le banner « 📋 Copier le .env » est supprimé (relique du flux manuel). Badge « Non déployée » + bouton fusée sur les apps avec `git_repo_url` sans déploiement réussi (cas exemple-app).
- **Corrigé** : `test_api_health_alias_returns_health_payload` attendait un payload sans `version` (test préexistant cassé sur main depuis l'ajout du champ au health).

### 🔧 Technique

| Composant | Détail |
|-----------|--------|
| `hub/app/deploy_service.py` | Worker asyncio + helpers `.env` (variantes `API_PORT`/`APP_PORT`/`APP_PORT_API`, `HUB_HOST`/`HUB_URL`, `APP_HOST`/`SERVER_IP`/`HOST_IP`, reconstruction `DATABASE_URL`) |
| `hub/app/routes/app_deploy.py` | Endpoints admin-only (404 app inconnue, 400 sans repo, 409 conflit) |
| `hub/migrations/056_create_app_deployments.sql` | Table + index (SQL maison, runner `apply_migrations`) |
| `frontend/public/js/modules/admin-app-deploy.js` | Modal de suivi temps réel (pattern `dockerInfoModal`, polling 2 s triple-cleanup) |

---

## [3.32.3] - 17 août 2026

### ⚡ Auth — réponse login fidèle à la base

- **Corrigé** : `POST /api/auth/login` renvoyait `must_change_password: false` (défaut du schéma `Token`) même quand la base exigeait un changement de mot de passe. Le champ est désormais renseigné depuis l'utilisateur réel. Le frontend contournait via `/me`, mais tout consommateur API direct recevait une information erronée. Constaté en test fresh-install Debian 12.

### 📖 Docs — guide installation

- **Corrigé** : `docker compose restart hub-api` ne recharge pas le `.env` (il redémarre le conteneur avec sa config de création) — remplacé par `docker compose up -d hub-api` dans la section CORS.
- **Ajouté** : troubleshooting `403 allocate-ports / auto-enregistrement` (`APP_WHITELIST_NETWORKS`) et note HTTPS app (port frontend+1 muet sans certificat PKI).

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.32.2] - 16 août 2026

### ⚡ Fix tooltip « Répartition du temps par application » (onglet Uptime)

- **Fix** (`audit-charts.js`, `renderUserDayBreakdown`) : au survol/clic d'un segment de la barre empilée, le tooltip affichait ` — 20min (undefined%)` sans nom d'application. Cause racine (vérifiée en reproduction headless sur la page réelle) : le formatter utilisait `params.name` — toujours vide pour des données scalaires sur un axe de catégorie masqué — et `params.percent` — propriété réservée aux séries pie, inexistante pour les barres. Correctif : nom via `params.seriesName` (déjà alimenté par le nom de l'app), pourcentage calculé sur `day_total_seconds`. Vérifié : « Test Template — 1h20 (100%) » au survol et au clic ; pas de régression sur la timeline (tooltip inchangé).

---

## [3.32.1] - 16 août 2026

### ⚡ Health monitor : fin du bruit 404 auto-infligé dans le journal d'audit

- **Fix** (`routes/apps_health.py`) : le moniteur de supervision (`GET /api/apps/health`) sondait `/api/health/detailed` sur **toutes** les apps online, y compris l'app système `hub` elle-même (`api_url = http://hub-api:8000`) — or le HUB n'expose pas cet endpoint. Résultat : un faux 404 (`IP 10.42.0.3` = hub-api) journalisé dans l'audit à **chaque affichage** de la page de supervision (reproduit : 1 entrée 404 par appel monitor).
- La sonde Phase 2 est désormais sautée pour le HUB (`if status == "online" and not is_hub:`) ; l'enrichissement local du bloc `is_hub` existant (version + uptime via `/proc/self/stat`, sans HTTP) reste inchangé — aucune perte de données dans la UI de supervision.
- Même famille que le fix 3.29.1 (bruit SSE 401) : les sondes internes attendues ne doivent pas polluer le journal d'erreurs.

---

## [3.32.0] - 16 août 2026

### ⚡ Snapshots complets du HUB : code des APPS inclus + runbook PRA bare-metal

- **Fix** (`routes/snapshots.py`) : le scheduler de snapshots planifiés ne se déclenchait jamais — `croniter(...).get_next()` renvoie une date strictement future, donc `next_run <= now` était toujours faux. Remplacement par `snapshot_cron_due()` (fonction pure basée sur `get_prev` vs dernière occurrence traitée) + garde anti-doublon si un snapshot tourne déjà. 8 tests de régression dans `test_snapshot_scheduler.py`.
- **DB** : migration `053_add_snapshot_apps_code.sql` — colonne `hub_snapshots.include_apps_code BOOLEAN NOT NULL DEFAULT TRUE`.
- **Snapshot** (`snapshot_collectors.py`, `snapshot_engine.py`) : nouveau collector `run_apps_code_tar` qui capture le code + config + `.git` de tout `APPS/` dans `apps/apps_code.tar`, avec exclusions des artefacts régénérables (`node_modules`, `__pycache__`, `.opencode`, `.worktrees`, `dist`, …) ; nouvelle étape de progression `apps_code` à 30 % ; entrée `collectors.apps_code` dans le manifest. Tolérance de `tar` exit 1 (fichier modifié pendant la lecture) pour ne pas faire échouer un snapshot nocturne sur un arbre de travail vivant.
- **Restore** (`restore_snapshot.py`) : extraction automatique de `apps/apps_code.tar` vers `/code/apps_source` (bind mount `../APPS`) au redémarrage PRA ; dégradation gracieuse si le montage est absent.
- **Compose** : montage lecture-écriture `../APPS:/code/apps_source:rw` dans le service `hub-api`.
- **Docs** : refonte de la section « Restauration bare-metal » de `BACKUP.md` avec la procédure semi-automatique complète (prérequis clé RSA, INSERT ligne `hub_snapshots`, `pending_restore.txt`, réinjection des données d'app via `POST /api/apps/{slug}/backup/{backup_id}/restore`) et les limites connues.
- **Tests** : `test_snapshot_collectors.py` (3), `test_restore_snapshot.py` (3), +1 test d'intégration moteur dans `test_snapshots.py`, +8 tests scheduler.

### ⚡ Allocation de sous-réseaux Docker par application

- **DB** : migration `054_add_port_allocations_subnet.sql` — colonne `port_allocations.subnet VARCHAR(30) NULL` ; remplissage paresseux des allocations existantes au prochain appel.
- **Config** (`.env.example`, `app/config.py`) : variables `APP_SUBNET_POOL` (défaut `10.50.0.0/16`) et `APP_SUBNET_SIZE` (défaut `24`).
- **API** (`port_allocator.py`) : `pick_free_subnet()` itère les sous-réseaux du pool et retourne le premier /24 non chevauchant, `SubnetPoolExhausted` ; `allocate_ports_for_app` attribue un subnet par app et remplit paresseusement les allocations antérieures.
- **Routes** (`routes/apps.py`, `routes/github_catalog.py`) : retour du champ `subnet` dans `AllocatePortsResponse` et `GithubRepoPrepareResponse`, gestion de `SubnetPoolExhausted` en `409 CONFLICT`.
- **Modèles / schémas** : champ `subnet` sur `PortAllocation` et dans les schémas `AllocatePortsResponse`, `PortAllocationItem`, `PortsStatusResponse`, `GithubRepoPrepareResponse`.

---

## [3.31.1] - 15 août 2026

### ⚡ Perf des agrégats usage 90j + filtre app sur /usage/apps

- **Perf** (`usage_stats.py`) : agrégats sur longue période (90 j) passés en O(n) par segment (plus de re-scan par jour), moyenne hoistée hors de la boucle des buckets — les graphiques 90 j de la vue « Par Application » ne bloquent plus l'event loop.
- **Perf** (`routes/audit.py`) : l'agrégation CPU-bound de `/usage/apps` est exécutée dans un thread (`anyio.to_thread`), les segments ouverts (clôture virtuelle) sont filtrés en une seule passe.
- **Filtre** : `/api/audit/usage/apps?days=N` accepte `&app=<slug>` pour restreindre les stats à une application (défaut : toutes) ; `api.js` → `getAppsUsageStats(days, appSlug)`.
- **Frontend** : purge de l'état vide superposé (`.usage-empty`) avant re-rendu du conteneur de chart.
- **Tests** : +2 (`test_usage_stats.py` perf/O(n), `test_api_usage_endpoints.py` filtre app).
- **Docs** : spec `2026-08-15-snapshot-apps-code-design.md` (design snapshot du code des APPS + runbook PRA bare-metal).

---

## [3.31.0] - 15 août 2026

### ⚡ Visualisations de la page Audit (uptime & stats par application)

- **DB** : migration `052_create_app_usage_segments.sql` — table `app_usage_segments` (segments d'usage par app : `session_id`, `user_id`, `app_slug`, `started_at`, `ended_at`, `end_reason` ; index composites `(user_id, started_at)` et `(app_slug, started_at)`). Pas de backfill : les données démarrent au déploiement.
- **Tracking** : nouveau module `hub/app/usage_tracking.py` branché sur le cycle de vie des sessions — bascule d'app (`update_session_activity`) → clôture `switch` + ouverture ; logout (`end_session`) → `logout` ; timeout (`cleanup_stale_sessions`) → `timeout` ; révocation admin → `forced`. Invariant : un seul segment ouvert par session, clamp défensif des bornes.
- **Agrégats** : `hub/app/usage_stats.py` — fonctions pures Python (simultanés par bucket 5 min, records max/avg/min/p95/σ, uniques/jour, heatmap jour×heure). Calcul applicatif (et non SQL PostgreSQL) pour rester testable sur SQLite.
- **API** (admin, `routes/audit.py`) : `GET /api/audit/usage/user/{id}?date=YYYY-MM-DD` (segments du jour clôturés virtuellement à `last_activity_at`), `GET /api/audit/usage/apps?days=N` (série simultanés downsampleée 5/30/120/360 min selon période + records sur la base 5 min + uniques/jour), `GET /api/audit/usage/heatmap?days=N` (sessions actives par jour×heure UTC).
- **Frontend** : ECharts 5.5.1 (CDN) + module `audit-charts.js` (registre d'instances avec `dispose` anti-fuite, resize auto). Nouvel onglet **« Uptime utilisateurs »** (sélecteur utilisateur, navigation jour ‹ ›, timeline 00h→24h colorée par app + répartition empilée). **Vue d'ensemble** : heatmap d'activité du parc. **Par Application** : graphique mixte simultanés (courbe) + uniques/jour (barres) et cartes records (pic+date+users, moyenne, P95, min/σ). Rafraîchissement SSE existant conservé.
- Le sélecteur d'utilisateurs de l'onglet Uptime charge tous les utilisateurs (page=0).
- **Tests** : `test_usage_segments.py` (persistance + hooks cycle de vie, 7), `test_usage_stats.py` (agrégats purs, 9), `test_api_usage_endpoints.py` (contrats API, 403/404/400, segment ouvert visible, 6) — 22 nouveaux tests.

---

## [3.30.0] - 15 août 2026

### ⚡ Groupes d'applications (regroupement visuel du dashboard)

- **DB** : migration `050_app_groups.sql` — table `app_groups` (name unique, description, icône Bootstrap Icons, couleur hex) + colonne `applications.group_id` (FK `ON DELETE SET NULL`, index). Supprimer un groupe dégage simplement ses apps.
- **Modèles** : `AppGroup` + `Application.group_id` (`models.py`).
- **API** : nouveau routeur `hub/app/routes/app_groups.py` — CRUD admin `GET/POST/PATCH/DELETE /api/app-groups` (409 nom dupliqué, 404 inconnu, dégrouppage explicite à la suppression). Enregistré dans `routes/__init__.py` + `main.py`.
- **API apps** : `GET /api/apps/` hydrate `group` (`{id, name, icon, color}`) sur chaque app (une requête de préchargement, pas de N+1) ; `POST/PATCH /api/apps/` acceptent `group_id` (validé, 404 si inconnu) et `category` à la création. `AppWithUsersAndDocker` expose désormais `category` + `group_id`.
- **Dashboard** : sans filtre actif, la grille affiche des tuiles de groupes (tri alphabétique, badge nombre d'apps, style `.app-card` réutilisé) puis les apps hors groupe ; le clic ouvre une modale `#appGroupModal` listant les apps accessibles du groupe (lancement via `openApp()` existant). Un groupe vide pour l'utilisateur est masqué (filtrage dynamique côté payload). Dès qu'une recherche ou un filtre (favoris/atelier) est actif : grille plate avec badge du groupe sur les tuiles. La recherche matche le nom de l'app, sa description, son slug, sa catégorie et le nom du groupe.
- **Dashboard (filtres)** : suppression des boutons de filtre par catégorie (`#categoryFilters`) pour ne plus mélanger les filtres. Les boutons **Toutes / Favoris / Prod / Atelier** conservent leur comportement initial ; les catégories restent en mini-badge sur les tuiles et se recherchent via la barre de recherche (placeholder mis à jour : « Rechercher une application ou une catégorie… »).
- **Admin** : onglet « Groupes » (table + CRUD via modale `#appGroupFormModal`, suppression confirmée par modale dédiée). Formulaire app : nouveau select « Groupe » et champ « Catégorie » (texte libre + datalist des catégories existantes). `api.js` : 4 méthodes CRUD app-groups.
- **Tests** : `hub/tests/test_api_app_groups.py` — 11 tests (CRUD, 409/404/403, dégrouppage à la suppression, hydratation `group` dans `/api/apps/`, PATCH `group_id`/`ungroup`).

---

## [3.29.0] - 14 août 2026

### ⚡ Certificat PhiliaNG auto-généré au démarrage (proxy transparent actif par défaut)

- `hub/pki.py` : `create_server_cert()` accepte désormais `--cn` et `--san` (CLI + API Python) pour générer un certificat serveur avec un CN/SAN explicite, sans fuiter les domaines hub (`PKI_CERT_DOMAINS`). Refactor : la conversion DNS/IP → entrées SAN est extraite en `_values_to_san()`, réutilisable.
- `hub/entrypoint.sh` : après la génération du cert hub, le hub-api génère automatiquement le cert PhiliaNG (`certs/server/philia-ng/`, CN=`service.example.org`, SAN=`DNS:service.example.org`) signé par la CA interne du Hub s'il est absent.
- `docker-compose.yml` : la variable `PHILIA_NG_DOMAIN` (défaut `service.example.org`) est passée au hub-api pour piloter le domaine du certificat généré.
- `.env.example` : documentation de `PHILIA_NG_DOMAIN`.
- `hub/tests/test_pki.py` : 2 nouveaux tests (CN/SAN surchargés sans fuite des domaines hub + CLI `--cn`/`--san`).

**Bénéfice** : suite logique du SNI automatique (v3.28.3). Le proxy transparent PhiliaNG (`service.example.org → :4443`) est désormais actif **par défaut sur tout déploiement** — `git pull && docker compose up -d` suffit, plus de génération/copie manuelle du certificat. De plus, le cert est signé par la CA interne du Hub (au lieu d'auto-signé) : les postes ayant importé la CA n'auront plus d'avertissement SSL.

---

## [3.28.5] - 15 août 2026

### ⚡ Journal d'erreurs : les 401 SSE non authentifiés ne sont plus enregistrés en base

- **Corrigé** : depuis l'audit #12 (SSE authentifié obligatoire), les applications legacy
  dont l'`AuthContext` connecte `/api/events/stream` sans token bouclent en 401 toutes les
  10 s → **~14 000 entrées/jour** dans `error_logs` qui masquent les vraies erreurs
  (incident 2026-08-15 : 14 012 erreurs en 13 h, IP gateway Docker).
- **Modifié** (`hub/app/main.py`, `http_exception_handler`) : les 401 sur
  `/api/events/stream` ne sont plus écrits dans `error_logs` — même logique que le
  filtrage existant des 404 non-API. Le rejet reste identique côté client (HTTP 401) et
  les accès restent visibles dans les access logs (déjà filtrés par
  `_SuppressLegacySSE401Filter`).
- Les 4 apps legacy concernées (exemple-app, exemple-app-16_API_v3, exemple-app_v3,
  exemple-app) sont corrigées par ailleurs (token SSE en query param) ; le point est
  documenté dans le skill de migration (`migrate-app-template` § D.3.2).

---

## [3.28.4] - 13 août 2026

### ⚡ Retrait du « Guide de maintenance » (vue d'aide)

- `frontend/public/index.html` : suppression du bouton d'aide `?` (`#guideBtn`) de la navbar et de la modale `#guideModal` complète (gestion des utilisateurs, applications, sauvegardes, audit, messagerie, VPN, commandes, sécurité).
- `frontend/public/js/modules/auth-navigation.js` : suppression de la fonction `showGuideModal()` et de l'affichage/masquage du bouton au login/logout admin.
- Vue jugée inutile : retirée sans équivalent.

**Bénéfice** : interface du hub-frontend allégée, moins de code mort à maintenir.

---

## [3.28.3] - 12 août 2026

### 🔧 SNI PhiliaNG automatique (plus d'IP hardcodée)

- `frontend/nginx-philia-ng.conf` : `proxy_pass` passe de `https://192.0.2.10:4443` (IP hardcodée) à `https://host.docker.internal:4443`. Le déploiement du HUB sur n'importe quel serveur fonctionne automatiquement, sans modifier la config nginx.
- `docker-compose.yml` : ajout de `extra_hosts: host.docker.internal:host-gateway` au service `hub-frontend` (nécessaire pour la résolution DNS dans le conteneur nginx).

**Bénéfice** : un `git pull && docker compose build hub-frontend && docker compose up -d hub-frontend` suffit pour que le SNI routing PhiliaNG fonctionne sur n'importe quel serveur, sans intervention manuelle.

## [3.28.2] - 12 août 2026

### ⚡ Docker GID configurable + fix comment workshop

- `.env.example` : ajout de la variable `HOST_DOCKER_GID` pour le mount du socket Docker.
- `docker-compose.yml` : le `group_add` du hub-api utilise `${HOST_DOCKER_GID:-128}` au lieu d'un GID fixe.
- `models.py` : correction du commentaire `is_workshop` (slug `-ws`, projet compose `X-ws`).
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.28.1] - 11 août 2026

### ⚡ PhiliaNG — inclusion conditionnelle (plus de crash au boot sans certs)

- **Corrigé** : le bloc serveur PhiliaNG (`service.example.org`) dans `nginx-ssl.conf`
  plantait nginx au démarrage si les certificats `/etc/nginx/ssl/server/philia-ng/`
  étaient absents (machine sans PhiliaNG déployé). Les certificats sont dans
  `certs/` (gitignoré) — ils ne voyagent pas avec le repo.
- **Refactor** : les blocs PhiliaNG (HTTP redirect :80 + HTTPS proxy :443) sont
  extraits dans `nginx-philia-ng.conf`, inclus conditionnellement par
  `docker-entrypoint.sh` uniquement si les fichiers `server.crt` et `server.key`
  existent.
- **Dockerfile** : copie de `nginx-philia-ng.conf` dans `/etc/nginx/templates/`.

---

## [3.28.0] - 10 août 2026

### ⚡ Installation full-auto par défaut + .env enrichi

- **Ajouté** : mode `--auto` dans `install.sh` — full automatique, zéro question. Auto-détecte l'IP, génère tous les secrets (DB, Fernet, JWT, SA interne), écrit le `.env`, lance Docker et vérifie la santé.
- **Modifié** : le mode auto est désormais le **défaut** (`./install.sh` sans argument = auto). L'ancien mode interactif s'active avec `--interactive`.
- **Ajouté** : le `.env` généré par `install.sh` inclut désormais toutes les variables OIDC (`OIDC_ENABLED`, `OIDC_ISSUER`, `OIDC_EMAIL_TEMPLATE`, `OIDC_CODE_TTL_SECONDS`, `OIDC_ID_TOKEN_TTL_MINUTES`, `OIDC_ACCESS_TOKEN_TTL_MINUTES`, `OIDC_REFRESH_TOKEN_DAYS`, `OIDC_BACKCHANNEL_LOGOUT_ENABLED`), les variables SLO (`OIDC_SLO_ENABLED`, `OIDC_SLO_IFRAME_TIMEOUT_MS`), et le Service Account interne (`HUB_INTERNAL_SA_CLIENT_ID`, `HUB_INTERNAL_SA_CLIENT_SECRET` auto-généré).
- **Ajouté** : `LEGACY_COMPAT_ENABLED` et `ALLOWED_ORIGIN_REGEX` dans le `.env` généré.
- **Ajouté** : affichage du mot de passe admin en fin d'installation (mode auto).
- **Mis à jour** : `.env.example` synchronisé avec les 5 variables manquantes (`LEGACY_COMPAT_ENABLED`, `OIDC_SLO_ENABLED`, `OIDC_SLO_IFRAME_TIMEOUT_MS`, `HUB_INTERNAL_SA_CLIENT_ID`, `HUB_INTERNAL_SA_CLIENT_SECRET`).
- **Corrigé** : logique d'écrasement du `.env` — le mode auto overwrite toujours, le mode `--profile` exige `--force` pour écraser.

---

## [3.27.0] - 10 août 2026

### ⚡ SNI routing PhiliaNG transparent proxy

- **Ajouté** : bloc serveur SNI `service.example.org` sur le port 443, route le trafic vers le conteneur Philia proxy-frontend (port 4443)
- **Ajouté** : redirection HTTP→HTTPS pour `service.example.org` sur le port 80
- **Ajouté** : certificat dédié CN=service.example.org (auto-signé) pour la validation navigateur des cookies Keycloak exemple Connect
- **Ajouté** : support SSE et long-polling (proxy_buffering off, proxy_read_timeout 86400s)
- **Modifié** : le serveur Hub sur les ports 80 et 443 passe en `default_server` (catch-all)

## [3.26.1] - 6 août 2026

### ⚡ SLO v2 — Câblage M2M HUB→exemple-app + Known Bug

#### Ajouté
- **`slo_orchestrator.py`** : module qui appelle chaque traducteur (app M2M) pour
  révoquer les sessions distantes de l'ancien user via le SA interne `hub-internal`.
- **SA `hub-internal`** : Service Account dédié (client_id=`hub-internal`), secret
  stocké dans `.env` (`HUB_INTERNAL_SA_CLIENT_ID`/`HUB_INTERNAL_SA_CLIENT_SECRET`),
  associé à l'app `exemple-app`. Permet au HUB de s'appeler lui-même en
  `grant_type=client_credentials` puis d'appeler les traducteurs en M2M.
- **Variables `hub_internal_sa_*`** dans `config.py` + `docker-compose.yml`.
- **Intégration login** : `auth.py` appelle `propagate_remote_revoke()` au login
  quand un switch est détecté (best-effort, ne bloque pas le login).

#### Ops — fix déploiement
- **`docker-compose.yml`** : les variables `OIDC_SLO_ENABLED` et
  `OIDC_SLO_IFRAME_TIMEOUT_MS` manquaient → impossible d'activer le kill-switch
  SLO via `.env` sans rebuilder l'image. Ajoutées.

### ⚠️ Known Bug — SLO différé (pas immédiat)

**Scénario** : A se connecte au HUB, lance Vikunja → B se connecte par-dessus au HUB
→ B clique sur Vikunja → A est **toujours connecté** pendant jusqu'à `jwtttl` (72h).

**Cause racine** : le JWT d'accès Vikunja est **stateless** (signé RS256, pas en DB),
donc non révocable avant expiration (`jwtttl=259200`). Le M2M `DELETE FROM sessions`
casse le **refresh token** (empêche le renouvellement) mais pas le JWT déjà émis.
Aucun mécanisme de `jti` blocklist dans Vikunja v2.4.0.

**Ce qui a été tenté (et pourquoi ça n'a pas suffi)** :

| Approche | Statut | Raison de l'échec |
|---|---|---|
| **Iframe cachée** vers `vikunja/logout` | ❌ abandonné | La SPA Vikunja ne purge pas automatiquement son `localStorage` sur `/logout` (le logout est un clic bouton → `D(u).logout()`, pas une route). L'iframe charge la SPA mais ne déclenche rien. |
| **M2M DELETE sessions** (v2, actuel) | ⚠️ partiel | Casse le refresh mais pas le JWT d'accès stateless. L'ancien user est déconnecté dans max `jwtttl` secondes (72h), pas immédiatement. |
| **`jwtttl=300`** (5 min) | ❌ abandonné | Réduit le délai à 5 min mais l'utilisateur voit encore A pendant ce laps de temps. Dégradation UX inacceptable pour les sessions longues légitimes. |

**Workaround utilisateur** : fermer manuellement l'onglet Vikunja puis le rouvrir
depuis le dashboard HUB — le `launch` redirige vers `/logout?provider=hub` (qui ne
purge pas plus, mais si le JWT est expiré entre-temps, le flow OIDC redémarre).

**Pistes futures** (non implémentées, à évaluer) :
- **Remplacer l'iframe par une navigation réelle** (`window.location = vikunja/logout`
  puis retour au dashboard) — plus intrusif UX mais garantit l'exécution du JS SPA.
- **Fork Vikunja** pour ajouter un endpoint de révocation de JWT (jti blacklist) —
  lourd mais seule solution vraiment immédiate.
- **Désactiver l'ancien user** (`users.status`) côté Vikunja via le traducteur —
  l'API exemple-app le permet mais c'est sémantiquement un **offboarding**
  (inadapté pour un simple switch de poste).
- **`prompt=login`** dans le flow OIDC — standard, mais Vikunja ne le supporte pas
  côté RP (il ne le passe pas à l'IdP).

#### Code toujours actif (non retiré)
- **`slo-purge.js`** (iframe frontend) : conservé — ne fait rien en pratique
  (la SPA Vikunja ignore `/logout`) mais ne gêne pas. À retirer si définitivement
  inutile.
- **`remote_logout`** dans `POST /api/auth/token` : conservé pour le frontend
  (la réponse contient toujours la liste des apps à purger, même si le frontend
  ne peut pas les purger efficacement aujourd'hui).

### 🔧 Correctifs hérités de 3.26.0
- **`UserAppRole.application_id` → `UserAppRole.app_id`** dans `/oidc/authorize`
  (regression cassait le SSO pour les users non-admin sur clients OIDC liés à
  une app — déjà déployé en hotfix puis mergé dans 3.26.0).

---

## [3.26.0] - 6 août 2026

### ⚡ SLO poste partagé (déconnexion propagée au login HUB)

- **Détection de changement d'utilisateur** au `POST /api/auth/token` via le cookie
  `hub_oidc_session` précédent : si un user B se connecte par-dessus un user A, le
  backend collecte les apps OIDC où A avait un refresh token valide.
- **Nouveau champ de réponse** `remote_logout: list[{slug, frontend_url, logout_url, translator_slug}]`
  dans la réponse de login (grant password). Le frontend HUB ouvre ensuite des iframes
  cachées vers `{app}/logout` pour purger les sessions distantes de A (localStorage +
  cookie HttpOnly cross-origin).
- **Kill-switch** : nouvelle var `OIDC_SLO_ENABLED` (défaut **False** en 3.26.0 — activer
  via `.env` après validation e2e atelier) et `OIDC_SLO_IFRAME_TIMEOUT_MS` (défaut 1500).
- **Helpers internes** : `slo_helpers.py` (`detect_user_switch_and_collect_logouts`,
  `lookup_translator_slug`, mapping statique `APP_TRANSLATORS = {"vikunja": "exemple-app"}`).
- **Frontend** : nouveau module `slo-purge.js` + intégration post-login dans
  `auth-navigation.js`. Best-effort (try/catch) — n'affiche jamais d'erreur visible.
- **Spec** : `docs/superpowers/specs/2026-08-06-slo-shared-workstation-design.md`.

### 🔧 Correctifs

- **`UserAppRole.application_id` → `UserAppRole.app_id`** dans `/oidc/authorize`
  (regression cassait le SSO pour les users non-admin sur clients OIDC liés à une app —
  les admins ne déclenchaient pas le bug grâce au court-circuit `if not user.is_admin`).

### 🔒 Sécurité

- Nouvel endpoint M2M côté `exemple-app` uniquement (cf. release exemple-app
  séparée) — non câblé côté HUB en 3.26.0 (câblage reporté v2).

### ⚠️ Notes de déploiement

- **Activation progressive recommandée** : laisser `OIDC_SLO_ENABLED=false` au déploiement,
  valider e2e atelier (scénario A→B sur Vikunja), puis flipper à `true` dans `.env`
  et redémarrer `hub-api`.
- Aucune migration DB requise.
- Rétrocompatible : si `remote_logout` est absent ou vide, frontend ne fait rien.

---

## [3.25.0] - 5 août 2026

### ⚡ Popup onboarding préférences + navbar unifiée

- **Popup d'onboarding** : le widget « Mes préférences » est retiré du dashboard et
  remplacé par un popup modal affiché à la connexion si l'utilisateur n'a pas encore
  sélectionné de préférences. Le popup réapparaît à chaque connexion tant qu'aucune
  préférence n'est choisie (décision B) et resurgit lorsqu'un admin ajoute une
  nouvelle catégorie (décision Oui). Idempotent, sans double-ack.
- **Table `user_tag_seen`** : nouvelle table normalisée (FK → `tag_categories`, `ON
  DELETE CASCADE`) pour tracer les catégories déjà présentées à un utilisateur.
  Migration 049.
- **API `POST /api/tags/my/acknowledge`** : marque des catégories comme « vues » (popup
  ack). Idempotent, slugs inconnus ignorés silencieusement. Émet un événement SSE
  `user_tag_ack`.
- **`GET /api/tags/my` étendu** : la réponse inclut désormais `seen_categories` (liste
  des slugs déjà présentés). Champ rétrocompatible (`default_factory=list`).
- **Navbar « Préférences » pour tous les utilisateurs** : l'entrée est désormais
  visible pour les non-admins (plus limitée au bloc `#adminNavigation`). Les onglets
  s'adaptent au rôle : non-admin → uniquement « Mes préférences », admin → 3 onglets.
- **Refactor form-builder** : extraction des helpers `buildTagsFormHTML()` /
  `collectTagsFromForm()` mutualisés entre le popup, l'onglet admin et la modale
  d'édition. -56 lignes de code dupliqué.
- **Nettoyage SSE** : la branche `tags_updated` ne référence plus le widget dashboard
  supprimé ; rechargement préservé pour la page admin dédiée.
- **Tests** : 20 tests d'intégration (5 nouveaux : `seen_categories` vide/non-vide,
  ack insert/idempotent, exclusion catégories inactives).

---

## [3.24.0] - 3 août 2026

### ⚡ Préférences utilisateur globales + JWT tags + UI admin

- **Système de préférences utilisateur** : les admins peuvent définir des catégories
  de préférences (ex: secteur, équipe) avec des valeurs importées via CSV ou M2M depuis
  une app source. Les utilisateurs sélectionnent leurs valeurs depuis leur dashboard.
  Les apps consomment ces préférences automatiquement via le claim JWT `tags`.
- **JWT `tags` claim** : `create_access_token()` inclut désormais les préférences de
  l'utilisateur dans le payload JWT (`tags: {slug: [values]}`). Les apps qui ne gèrent
  pas une préférence l'ignorent silencieusement.
- **API REST** (`/api/tags`) : 12 endpoints — CRUD catégories (admin), import CSV/M2M,
  GET/PUT préférences utilisateur (`/my`), liste et modification des préférences par
  utilisateur (`/users`, `/user/{username}`), endpoint M2M pour les apps.
- **Migration 047** : tables `tag_categories` et `user_tags` (idempotente `IF NOT EXISTS`).
- **UI admin** : page « Préférences utilisateur » avec 3 onglets — Catégories (CRUD +
  stats), Utilisateurs (vue table + modal d'édition des préférences de chaque user),
  Mes préférences (sélection personnelle de l'admin).
- **Widget dashboard** : « Mes préférences » pour les utilisateurs non-admin, avec
  checkboxes (choix multiples) ou radios (choix unique).
- **SSE** : événement `tags_updated` pour rafraîchir les widgets en temps réel.
- **Tests** : 14 tests d'intégration (CRUD, validation, cascade, JWT, e2e login→tags).
- **Correctifs** : proxy SSL redirect (`proxy_redirect` + `X-Forwarded-Proto`), grille
  VPN peers UI, healthcheck frontend.

---

## [3.23.0] - 2 août 2026

### ⚡ Guide modal + health enrichi + audit IP réelle + VPN fiabilisé

- **Guide de maintenance** : passe d'une section inline dans le dashboard à une modale
  Bootstrap chargée à la demande (bouton Guide réservé aux admins).
- **Apps santé** : badge « Système » sur la carte du HUB, masquage des boutons ▶/■/⟳/📋
  sur les apps système (le HUB ne se start/stop pas depuis cette vue).
- **`/api/health`** expose la version (`get_hub_version()`) et l'uptime processus HUB
  (via `/proc/self/stat`). Le health check des apps enrichit le champ `is_system_app`.
- **Audit IP réelle** : `_get_client_ip()` remplace `request.client.host` dans toutes
  les routes auth et audit. Derrière le proxy nginx, l'IP du client réel est désormais
  correctement loguée (X-Forwarded-For/X-Real-IP).
- **App système `git_repo_url`** : le champ est persisté dans `ensure_hub_system_application()`
  et modifiable via l'UI (nécessaire pour l'update-check et le changelog). Schema `AppUpdate`
  enrichi.
- **VPN — migration API Docker** : `subprocess.run(["docker", ...])` remplacé par l'API
  Python Docker (`docker.from_env().containers.get(...).exec_run()`). Le CLI `docker`
  n'est pas installé dans l'image hub-api — tout passe désormais par le socket Docker.
- **VPN — parsing handshake** : `_parse_handshake_age()` utilise une regex pour convertir
  les durées `wg show` (secondes, minutes, heures, jours, combinaisons) en secondes.
  `_peer_connected()` applique un seuil de 180s. Corrige le bug où « 1 hour, 5 minutes ago »
  était compté comme connecté (contenait « minute »).
- **VPN — transfer pretty** : parsing du format « 1.50 GiB received, 789.00 MiB sent ».
- **Dashboard** : sélecteur CSS `[data-filter]` plus précis pour éviter de matcher
  le bouton Guide.
- **Tests VPN enrichis** : `_parse_handshake_age` et `_peer_connected`.
- **Spec** : hub-self-restore design ajoutée.

---

## [3.22.0] - 2 août 2026

### ⚡ Restauration self-backup du HUB

- **POST `/api/admin/restore`** : nouvel endpoint permettant de restaurer la configuration
  du HUB (users, apps, service accounts, rôles) depuis un backup ZIP contenant
  `environment.json`. Auth admin et M2M (service accounts).
- **`import_environment()`** : pipeline d'import en mode update, best-effort avec
  savepoints par entité — une erreur sur une entité n'interrompt pas l'import global.
- **Service Account HUB** : script `create_hub_sa.py` pour créer un SA dédié au HUB,
  nécessaire au flux de restauration existant `restore_backup_to_app(slug="hub")`.
- **Tests** : 14 tests couvrant l'import (users, apps, SAs, rôles), l'auth (admin + M2M),
  les erreurs (ZIP corrompu, fichier manquant) et un test d'intégration complet.

---

## [3.21.0] - 2 août 2026

### Ajouts
- **VPN WireGuard — Mode serveur** : le HUB est désormais serveur WireGuard,
  permettant aux utilisateurs HUB de se connecter au HUB à distance. Gestion
  des peers liée aux comptes HUB, téléchargement .conf, QR code, split tunnel.
  Le mode client précédent (site-à-site) est retiré.
- **AllowedIPs dynamiques** : accès à toutes les apps du host (`HUB_HOST:*`)
  via le tunnel VPN, aucune IP en dur dans le code — `hub_host` lu depuis
  `HUB_HOST`/`.env` et `vpn_server_ip` dérivé du sous-réseau VPN.

### Changements technique
- Service Docker `wireguard-client` renommé `wireguard-server`
- Nouvelle table `vpn_peers` (FK users avec cascade)
- Migration `046_vpn_server_mode.sql`
- Variable `WG_ENABLED` supprimée (cosmétique, non reconnue par l'image)
- `hubctl vpn` : `WG_SERVICE` mis à jour

### Corrections
- **Changement de port via l'UI réellement appliqué** : le conteneur
  `hub-wireguard` est redémarré lorsque le `ListenPort` change (`wg syncconf`
  ne modifie pas le `ListenPort` à chaud — bug silencieux corrigé)
- **Retrait du DNS imposé au client** : suppression de `DNS = 10.8.0.1` dans
  les `.conf` clients — le client conserve son résolveur habituel (évite que
  le DNS du VPN casse internet quand le tunnel n'est pas monté)
- **`api.js`** : gestion du nouveau format d'erreur Pydantic `{field, message}`
  en plus du format historique `{loc, msg}`

---

## [3.20.1] - 1 août 2026

### ⚡ Corrections UI — filtrage temporel audit + bugs data-action

- **audit: boutons de période visuellement cassés** — `parseArgs` passe les arguments en string, la comparaison stricte `parseInt(dataset) === days` échouait toujours. Ajout de `parseInt(days)` dans `setAuditPeriod`.
- **audit: double sélecteur de période incohérent** — supprimé `errorsFilterHours` indépendant dans l'onglet Erreurs, remplacé par `getErrorHours()` lié au sélecteur global `auditPeriodPage` (plafonné à 168h, max API).
- **audit: changement de période ignorait l'onglet actif** — `loadAuditPageData()` n'appelait que l'overview. Ajout de la détection de l'onglet actif (`#auditPageTabs .nav-link.active`) et appel du loader correspondant.
- **SSE audit: reconnect et `audit_updated` incomplets** — appelaient `loadAuditPageOverview()` au lieu de `loadAuditPageData()`, ignorant l'onglet visible. Corrigé (×2).
- **export CSV audit: période et filtres toujours au défaut 30j** — `exportAuditLogs`/`exportSessions` lisaient `auditPeriod` (inexistant) et `logsFilterAction`/`logsFilterApp` (vieux IDs modale). Corrigé vers `auditPeriodPage` + `pageLogsFilterAction`/`pageLogsFilterApp`.
- **toggle inscriptions admin: toujours actif** — `data-args="this.checked"` passé en string littérale `"this.checked"` (toujours truthy). Remplacé par `$el` + lecture de `el.checked`.
- **profils: « Tout désélectionner » sélectionnait tout** — `profileSelectAllApps("false")` recevait la string `"false"` (truthy). Ajout de `checked = (checked === true || checked === 'true')`.
- **SSE users reconnect: profils/demandes ignorés** — `refreshAfterReconnect` et `user_permissions_changed` n'appelaient que `loadUsersPageData()`. Ajout de `loadUsersPageActiveTab()` avec détection d'onglet actif (users/profils/demandes).
- **audit.js: purge de 18 fonctions mortes** (617→138 lignes) — l'ancien cluster modale (`loadAuditData`, `renderAuditStats`, `renderActiveSessions`, etc.) ciblait des éléments DOM qui n'existent plus. Conservé uniquement les 7 fonctions utilisées (5 helpers + 2 exports).
- **audit-page.js: source de vérité unifiée** — suppression de la variable `pageAuditDays`, toutes les fonctions lisent désormais `auditPeriodPage` (DOM).

---

## [3.20.0] - 1 août 2026

### ⚡ Workshop auto-approval + token GitHub + fix backup_mode

- **Workshop DEV auto-approval** : en mode DEV (`hubctl --dev`), les apps atelier (`slug-ws`) sont auto-approuvées si l'app de base (sans `-ws`) est déjà approuvée en base. Élimine la friction d'approbation admin pour les clones de développement. S'applique à la création ET au ré-enregistrement (re-register d'un atelier précédemment pending).
- **Token GitHub** : formulaire de saisie du token GitHub directement dans l'interface admin (onglet Apps → GitHub). Le token est stocké via l'endpoint `PUT /api/settings/{key}` (upsert), avec validation basique du format (`ghp_` / `github_pat_`). Badge visuel indiquant l'état du token.
- **Settings upsert** : `PUT /api/settings/{key}` accepte désormais les chaînes quelconques (plus limité aux booléens) et crée le paramètre s'il n'existe pas (upsert). Invalidation du cache `github_token` à la mise à jour.
- **Fix `_safe_backup_mode()`** : corrige un crash 500 sur `GET /api/admin/backups` quand une app a `backup_mode = "none"` en base. Le nouveau helper valide contre les valeurs connues (`disabled`, `manual`, `scheduled`) avec fallback `"manual"`. Appliqué aux 4 endpoints lecture + `ensure_hub_system_application`.
- **Tests** : settings — upsert, chaînes GitHub, valeurs numériques rejetées ; backup — 23/23 passent.

---

## [3.19.1] - 30 juillet 2026

### 🐛 Fix — migrations 026 et 042 cassées sur fresh install

- **Migration 026** (`INSERT` app système Hub) : ajout des colonnes `is_workshop` et `is_unmanaged` dans la liste explicite de colonnes. Sans ces colonnes, l'INSERT échouait avec `NotNullViolation` sur base fraîche car `create_all()` matérialise toutes les colonnes `NOT NULL` du modèle avant l'exécution des migrations.
- **Migration 042** (`ALTER TABLE ADD COLUMN git_repo_url`) : ajout de `IF NOT EXISTS`. Sans cette garde, la migration échouait avec `DuplicateColumnError` car le modèle SQLAlchemy contient déjà la colonne.
- **Test** : nouveau garde-fou `test_migration_026_insert_covers_all_required_columns` — vérifie dynamiquement que l'INSERT de la migration 026 couvre toutes les colonnes `NOT NULL` sans `server_default` du modèle `Application`. Toute future colonne ajoutée au modèle sera détectée automatiquement, empêchant cette classe de bug à la source.

---

## [3.19.0] - 29 juillet 2026

### 📣 Pour les utilisateurs

- **Ajouté** : nouvelle option « Non gérée (lien only) » dans les paramètres d'une application. Permet de déclarer qu'une app est pilotée par un proxy externe (ex. Vikunja piloté par exemple-app) — le HUB ne la sonde plus (santé, backup, sync) mais elle reste visible et cliquable dans le launcher.
- **Ajouté** : filtre « Gestion » (Toutes / Gérées / Non gérées) dans la vue admin des applications, pour distinguer les apps pilotées directement par le HUB de celles pilotées par un proxy.
- **Amélioré** : badge visuel « Non gérée » sur les apps concernées, avec une icône dédiée.

### ⚡ Apps non gérées — `is_unmanaged`

- Ajout de la colonne `is_unmanaged` sur `applications` (migration 045) — flag pour les apps « launcher-only » : présentes/lancables mais exclues de la gestion technique.
- **Health-check** : exclusion des apps `is_unmanaged=True` du checker périodique et du dashboard santé.
- **Backup** : refus du backup manuel (HTTP 400) et exclusion du scheduler planifié et du snapshot global pour les apps non gérées.
- **Sync Docker** : exclusion des apps non gérées du repositionnement automatique de `is_active`.
- **Frontend admin** : toggle « Non gérée » dans le formulaire, badge « Non gérée » sur les lignes, filtre « Gestion » (Toutes/Gérées/Non gérées) dans la toolbar.
- **Vikunja (id=6)** : flaggée `is_unmanaged=True, backup_mode=disabled` — l'app Docker Vikunja pilotée par `exemple-app` (id=5) n'est plus health-checkée ni backupée.

---

## [3.18.2] - 28 juillet 2026

### 📣 Pour les utilisateurs

- **Corrigé** : après plusieurs heures d'utilisation du HUB, les apps externes (Vikunja) ne demandaient plus de reconnexion au dashboard. La session reste désormais active en continu, sans interruption du SSO.

### 🐛 Fix — cookie OIDC non rafraîchi au refresh token (SSO Vikunja cassé après 8h)

- **Problème** : le cookie `hub_oidc_session` (TTL 8h) n'était posé qu'au login password (`POST /api/auth/token` grant_type=password). Les endpoints de refresh (`grant_type=refresh_token` et `/token/refresh`) renouvellent le JWT dashboard (refresh token 7j) mais ne re-posaient pas le cookie OIDC → après 8h, l'utilisateur restait connecté au dashboard mais `/oidc/authorize` renvoyait 401 "Session HUB requise" → apps OIDC (Vikunja) inaccessibles.
- **Fix** : nouveau helper `attach_oidc_session_cookie()` dans `oidc_helpers.py` (unique source de vérité pour les flags httpOnly/samesite/secure/TTL 8h), appelé dans les 3 sites : login password, refresh via `/api/auth/token` grant_type=refresh_token, et `/api/auth/token/refresh`. Appel uniquement dans la branche `refresh_obj.user` (pas les service accounts).
- **Tests** : 4 nouveaux tests d'intégration dans `test_api_auth.py` — login sanity, refresh `/token` + `/token/refresh` re-posent le cookie, non-régression SA. Suite OIDC complète inchangée (115+/116).

---

## [3.18.1] - 27 juillet 2026

### ⚡ Fix timezone `github_repo_cache`

- **Corrigé** : `GithubRepoCache.updated_at_github` et `fetched_at` utilisaient `DateTime(timezone=True)` (timezone-aware) en Python, mais la migration 044 crée `TIMESTAMP` (sans fuseau). Incohérence qui causait un `TypeError: can't subtract offset-naive and offset-aware datetimes` dans la garde d'âge du refresh.
- **Alignement** : le modèle utilise maintenant `DateTime` naïf (sans `timezone=True`), cohérent avec toutes les autres colonnes du codebase et le helper `utcnow()`.
- **Nettoyage** : `_parse_iso()` retourne du naïf UTC (`.replace(tzinfo=None)`), la garde d'âge utilise `utcnow()` au lieu d'un hack `.replace(tzinfo=timezone.utc)`, et l'assignation `now` dans le refresh utilise `utcnow()`.

---

## [3.18.0] - 27 juillet 2026

### 📣 Pour les utilisateurs

- **Ajouté** : un nouvel onglet « Apps GitHub » dans la page Admin. Si un token GitHub est configuré, vous pouvez voir vos dépôts, identifier ceux déjà intégrés au HUB, et ajouter une app en un clic — le HUB pré-remplit le formulaire à votre place (nom, slug, ports). Un bloc `.env` copiable vous rappelle les valeurs à reporter dans l'app déployée.

### ⚡ Découverte visuelle d'apps GitHub

- **Ajout** : nouvel onglet Admin « Apps GitHub » — grille de cartes listant les dépôts GitHub accessibles au PAT configuré (`GITHUB_TOKEN` env + `github_token` dans les settings), avec badges 🟢 Dispo / 🔵 Intégré / 📦 Archivé. Sélection au clic pour pré-remplir le formulaire d'ajout d'app (slug dérivé + ports alloués par le HUB, principe « le HUB décide, l'app suit »). Cache persistant `github_repo_cache` synchronisé via le bouton « Rafraîchir » du dashboard santé (garde d'âge 30s) + bouton dédié dans la vue.
- **Ajout** : 3 nouveaux endpoints admin-only sous `/api/apps/github/` :
  - `GET /repos` — lit le cache (0 appel GitHub) avec filtre `search`/`owner`/forks/archiveds et calcul `already_integrated` (croisé avec `git_repo_url` des apps existantes).
  - `POST /repos/refresh` — synchronise le cache depuis GitHub `/user/repos` paginé (upsert par `repo_id`), avec gestion rate-limit/401/timeout (cache jamais vidé sur erreur).
  - `POST /prepare` — slug dérivé + ports alloués via `allocate_ports_for_app` (idempotent), 409 sur collision de slug.
- **Ajout** : modal d'ajout d'app pré-rempli avec encart `.env` copiable (`APP_SLUG`, ports) + champ `git_repo_url` optionnel dans `POST /api/apps/` (option α).
- **Refactor** : extraction du client GitHub (`_github_headers`, `_load_db_token`, `_fetch_github`, `_parse_github_url`, `_fetch_github_raw`) de `apps_health.py` vers `app/github_client.py` + nouvelle fonction `slugify_repo`.
- **Ajout** : migration `044_create_github_repo_cache.sql` (cache persistant des repos).
- **Ajout** : 65 nouveaux tests backend (`test_github_catalog.py`, `test_github_client.py`, extension `test_api_apps.py`) — couvre tous les endpoints, la matrice d'erreurs et la non-régression.

---

## [3.17.3] - 27 juillet 2026

### 🐛 Fix — modales invisibles (classe `show` manquante)

- Bootstrap `.fade:not(.show) { opacity: 0 }` rendait les modales logs et changelog complètement transparentes. L'overlay sombre (lui aussi invisible) bloquait tous les clics, donnant l'impression d'un « hang » de la page. Ajout de la classe `show` dans `_showModal()`.

---

## [3.17.2] - 27 juillet 2026

### 🐛 Fix — logs, start, stop restaurés (Docker socket)

- **fix(logs)** : l'endpoint `GET /api/apps/{slug}/logs` utilise désormais `container.logs()` (docker-py) au lieu de `docker compose logs` (qui nécessitait les fichiers compose absents du conteneur). Corrige l'erreur 404 « docker-compose.yml introuvable ».
- **fix(start/stop)** : les endpoints `POST /{slug}/start` et `/stop` utilisent `container.start()`/`container.stop()` (docker-py) au lieu d'appeler `hubctl` (inaccessible depuis le conteneur).
- **fix(docker-compose)** : ajout du mount `/var/run/docker.sock:ro` et `group_add: ["128"]` (groupe docker) au service `hub-api`. Sans ce mount, `docker_scanner` ne pouvait pas se connecter et aucune action de pilotage (restart/logs/start/stop) ne fonctionnait.

---

## [3.17.1] - 24 juillet 2026

### 🔧 Corrigé
- **Session multi-utilisateur Vikunja** : le launch OIDC redirige désormais vers `/logout` de l'app avant d'afficher la page de login. Cela purge le cookie de session Vikunja et empêche l'utilisateur suivant de voir les données du précédent. Le flow est fiable (302 direct) et ne dépend pas d'iframes JavaScript.

---

## [3.17.0] - 24 juillet 2026

### ⚡ Notes cumulatives — toutes les versions intermédiaires affichées

- **feat(cumul)** : quand la modale changelog est ouverte depuis le badge de mise à jour, l'onglet Notes affiche désormais **toutes** les releases entre la version actuelle déployée et la dernière disponible. Chaque version intermédiaire est présentée dans une carte dédiée (tag, date, body). Un header indique la plage (ex: « 1.0.0 → 1.3.0 · 3 versions »).
- **comportement** : le sélecteur de version permet toujours d'afficher les notes d'une version spécifique (mode normal). Revenir sur la dernière version via le sélecteur réactive le mode cumulatif.
- **tech** : fonctions `_renderCumulativeNotes`, `_versionNewer`, `_parseVersion` pour la comparaison semver et le rendu multi-sections.

---

## [3.16.0] - 24 juillet 2026

### ⚡ Changelog — navigateur d'historique + visualiseur de code modifié

- **feat(releases)** : endpoint `GET /api/apps/{slug}/releases` interroge l'API GitHub et retourne la liste structurée des releases (tag, date, notes, URL). La modale changelog affiche désormais un **sélecteur de version** pour naviguer dans tout l'historique.
- **feat(diff)** : endpoint `GET /api/apps/{slug}/diff?from=X&to=Y` utilise l'API GitHub `compare` et retourne les fichiers modifiés avec leurs patches unifiés (`additions`, `deletions`, `patch`). Plafonné à 50 fichiers.
- **feat(UI code)** : onglet « Code » dans la modale changelog — affiche la liste des fichiers modifiés (avec icônes 🟢 ajout / 🟡 modif / 🔴 suppression, compteurs +/−), chacun **expandable** pour révéler le diff colorisé (vert/rouge/hunk headers). Lien vers la comparaison GitHub complète.
- **feat(UI notes)** : onglet « Notes » affichant le body de la release sélectionnée. Fallback automatique sur le `CHANGELOG.md` brut si aucune release GitHub n'est trouvée.
- **tests** : +4 tests (parsing releases, parsing diff, cas repo non configuré). Total : 12 tests passent.

---

## [3.15.0] - 24 juillet 2026

### ⚡ Dashboard santé — pilotage des apps + mise à jour + changelog

- **feat(start/stop)** : nouveaux endpoints `POST /api/apps/{slug}/start` et `/stop` qui wrappent `hubctl start/stop {slug}` en subprocess. Boutons ▶ Démarrer et ■ Arrêter sur chaque carte, avec confirmation navigateur.
- **feat(logs)** : endpoint `GET /api/apps/{slug}/logs?tail=150&service=api` retourne les logs Docker Compose récents. Bouton 📋 Logs avec affichage dans une modale (fond sombre, scrollable).
- **feat(update-check)** : endpoint `GET /api/apps/{slug}/update-check` interroge l'API GitHub (`/repos/{owner}/{repo}/releases/latest`) et compare la version actuelle (sonde `/api/health`) avec la dernière release. Un badge 🔄 « Mise à jour disponible : X → Y » apparaît sur la carte quand une version supérieure est détectée.
- **feat(changelog)** : endpoint `GET /api/apps/{slug}/changelog` récupère le `CHANGELOG.md` brut depuis GitHub (`raw.githubusercontent.com`). Affichage dans une modale dédiée au clic sur le badge de mise à jour.
- **model** : nouveau champ `git_repo_url` (nullable) sur le modèle `Application` + migration 042. Requis pour activer update-check et changelog par app.
- **UI** : refonte du footer des cartes — 4 boutons d'action (Start/Stop/Redémarrer/Logs) en groupe compact. Le badge de mise à jour s'affiche en arrière-plan après le rendu (non-bloquant).

---

## [3.14.0] - 23 juillet 2026

### ⚡ Dashboard de santé — sonde enrichie + UI détaillée

- **feat(probe)** : refonte de la logique de sonde dans `apps_health.py`. L'ordre des paths est inversé (`/api/health` d'abord, standard du parc), et un **4xx n'interrompt plus le parcours** — il indique juste que le serveur est joignable, et on continue vers le path suivant (fin du bug « note grise pour toutes les apps template »).
- **feat(detailed)** : phase 2 de sonde — si l'app est joignable, le Hub interroge `/api/health/detailed` pour collecter `uptime_seconds`, `hub.connected`, `services.discovered_count`, `m2m_token_available`, et `app.version` (fallback si absent du `/api/health` basique).
- **feat(UI)** : enrichissement de la carte `renderAppCard` avec ⏱ uptime formaté, badge vert/rouge « Hub connecté/déconnecté », et compteur 🔌 services découverts. Le message « pas d'endpoint /health standard » ne s'affiche plus que s'il n'y a **vraiment** aucune donnée collectée.
- **tests** : 8 nouveaux tests unitaires (`test_api_apps_health.py`) couvrant la continuation sur 4xx, l'extraction version (top-level + fallback detailed), les données riches, et les statuts offline/degraded.
- **schema** : `AppHealthItem` enrichi avec `uptime_seconds`, `hub_connected`, `services_count`, `m2m_token_available` (tous `Optional`, rétrocompatibles).

### 🔗 OIDC — endpoint /authorize + admin clients (v3.13.x, portage depuis branche feature)

- L'ensemble des features OIDC développées sur la branche `feat/oidc-bridge-v3.13` est intégré : endpoint `/oidc/authorize`, gestion admin des clients OAuth, sessions cookie `hub_oidc_session`, discovery, backchannel logout, helpers JWT. (Détails complets dans les entrées v3.13.1 → v3.13.4 ci-dessous.)

---

## [3.13.4] - 23 juillet 2026

### 🐛 Fix — lazy=raise sur User.app_roles dans /oidc/authorize (500 en prod)

- **Problème** : user.get_role_for_app() accédait à User.app_roles (lazy="raise" en async) → InvalidRequestError 500.
- **Fix** : requête directe sur UserAppRole (select + user_id + application_id), évite le lazy loading.


### 🐛 Détection d'IP : renvoie l'IP LAN du serveur (pas l'IP Docker du conteneur)

| Fonctionnalité | Description |
|----------------|-------------|
| Détection host | `GET /api/config/network/detect-host-ip` (admin) : conteneur jetable en `--network host` (image alpine:3, pattern clone-db du workshop) + `ip -4 route get 8.8.8.8` avec iproute2 ajouté à la volée → renvoie l'IP LAN du serveur (ex. 192.0.2.10) alors que la socket UDP du conteneur ne voit que son IP Docker (10.42.0.x) |
| UI | Le bouton « Détecter » du modal d'IP appelle ce endpoint ; avertissement « IP Docker » retiré (obsoleted par la détection fiable) et ligne trompeuse « Auto-détectée » retirée de la carte |
| Parse | La sortie brute de `ip route get` est parsée côté Python (le champ `src` est en 7ᵉ position selon le format — plus d'awk positionnel fragile) |

---

## [3.13.3] - 23 juillet 2026

### 🐛 Fix — cookie `hub_oidc_session` ignoré en HTTP dev (Secure flag)

- **Problème** : le cookie était posé avec `secure=settings.hub_https_enabled` (défaut `true`), mais en environnement dev HTTP, un cookie `Secure` n'est **jamais stocké** par le navigateur → le cookie n'était jamais envoyé à `/oidc/authorize` → erreur 401 "Session HUB requise".
- **Fix** : `secure=(request.url.scheme == "https")` détecte le transport réel. Le cookie est `Secure` seulement si la connexion est HTTPS. En dev HTTP, le cookie est posé normalement et envoyé par le navigateur.

---

## [3.13.2] - 23 juillet 2026

### 🐛 Fix — launch shortcut devient redirecteur (corrige "State does not match" Vikunja)

- **Problème** : le dashboard HUB ajoute toujours `?access_token=<jwt>` au `callback_url` de l'app au clic. Quand `callback_url` pointait vers la page de login Vikunja (`/auth/openid/auth`), Vikunja recevait `?access_token=...` et l'interprétait comme un retour de flow OIDC → erreur "State does not match" (pas de `state`/`code` dans l'URL).
- **Fix** : `/api/oidc/launch/{client_id}` ne génère plus de code OIDC. Il valide le JWT HUB, lookup l'app liée, et **redirige 302 vers le `frontend_url`** de l'app (page de login Vikunja) **SANS `access_token`** dans l'URL finale. L'user arrive proprement sur la page de login Vikunja, clique le provider HUB, et Vikunja initie le flow OIDC standard via `/oidc/authorize` (qui lit le cookie `hub_oidc_session`).
- **Tests** : 6 tests du launch shortcut réécrits pour le nouveau comportement redirecteur. Suite OIDC complète : 47/47.

### 📋 Configuration attendue des apps OIDC

- `callback_url` de l'app HUB → `http://<hub_host>:<hub_port>/api/oidc/launch/<client_id>` (le redirecteur).
- `frontend_url` de l'app HUB → page de login OIDC de l'app externe (ex: `http://vikunja.../auth/openid/auth`).
- `redirect_uris` du client OIDC → callback réel de l'app externe (ex: `http://vikunja.../auth/openid/hub/callback`).

---

## [3.13.1] - 23 juillet 2026

### 🆕 Feat — `/oidc/authorize` standard + cookie session HUB pour SSO OIDC complet

- **Ajouté** : endpoint `GET /oidc/authorize` (RFC 6749 §4.1.1 + OIDC Core §3.1.2) — le vrai endpoint OIDC Authorization standard que les clients type Vikunja attendent. Identifie l'user via un cookie `hub_oidc_session` (et non plus via le JWT HUB en query string), génère un code OIDC one-shot lié au state/nonce/PKCE fournis par le client, redirige vers `redirect_uri?code=...&state=...`.
- **Ajouté** : cookie de session `hub_oidc_session` posé au login (grant_type=password) — JWT RS256 court (8h, type `oidc_session`, signé clé HUB), httpOnly, samesite=lax, secure selon `HUB_HTTPS_ENABLED`. Permet à `/oidc/authorize` de savoir qui est connecté sans re-login, comblant le gap « pas de session cookie navigateur » du HUB.
- **Corrigé** : `authorization_endpoint` dans le document discovery pointe désormais vers `/oidc/authorize` (standard) au lieu du shortcut `/api/oidc/launch/{client_id}` qui était incompatible avec le flow OIDC standard de Vikunja (elle génère son propre state/nonce et initie le flow elle-même — le shortcut court-circuitait ces étapes).
- **Ajouté** : variables `OIDC_*` exposées au conteneur `hub-api` dans `docker-compose.yml` (8 vars d'interpolation `${OIDC_*}` depuis `.env`), sans lesquelles `settings.oidc_*` restait vide en runtime.
- **Tests** : 12 nouveaux tests (8 endpoint authorize + 4 cookie session helpers). Suite OIDC complète : 48/48.

### ⚠️ Notes

- Le shortcut `/api/oidc/launch/{client_id}` reste disponible pour les clients OIDC custom qui l'appellent consciemment, mais **n'est plus le chemin recommandé** pour les apps OIDC standard. Les apps comme Vikunja doivent utiliser `/oidc/authorize` (découvert automatiquement via `/.well-known/openid-configuration`).
- Le `callback_url` des apps HUB existantes pointant vers le shortcut `/api/oidc/launch/...` doit être updaté pour pointer vers la page de login de l'app externe (ex: `http://vikunja.../auth/openid/auth`) afin que l'user déclenche le flow OIDC standard.

---

## [3.13.0] - 23 juillet 2026

### 🆕 Feat — Bridge OIDC Provider (SSO pour apps externes type Vikunja)

- **Ajouté** : module OIDC Provider dans `HUB_base` permettant aux apps externes (Vikunja, Nextcloud, Grafana, etc.) de consommer le SSO HUB via OIDC standard, **sans modifier le SSO JWT maison** existant pour les apps template. Implémentation from-scratch avec `pyjwt[crypto]` déjà présent (pas de nouvelle dépendance).
- **Modèle de données** : 3 nouvelles tables via migrations `039_add_oauth_clients.sql`, `040_add_oauth_authorization_codes.sql`, `041_add_oauth_refresh_tokens.sql` :
  - `oauth_clients` : clients OIDC (client_id, client_secret_hash bcrypt, redirect_uris whitelist, scopes, backchannel_logout_uri, FK optionnelle vers applications).
  - `oauth_authorization_codes` : codes one-shot (TTL 30s, hash SHA256, PKCE, nonce).
  - `oauth_refresh_tokens` : refresh tokens persistants et **révocables** (mirroir du mécanisme apps hub pour le DC passif).
- **Endpoints** (`hub/app/routes/oidc.py`) :
  - `GET /.well-known/openid-configuration` : discovery document OIDC standard.
  - `GET /api/oidc/launch/{client_id}` : **shortcut spécifique HUB** — valide un JWT HUB (query string), vérifie `UserAppRole`, génère un code OIDC one-shot, redirige 302 vers `redirect_uri`. Évite l'ajout d'un cookie de session navigateur au dashboard.
  - `POST /api/oidc/token` : échange code → `id_token` + `access_token` + `refresh_token` (grants `authorization_code` avec PKCE S256 + `refresh_token` avec rotation). Auth client via `client_secret_basic` (Authorization header) OU `client_secret_post` (form body).
  - `GET/POST /api/oidc/userinfo` : claims filtrés par scope (`sub` toujours ; `preferred_username`/`name` sur `profile` ; `email`/`email_verified` sur `email`).
  - `POST /api/oidc/backchannel-logout/{client_id}` : **admin HUB only** — révoque tous les refresh tokens d'un user pour ce client + envoie un `logout_token` JWT signé au `backchannel_logout_uri` du client (OIDC Back-Channel Logout 1.0).
  - CRUD `/api/oidc/clients` (admin only) : list/create/update/toggle-active/regenerate-secret/delete. Le `client_secret` brut est retourné **une seule fois** à la création/régénération (jamais persisté en clair, hash bcrypt en base).
- **Helpers** (`hub/app/oidc_helpers.py`) : `create_id_token`, `create_access_token_jwt`, `generate_authorization_code`, `generate_refresh_token`, `generate_logout_token`, `validate_pkce` (S256), `compute_email_claim` (template `{username}@hub.internal`), `build_oidc_metadata`. Tous signent JWKS RS256 avec `kid="hub-jwt-key-1"` (cohérent avec l'endpoint JWKS existant).
- **Sécurité** :
  - `client_secret` hashé bcrypt (`get_password_hash()`), jamais sérialisé en API.
  - Codes OIDC one-shot (`used_at` + TTL 30s), stockés uniquement par hash SHA256.
  - PKCE S256 obligatoire côté validation (optionnel si absent côté client).
  - `id_token`/`access_token` stateless signés RS256 (court TTL).
  - Refresh tokens persistants, **révocables** (DC passif) + rotation à chaque usage.
  - Back-channel logout avec `logout_token` signé (claims `iss/aud/iat/jti/events`, pas de `nonce`).
  - **Rate limiting** sur `/api/oidc/token` : 10 req/min/IP (anti brute-force `client_secret`) → 429 si dépassé.
  - `redirect_uri` validé strictement (whitelist par client, égalité exacte).
- **Claims mapping** : `sub` = `User.username` (stable), `email` = synthétique `{username}@hub.internal` (configurable via `OIDC_EMAIL_TEMPLATE`), `preferred_username`/`name` = `username`, `email_verified` = `true`.
- **Settings** : 8 nouveaux attributs `oidc_*` dans `Settings` (`oidc_enabled`, `oidc_issuer`, `oidc_email_template`, `oidc_code_ttl_seconds=30`, `oidc_id_token_ttl_minutes=10`, `oidc_access_token_ttl_minutes=15`, `oidc_refresh_token_days=30`, `oidc_backchannel_logout_enabled`) + section correspondante dans `.env.example`.
- **Frontend** : nouvelle page admin `admin-oidc-clients.js` (vanilla JS + Bootstrap 5, pattern `admin-service-accounts.js`) intégrée au menu admin (onglet « Clients OIDC »). CRUD complet + modal credentials avec secret one-shot display + boutons copie namespacés (`copyOidcValue` pour éviter la collision avec la globale `copyToClipboard`).
- **Tests** : 558 tests au total (suite HUB_base + OIDC). Couverture OIDC : modèles (5), helpers (12), discovery (2), launch (7), token (10 dont 5 security branches : basic auth, expired code, redirect mismatch, PKCE fail/optional), userinfo (4), backchannel-logout (3), clients CRUD (6).
- **Documentation** : design spec détaillé dans `docs/superpowers/specs/2026-07-22-vikunja-hub-integration-design.md` (14 sections), plan d'implémentation dans `docs/superpowers/plans/2026-07-22-plan-1-bridge-oidc-hub-base.md` (11 tâches TDD).

### 🔧 Tech — conventions et réutilisation

- **Aucune nouvelle dépendance Python** : réutilisation de `pyjwt[crypto]`, `cryptography`, `bcrypt` (déjà présents). Approche C (from-scratch) préférée à authlib après découverte qu'authlib n'a pas d'intégration FastAPI provider native (aurait requis un adaptateur Starlette + `run_in_threadpool` + 2 dépendances pour le back-channel logout manuel de toute façon).
- **Réutilisation du socle** : `verify_token()`, `get_current_admin`, `hash_token()` (SHA256), `get_password_hash()`/`verify_password()` (bcrypt), `settings.jwt_private_key`/`jwt_public_key`/`kid="hub-jwt-key-1"`, JWKS endpoint existant, `log_action()` + commit explicite.
- **Conventions parc respectées** : `DateTime`+`utcnow` (pas TIMESTAMPTZ), `TIMESTAMP`+`DEFAULT CURRENT_TIMESTAMP` en SQL, `IF NOT EXISTS` idempotent, `lazy="raise"` sur les relations, migrations numérotées 039/040/041.

### ⚠️ Notes de migration

- **Pas de modification** du modèle `User` existant ni des apps template actuelles. Le bridge OIDC est purement additif.
- **Config requise en prod** : renseigner `OIDC_ISSUER=https://hub.tondomaine.tld` dans le `.env` (vide par défaut, bloque la signature des id_tokens si absent — le claim `iss` serait vide).
- **Follow-ups non-bloquants** (trackés pour Plan 2 / cleanup) : unify messages 401 (anti client-enumeration), `log_action` sur update/toggle-active, tests 404/update endpoint, cleanup `generate_logout_token` hygiene, wire `oidc_enabled` (kill-switch global), POST userinfo test, redaction `access_token=` des access logs `/launch` (pré-existant).

---

## [3.12.3] - 21 juillet 2026

### 🐛 Fix — fresh install cassé par NotNullViolation sur m2m_enforcement_mode

- **Corrigé** : le fresh install (DB vierge) échouait sur la migration `026_add_backup_clusters_and_system_app.sql` avec `NotNullViolationError: null value in column "m2m_enforcement_mode"`. Cause : la colonne était déclarée `nullable=False` avec un `default='compat'` Python uniquement (pas de `server_default`), donc `Base.metadata.create_all()` (qui tourne avant les migrations) créait la colonne NOT NULL **sans default SQL** ; la migration 026 (INSERT de l'app système "Hub") ne fournissait pas cette colonne → violation. Le bug n'apparaissait pas sur DB existante (la migration 038 `ADD COLUMN ... DEFAULT 'compat'` s'y appliquait), uniquement sur DB vierge.
- **Fix** : ajout de `server_default='compat'` sur `models.Application.m2m_enforcement_mode`. `create_all()` crée désormais la colonne avec un default SQL ; la migration 026 et tous les INSERT SQL directs fonctionnent.
- **Note** : d'autres colonnes NOT NULL (`is_system_app`, `backup_quiesce`) ont le même pattern (`default=` Python sans `server_default`) mais sont systématiquement fournies dans les INSERT existants → non impactées. À surveiller pour toute nouvelle migration INSERT.

---

## [3.12.2] - 21 juillet 2026

### ⚡ Sécurité M2M — extension du garde SA/slug à 3 routes supplémentaires

- **Sécurité** : le garde `authorize_service_account_slug()` est désormais appliqué à `PUT /api/apps/me/shared`, `POST /api/apps/me/openapi` et `POST /api/messages/m2m/send` (partage, spec OpenAPI, synchronisation). Les slugs non possédés sont audités en mode `compat` (défaut) et refusés en `enforced` (403) — conformément au design Lot 1 « Autorisation M2M centralisée ».
- **Sécurité** : `/messages/m2m/send` ne retombe plus sur `entity.name` comme slug quand le SA n'a aucune application ; un SA sans app ne peut plus émettre de message sous un slug arbitraire.
- **Corrigé** : `POST /me/openapi` respecte désormais le `X-Slug` fourni (quand il appartient au SA) au lieu de toujours cibler la première application du SA.
- **Non couvert (volontaire)** : `/me/credentials` (le `X-Slug` n'est pas utilisé en branche token, pas de fuite cross-app) et `POST /api/apps/{slug}/backup` (déjà protégé strictement par `check_backup_entity_access`, sans mode compat).
- **Tests** : +6 tests (nominal possédé + rejet en enforced pour chaque nouvelle route gardée). 504 passed, 1 échec préexistant non lié (PKI/HTTPS dépendant de l'environnement).

---

## [3.12.1] - 19 juillet 2026

### ⚡ Sécurité M2M et outbox — mode enforcement par application

- **Ajouté** : colonne `m2m_enforcement_mode` sur `applications` (`compat` par défaut, `enforced` opt-in).
- **Ajouté** : garde `authorize_service_account_slug()` — les slugs non possédés sont audités en `compat`, refusés en `enforced`.
- **Ajouté** : réinscription avec preuve M2M optionnelle (Bearer token). Sans preuve, le comportement legacy est conservé et audité. Avec preuve, l'app passe en `enforced`.
- **Corrigé** : outbox Hub — les routes `/api/outbox/*` exigent le rôle admin.
- **Corrigé** : outbox Hub — seules les réponses HTTP `2xx` sont considérées livrées ; les `4xx` restent en `failed` pour retry.
- **Corrigé** : `cleanup_old_messages()` commit désormais la purge.
- **Marquage** : chemins legacy tagués `🔶 LEGACY` dans `routes/apps.py` pour suppression après migration complète.
- **Tests** : +7 tests (M2M slug enforcement, outbox admin) ; 498 passed, 1 skipped.

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
