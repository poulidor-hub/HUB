# Changelog - Hub d'Authentification

> Ce fichier documente toutes les modifications apportées à la documentation et à l'API.
> Les agents IA peuvent utiliser `/api/docs/changelog` pour consulter cet historique.

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


### 🆕 Nouvelle version

> ⚠️ Cette entrée a été créée automatiquement par `sync_version.py`.
> Veuillez compléter les détails des modifications.

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

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
