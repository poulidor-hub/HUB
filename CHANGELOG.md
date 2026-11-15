## [3.91.5] - 15 novembre 2026

### 🐛 Préférences — modale admin et erreurs M2M

- **Sync depuis l'application** : la modale admin pouvait cliquer
  « Synchroniser depuis l'application » sur une catégorie restée en source
  CSV → 400 « pas configurée en source M2M ». La sauvegarde bascule
  désormais la source (csv ⇄ m2m) avec validation de l'application source
  (PATCH catégorie : nouveaux champs `source` / `source_app_id`).
- **Messages 502 actionnables** : discovery et import M2M renvoient
  « Application source injoignable (<url>) — vérifie qu'elle est déployée
  et démarrée » au lieu de l'erreur httpx brute.
- Tests : bascule csv→m2m, refus sans source_app, message 502.

---
# Changelog - Hub d'Authentification

> Ce fichier documente toutes les modifications apportées à la documentation et à l'API.
> Les agents IA peuvent utiliser `/api/docs/changelog` pour consulter cet historique.

---

## [3.91.6] - 15 novembre 2026

### 🐛 Sécurité M2M — heuristique de SA propriétaire en égalité exacte

- **Rotations croisées de secrets éliminées** : `is_owner_service_account`
  (et ses 2 variantes `_is_owner_service_account` + bloc « retire l'app des
  SA partagés ») testait l'appartenance par **sous-chaîne**
  (`app_slug in sa_name_lower`) : le register de `transmission` rotait le
  secret de « SA - Transmission Paie » (app `exemple-app`) et le livrait à la
  mauvaise stack → 401 en boucle sans issue self-service (V4, 15/11/2026 ;
  même classe que l'incident Philia Proxy → exemple-app-7).
- **Nouveau contrat** (`_sa_matches_owner_name`, partagé par les 3 sites) :
  égalité exacte insensible casse/espacements sur les formes canoniques —
  « SA - <App Name> », slug pur, slug-normalisé. Les familles d'apps aux
  noms préfixés (transmission/exemple-app/exemple-app, dipec-*, contrat-*)
  ne peuvent plus se voler leurs secrets.
- **Conséquence assumée** : les SA legacy nommés hors convention (ex.
  « SA - Outil de transmission » pour une app renommée depuis) ne sont plus
  reconnus comme propriétaires — le register crée un SA neuf au lieu de
  roter l'ancien (l'orphelin reste listé côté admin, sans risque de vol).
- Tests : +12 (cycle rouge/vert `test_sa_owner_matching.py`), non-régression
  owner-resolution (4) + register/API apps (24).

---

## [3.91.4] - 15 novembre 2026

### 🆕 Nouvelle version

> ⚠️ Cette entrée a été créée automatiquement par `sync_version.py`.
> Veuillez compléter les détails des modifications.

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.91.3] - 15 novembre 2026

### ⚡ Fix console notifications : le select-all couvre désormais toutes les pages + actions en masse graduées

- **Bug** : dans la console « Mes notifications » (Ma boîte comme Gestion), le checkbox « tout sélectionner » ne sélectionnait que les 20/50/100 lignes de la page affichée (`notifToggleSelectAll` itérait `_notifMessages`, la réponse paginée) et la sélection était en plus vidée à chaque changement de page ou de taille de page — impossible de traiter en masse au-delà d'une page (ex. 547 non lus affichés « 20 sélectionnée(s) »).
- **Fix select-all étendu** : au cochage, fetch paginé de **toutes les pages** matchant les filtres courants (`_notifFetchAllMatchingIds`, page_size au max backend 100, boucle sur `pages`) → sélection complète, barre affichant le total réel, toast d'information quand la sélection dépasse la page affichée. Au décochage : reset complet. La sélection **survit** désormais au changement de page/taille de page (les cases par ligne reflètent l'appartenance via `selection.has(id)`) ; elle reste vidée par changement de filtre (chips, source, recherche), de mode ou d'entrée dans la page. Header select-all synchronisé `checked`/`indeterminate` (`_syncNotifSelectAllHeader`, appelé depuis `_syncNotifSelectionBar`).
- **Graduation des actions en masse** : la sélection pouvant dépasser les caps backend (500 IDs max/requête sur `POST /messages/me/delete-batch` et `DELETE /messages/batch`), les actions partent en lots séquentiels de ≤500 (`_chunkArray`) avec totaux agrégés dans les toasts (suppression boîte `hidden`, archivage `affected`, hard delete `deleted`) ; « Marquer lu » (N POST unitaires) gradué par lots de 50 au lieu d'un `Promise.all` global pour ne pas saturer le serveur sur une grande sélection.
- **Test de régression** : `frontend/tests/test_notifications_select_all.mjs` (pattern autonome `test_resilient_download`, zéro dépendance) — 9 scénarios : fetch paginé inbox/Gestion, persistance page/pageSize, reset par filtre, unselect-all, header indéterminé, bulk read 120 gradué, bulk delete 750 en 2 lots (500+250), archivage et hard delete Gestion. Rouge sur l'ancien code, vert sur le fix. Aucun changement backend.

---

## [3.91.2] - 15 novembre 2026

### ⚡ Fix sécurité : headers de sécurité nginx effectivement envoyés (include factorisé)

- **Bug latent** : la sémantique nginx veut qu'une `location` définissant le moindre `add_header` hérite d'AUCUN `add_header` du bloc `server`. Les 4 locations de cache (`= /index.html`, assets hashés, `/`, regex `\.(css|js)$`) redéfinissant `Cache-Control`, toutes les réponses HTML/JS/CSS partaient **sans aucun header de sécurité** (CSP durcie, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy) depuis la création de la conf — vérifié au curl en prod (seules les locations proxy `/api/*`, sans `add_header`, héritaient encore).
- **Fix** : snippet factorisé `frontend/security-headers.conf` (les 5 `add_header`, valeurs inchangées, copié dans `/etc/nginx/templates/` par le Dockerfile) inclus au niveau `server` ET en tête de chaque location qui redéfinit `add_header`. Zéro modification de `docker-entrypoint.sh` (le snippet vit dans `templates/`, jamais auto-inclus au niveau http).
- **Vérification** : image reconstruite depuis le worktree et testée sur conteneurs jetables en HTTP (:18099) et HTTPS (:18444, cert auto-signé, `--add-host hub-api`) — les 5 headers + le Cache-Control de location présents sur `/`, `/index.html`, `/vendor/bootstrap.bundle.min.js` dans les deux modes ; pré-contrôle CSP sans risque applicatif (aucun script inline, aucune ressource externe dans le HTML servi).
- **Propagation** : même fix appliqué à HUBapp_TEMPLATE_base (confs + `Dockerfile.frontend`), HUBsocle (image socle-web + `INITIALISE.py` : `INFRA_FILES` + `DOCKERFILE_FRONTEND`) et HUBCSS (nosniff répété dans les locations). Les apps déjà déployées gardent leur conf nginx cuite dans l'image → migration ultérieure via `migrate-app-template`.

---

## [3.91.1] - 14 novembre 2026

### ⚡ Fix contraste des badges `bg-info` (text-dark)

- **Cause racine** : `.badge` impose `color: #fff` (bootstrap.min.css 5.3.2) et `bg-info` ne colore que le fond — tout contenu (glyphe ou texte) s'affichait en blanc sur cyan, quasi invisible à 0.75em. Le cas signalé : le glyphe `bi-share` du badge « API partagée » (admin-apps.js:588), interprété à tort comme un échec de chargement de la police bootstrap-icons (exclu : `\f52e` présent dans le woff2 vendored, servi 200, aucun header CSP effectivement envoyé — cf. héritage `add_header` nginx).
- **Fix** : ajout de `text-dark` sur 12 badges `bg-info` (admin-apps, audit-errors ×3 dont badge code 404, app-backups, backup-import ×3, admin-docker-scanner, admin-user-tags, index.html ×2) — harmonisation sur le pattern `bg-info text-dark` déjà utilisé ailleurs (admin-vault, notifications, admin-routing, admin-users-profiles).
- **Hors périmètre** : le `modal-header bg-info text-white` (index.html) est conservé (choix de design grand format).

---

## [3.91.0] - 14 novembre 2026

### 📣 Pour les utilisateurs

- **Ajouté** : dans les Préférences, vous choisissez désormais les secteurs que vous gérez (renfort / binôme inclus), puis désignez votre **secteur principal** parmi eux.
- **Amélioré** : la fenêtre de bienvenue ne réapparaît plus à chaque connexion une fois vos choix enregistrés ; les secteurs proposés sont actualisés en direct depuis exemple-app (et restent modifiables même si elle est momentanément injoignable).
- **Corrigé** : la sauvegarde des préférences ne bloque plus si un secteur a été renommé côté exemple-app — les valeurs devenues invalides sont retirées proprement avec un simple avertissement.

### ⚡ Refonte préférences : secteurs gérés + secteur principal

| Fonctionnalité | Description |
|----------------|-------------|
| **Migration 068** | `user_tags.is_primary` + `tag_categories.primary_enabled` ; index partiel unique `uq_user_tags_primary` (une seule primaire par utilisateur/catégorie). Idempotente. |
| **PUT /api/tags/my** | Upsert diff (fini delete-all + réinsertion, `created_at` préservé), payload enrichi `primary` (absent = inchangé, `null` = suppression), valeurs obsolètes **filtrées en 200** (`ignored`), 400 réservé à la primaire invalide. La réponse resynchronise le cache front. |
| **Valeurs live** | `GET /api/tags/categories/active` fetch les valeurs M2M de l'app source (token service 5 min, timeout 3 s, cache mémoire TTL 60 s, negative-cache 15 s pendant une panne pour éviter la cascade de timeouts, fallback snapshot + `source_flag: live|cache|csv`, snapshot DB rafraîchi au succès). |
| **Onboarding serveur** | `GET /api/tags/my?with_reason=1` calcule `onboarding_reason` (priorités : `newcat` → `primary_lost` → `primary_missing` → `onboarding` → `None`) ; suppression du `sessionStorage` — choix complet posé = plus jamais de modale. |
| **JWT** | Nouveau claim `tags_primary` émis à côté de `tags` sur les 7 points d'émission de tokens user (login, refresh ×2, redirect, app-launch, sso, approbation de compte) — émis seulement si non vide, backward compatible. |
| **Endpoint M2M** | `GET /api/tags/user/{username}` renvoie désormais `primary` (consommation « fraîche » hors JWT). Admin : `PUT /api/tags/user/{username}` en upsert diff + primaire ; catégories actives avec `source_flag` + `primary_enabled` ; PATCH catégorie `primary_enabled`. |
| **Frontend** | Modale 2 temps (① secteurs chips → ② radios principal parmi les cochés, bouton désactivé sans ①+②), états `primary_missing`/`primary_lost` réduits avec bandeau warning, page Mes préférences (primaire ★ + autosave enrichi + toast `ignored` + bandeau cache), case « autoriser un secteur principal » dans l'admin des catégories. |
| **Tests** | +34 tests (résolveur live/cache/dédup/negative-cache, upsert diff, raisons d'onboarding, JWT, M2M, gel de contrat primaire filtrée). Suite complète : 1741 passed (hôte). Review indépendante : 1 Important fixé (negative-cache), 5 Minor (2 fixés, 3 différés documentés). |

---

## [3.90.3] - 14 novembre 2026

### ⚡ Tri alphabétique des profils utilisateur

| Fonctionnalité | Description |
|----------------|-------------|
| **fix(users)** | La colonne « Profil » de la vue utilisateurs (et la modal « Appliquer un profil ») affiche désormais les profils triés par nom, quel que soit l'ordre d'attribution des droits. |
| **Implémentation** | `order_by="Profile.name"` sur la relation SQLAlchemy `User.profiles` (models.py) → tous les `selectinload` (liste, détail, PATCH, création) trient en SQL. `.order_by(User.username, Profile.name)` sur la requête du endpoint M2M bulk-roles (cohérence apps consommatrices). |
| **Tests** | 2 tests de régression (liste admin + endpoint M2M), cycle rouge/vert. Suite complète verte : 1708 passed (hôte), 1641 passed (Docker pouly-test). |

---

## [3.90.2] - 14 novembre 2026

### 📣 Pour les utilisateurs
- **Corrigé** : le Hub en HTTPS profite maintenant lui aussi du chargement plus rapide (cache des fichiers statiques et bibliothèques servies localement).

### ⚡ Fix : harmonisation nginx-ssl.conf avec nginx.conf (front HTTPS)

- **Cache 7 jours des assets versionnés `?v=`** en HTTPS (`Cache-Control: public, max-age=604800, stale-while-revalidate=86400`) — la version SSL du frontend n'avait aucune des optimisations de la 3.90.0 (les utilisateurs réels passent par HTTPS).
- **Pas de header de cache sur les 404** (`always` retiré des locations hashed-assets et css/js) — un asset manquant ne peut plus être mis en cache 7 jours par les navigateurs.
- **`location ^~ /api/`** — une route API future terminant en `.js`/`.css` ne peut plus être capturée par la location statique.
- **CSP sans `cdn.jsdelivr.net`** (script/style/font `self`) — les bibliothèques sont vendored localement depuis la 3.90.0 ; l'origine externe n'avait plus de raison d'être autorisée.

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.90.1] - 14 novembre 2026

### ⚡ Fix : gitigne `secrets/atelier_token` — fin du statut « arbre sale » permanent

- **`secrets/atelier_token` ajouté au `.gitignore` versionné** (à côté de `secrets/hub_instance_id`) : token runtime créé hors git par `hubctl init` (`hubctl_cli/actions.py:553`), jusqu'ici couvert uniquement par un contournement local non versionné (`.git/modules/HUB_base/info/exclude`), perdu au re-clone.
- **Effet** : le service d'update qui calcule le statut sur le porcelain brut remontait un état « sale » permanent pour HUB_base dans l'UI Mises à jour (remède « committer ou nettoyer » inapplicable à un secret) — l'entrée versionnée survit au re-clone (prouvé en clone frais @ 8e3e385b).
- **Complément hors dépôt** : l'harmonisation du dirty-check (filtrage des non-suivis) est faite côté hubctl (`Hubctl` 0.18.5, `update_serve.py` + `hub_state.py` alignés sur `park_checks._dirty_tracked`).

---

## [3.90.0] - 14 novembre 2026

### 📣 Pour les utilisateurs
- **Amélioré** : ouverture des applications nettement plus rapide aux heures de pointe (plusieurs secondes gagnées par ouverture).
- **Amélioré** : le portail Hub se charge désormais entièrement depuis le serveur (plus d'attente sur des ressources externes) et réutilise les fichiers déjà téléchargés.
- **Corrigé** : les sessions multi-onglets restent stables lors des rafraîchissements simultanés.

### ⚡ Perf : fin du goulot CPU sérialisé à l'ouverture des apps (80+ utilisateurs)
- **Cache mémoire de la clé privée RSA** (`jwt_keyring.py` : `_private_key_cache` + `get_active_private_key()`) — fin de la relecture disque + re-parse à chaque mint (~250 ms/mint sur RSA-4096) ; purge couvre `reload()` et toute mutation du manifest (`_save_entries`).
- **bcrypt + secret client en thread pool** (`verify_password_async` via `asyncio.to_thread`, 10 sites + `check_client_secret` OIDC) — plus aucun gel de l'event loop.
- **Migration 067** : purge automatique de `oauth_authorization_codes` (>1 h) et `oauth_refresh_tokens` (expirés) dans `cleanup_expired_tokens` + index hot-path : `refresh_tokens(user_id)`, `user_sessions(user_id) WHERE logout_at IS NULL`, `expires_at` des 2 tables OAuth.
- **Rotation du refresh token en une seule transaction** (`create_refresh_token` : commit intermédiaire → flush) — crash-consistance améliorée (révocation + remplaçant committés ensemble) ; période de grâce inter-onglets préservée (test de re-délivrance).
- **Throttle 5 s du broadcast SSE `stats_updated`** (fenêtre ancrée + flush final garanti ; `notify_event`/`send_to_user` ciblés non throttlés) — casse les tempêtes auto-amplifiées post-login.
- **Frontend** : bootstrap@5.3.2/bootstrap-icons@1.11.2/echarts@5.5.1 vendored localement (fin du CDN jsdelivr bloquant, SRI retirés car git-épinglés) + cache 7 jours des assets versionnés `?v=` (`Cache-Control: public, max-age=604800, stale-while-revalidate=86400`, sans `always` → les 404 ne sont pas cachables) + `location ^~ /api/` ; `index.html` reste no-cache.
- **Vérifié en stack de test isolée (`pouly-test`)** : jwks cold 40 ms → warm 1 ms ; `POST /launch` 30 ms ; burst 20 refreshes moyenne 14,9 ms (rotation single-commit confirmée en DB) ; migration 067 + 4 index appliqués sur volume frais ; suite in-container 1639 passed (1 échec préexistant `origin/main` : chmod root).

---

## [3.89.0] - 14 novembre 2026

### ⚡ Admin : graphe des dépendances inter-apps lisible + vue matrice

- **Ordre des nœuds par barycentre** (`depsComputeOrderedColumns`) : balayages
  alternés bas→haut/haut→bas, tiebreak alphabétique — remplace le tri
  alphabétique qui maximisait les croisements. Sur les 30 dépendances
  actuelles : 88 → 39 croisements, rendu déterministe (même donnée ⇒ même graphe).
- **Points d'ancrage des arêtes étalés** (`depsSpreadAnchors`) : les k arêtes
  d'un nœud sont réparties le long de son bord (triées par position de leur
  extrémité opposée) au lieu de partir toutes du point médian — les faisceaux
  se séparent dès la sortie du nœud.
- **Hiérarchie visuelle de fraîcheur** : épaisseur 2.4/1.7/1.1 et opacité 0.9/0.9/0.72
  pour frais/périmé/jamais — le synchro récent ressort même quand l'essentiel
  est rouge. Dimming de sélection inchangé (0.15).
- **Bascule Graphe / Matrice** (`btn-group` à côté d'« Actualiser ») :
  matrice d'adjacence sources × cibles, cases colorées par fraîcheur, cible
  hors registre en contour pointillé, tooltip réutilisé (`depsTooltip` via
  `data-deps-edge`, zéro nouveau listener). Défaut = Graphe, état non persisté.
- Sélection nœud (clic graphe ou ligne du tableau) re-render la vue courante
  (`depsRenderMainView`) — plus d'écrasement de la matrice par le graphe.
- Front uniquement (`admin-app-dependencies.js` + `index.html`) : pas de lib,
  pas d'animation (charte perf/stabilité), hover tooltip/clic/tableau inchangés.

---

## [3.88.3] - 14 novembre 2026

### ⚡ VPN : `restart: unless-stopped` — le tunnel survit aux restarts du daemon docker

- Incident du 13/11 : restart du daemon docker (ou reboot) à 05h59 UTC →
  `hub-wireguard` (policy `"no"`) jamais relancé → **tunnel mort 30 h**,
  accès mobile impossible. Le boot hook systemd `hub-vpn-sync` ne couvre
  que les boots de l'hôte, pas les restarts du daemon.
- `wireguard-server` passe en `restart: unless-stopped` : honore un
  `docker stop` explicite (hubctl vpn down — le conteneur arrêté volontairement
  reste arrêté) tout en relançant le tunnel après un restart daemon ou un
  reboot. Le cycle de vie DB (`vpn_enabled` via `hubctl vpn`) reste inchangé.
- Corrige au passage le commentaire du compose : port réel 51821/udp
  (`vpn_listen_port`), pas 51820.

---

## [3.88.2] - 14 novembre 2026

### ⚡ Hygiène tests : éradication des `get_settings.cache_clear()` résiduels

- `test_vpn.py` : les 4 `cache_clear()` (fixture `vpn_temp_dir`,
  `test_download_config`) remplacés par un patch ciblé du singleton via
  `monkeypatch.setattr` — les routes VPN lisent la config à la requête
  (`vpn.py:136,239,263`), le patch suffit et se restaure au teardown.
  Le `cache_clear` y jouait un rôle de pansement accidentel : il masquait
  une fuite d'état (voir ci-dessous).
- Dette cachée révélée par la suppression du pansement :
  `test_github_catalog.py` mutait `settings.github_token` sans snapshot →
  fuite vers `test_workshop_service::test_github_token_from_db`
  (`deploy_service._get_github_token` fait gagner le champ sur la DB).
  Fix : `github_token` ajouté au snapshot de la fixture `settings` (conftest).
- Vérifié sans risque : `get_instance_id.cache_clear()` de test_config_ports
  (aucune capture module-level, preuves grep) ; pattern documenté de
  `test_pki_wildcard.py` inchangé.
- Suite complète : 1685 passed, 1 skipped, 0 failed (comptage identique
  à la 3.88.1). Test-only — zéro code runtime modifié.

---

## [3.88.1] - 14 novembre 2026

### 📣 Pour les utilisateurs
- **Fiabilité** : la suite de tests automatiques du Hub est de nouveau 100 % verte
  (1685 tests). Les prochaines mises à jour sont mieux protégées contre les
  régressions silencieuses — aucune fonctionnalité n'est modifiée dans cette version.

### ⚡ Fix tests : 6 échecs pré-existants corrigés (suite complète verte)

- `test_app_deploy` ×2 : le helper d'allocation ancre désormais la
  `PortAllocation` sur le host courant du hub (`get_settings().get_hub_host()`)
  au lieu d'un host arbitraire — l'idempotence slug+host de l'allocateur est
  de nouveau exercée (9002 / 10.50.0.7/24 réutilisés).
- `test_vpn_dns::test_get_dns_line_with_zone` : override explicite de
  `settings.hub_host` (RFC 5737) via la fixture `settings` + `hub_host` ajouté
  aux champs snapshot/restaurés par la fixture (il n'était jamais restauré).
- Pollution d'état (oidc_discovery, oidc_authorize, slo_login) : cause prouvée —
  `test_apps_host_path_env_override` appelait `get_settings.cache_clear()`,
  désynchronisant les ~10 singletons Settings capturés à l'import. Le test
  instancie `Settings()` directement (pattern `test_hub_host.py`).

---

## [3.88.0] - 14 novembre 2026

### ⚡ Audit erreurs : colonne `attempted_client_id` — identifier l'appelant d'un échec d'auth M2M

- `error_logs` gagne `attempted_client_id VARCHAR(255) NULL` (migration 066,
  modèle `ErrorLog` enrichi).
- Les 3 sites d'échec M2M de `/api/auth/token` (401 identifiants invalides,
  403 SA désactivé, 429 rate-limit) attachent le `client_id` tenté à la
  `HTTPException` ; le handler global le transmet à `_log_error_to_db`
  (jamais le client_secret). Les autres grants ne tracent rien (NULL).
- GET /api/audit/errors expose le champ ; l'UI (colonne « Utilisateur »)
  affiche un badge « M2M tenté » + client_id tronqué quand seul ce champ
  identifie l'appelant.
- Motivation : la boucle de 186 × 403 « Service account désactivé » du
  10-13/11 n'était pas attribuable depuis error_logs (ni client_id ni body).

---

## [3.87.9] - 14 novembre 2026

### ⚡ Fix UI : collision globale `renderPagination` — « undefined » affiché dans les paginations

- `admin-vpn.js` redéfinissait `renderPagination(totalUsers, totalPages)` (mutateur DOM,
  ne retourne rien) et, chargé en dernier dans `index.html`, écrasait la version
  HTML de `audit.js:71` (`renderPagination(currentPage, totalPages, functionName)`).
- Conséquence : `pagination.innerHTML = renderPagination(...)` écrivait la chaîne
  `"undefined"` dans le DOM sur 4 pages : onglet Erreurs (audit-errors.js:215),
  Logs détaillés (audit-page.js:809), détail d'app (audit-page.js:674),
  profils utilisateurs (admin-users-profiles.js:117).
- Fix : renommage de la variante VPN en `updateVpnPagination` (définition + appel,
  2 lignes) — une seule définition globale de `renderPagination` subsiste.

---

## [3.87.8] - 14 novembre 2026

### ⚡ Allocateur : trace l'épinglage dans les pools daemon Docker (F15)

## [3.87.8] - 14 novembre 2026

### ⚡ Allocateur : trace l'épinglage dans les pools daemon Docker (F15)

- `is_in_daemon_default_pool()` : détection CIDR ⊆ 172.16/12 ou 192.168/16
  (pools PAR DÉFAUT du daemon).
- `reconcile_subnet_with_docker` : warning explicite + consigne de
  migration quand le réseau vivant d'une app est épinglé dans ces pools
  (chevauchement avec les pools de la machine hôte — claims
  172.20-23/16 et 192.168.x/20 constatés en production au test PRA VM).

---

## [3.87.7] - 14 novembre 2026

### ⚡ Restore snapshot en streaming — PRA fonctionnel pour les snapshots multi-Go

Fixes issus du test PRA réel sur VM Debian vierge (snapshot 3,5 Go, 13/11/2026) :
l'ancien chemin de restauration était bloqué par deux pannes systémiques
(OOM memcg du conteneur hub-api à 2g, puis dépassement int32 du backend
openssl sur le déchiffrement one-shot). Aucun snapshot multi-Go n'était
restorable — en VM comme en prod.

- `snapshot_crypto.py` : `decrypt_snapshot_to_file()` — déchiffrement en
  chunks de 4 Mo (symétrique de `encrypt_file`, format byte-compatible,
  boucle candidates + `finalize_with_tag`), `decrypt_snapshot_bytes`
  conservé pour les petits snapshots.
- `restore_snapshot.py` : pipeline `zstd stream_reader → tarfile r|`
  (aucun tar intermédiaire sur disque), purge du ciphertext déchiffré et
  du tar.zst dès consommés (pic disque ~17 Go → ~8 Go), extraction
  `_extract_tar_archive()` en filter `tar` (symlinks absolus `socle`
  des apps préservés et loggés — Python 3.12 les refusait), purge du
  contenu d'APPS_SOURCE avant extraction apps_code (résidus `.git`
  read-only d'un run partiel).
- Tests : `hub/tests/test_snapshot_crypto_streaming.py` — 9 tests
  (roundtrip multi-chunks, sans clé, corruption GCM, repli keyring,
  compat one-shot, pipeline zstd→tar streamé, symlinks absolus,
  purge résidus RO). 1673 passed, suite complète.

| Fonctionnalité | Description |
|----------------|-------------|
| **Restauration PRA** | Les snapshots multi-Go (3,5 Go réels) sont restaurables : déchiffrement streaming + pipeline zstd, fin des OOM et du blocage openssl |
| **Robustesse extraction** | Symlinks absolus des apps et résidus read-only gérés sans échec d'extraction |

---

## [3.87.6] - 13 novembre 2026

### ⚡ Allocation ports/subnets : revendications .env de la flotte

Bug 13/11/2026 : l'ajout d'intendance a ré-attribué 10.50.16.0/24 (claim .env de contrat-w-pr, réseau prunable) et le port API 9019 (claim exemple-app, conteneurs down au moment de l'allocation) → « Pool overlaps with other one » puis « port is already allocated » au premier make up. Cause racine : l'allocateur ne consultait que port_allocations (la flotte legacy n'y figure jamais — 4 lignes seulement) et les scans Docker, qui ne voient que le vivant.

- Nouveau hub/app/fleet_env_scanner.py : lit les .env de la flotte (montage apps_host_path) → (sous-réseaux, ports) revendiqués ; un seul niveau de dossiers, fichier exact .env uniquement (sauvegardes .env.pre-* ignorées), valeurs invalides ignorées
- port_allocator.py : fleet_env_occupied() (thread, host local uniquement, flag fleet_env_scan_enabled, best-effort jamais levé) fusionnée dans les deux chemins d'allocation — backfill rétroactif du subnet et nouvelle allocation (ports via extra_used, subnets directs)
- config.py : flag fleet_env_scan_enabled: bool = True
- Tests hub/tests/test_fleet_env_scanner.py (8) : parsing, cas invalides, sauvegardes ignorées, intégration allocateur (cycle rouge → vert vérifié : sans correctif, l'allocateur réattribue 10.50.0.0/24)
- Non-régression : 388 tests passants ; test_app_deploy::test_run_deployment_success en échec préexistant sur main (écriture /code impossible hors conteneur — environnemental, hors scope)

---

## [3.87.5] - 13 novembre 2026

### ⚡ Dimensionnement du pool DB hub-api (tempêtes d'ajout d'app)

Incidents 13/11/2026 : l'auto-register (11:09) puis l'approbation (11:18) de `exemple-app` ont déclenché des tempêtes de requêtes (broadcast SSE → vagues `/api/apps/pending` ~1/s, rafales `GET /api/apps/`, herds `POST /api/auth/token/refresh`, `unread-count` jusqu'à 123/min) dont la demande concurrente de sessions a dépassé le pool SQLAlchemy 5 + 10 = 15 connexions → `QueuePool TimeoutError` après 30 s d'attente → 500 en cascade pendant ~50 s (139 erreurs pool en 24 h, readtimeout M2M côté app qui s'enregistre). Diagnostic : aucune fuite de connexion (sessions toutes fermées correctement dans `get_db`, pipeline deploy, ateliers, SSE) — pur sous-dimensionnement face aux pics ; hub-db disposait de 100 `max_connections` avec ~7 utilisées.

- `hub/app/database.py` : `pool_size` 5 → 25, `max_overflow` 10 → 25 (plafond 50, marge conservée sous `max_connections` PG = 100) via constantes `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`
- Nouveau `hub/tests/test_database_pool_config.py` : couverture tempêtes (pool ≥ 20 + overflow ≥ 20), plafond global ≤ 60, conformité de l'engine réel aux constantes (cycle rouge → vert vérifié dans le conteneur hub-api)
- Non-régression : 67 tests auth (`token/refresh` inclus) passants

---

## [3.87.4] - 13 novembre 2026

### ⚡ Réconciliation des fourches 028/029 après restore legacy

Un dump hub legacy (hub-legacy 3.9.x) marque 028 = `create_hub_snapshots` et 029 = `add_snapshot_settings` : les migrations homonymes du hub neuf n'étaient jamais rejouées après un restore → colonne `account_requests.email` manquante (crash runtime prouvé sur le hub 12/11/2026 : 44 tracebacks `UndefinedColumnError` en 48h) et tables `backup_clusters` résiduelles.

- `FORKED_MIGRATIONS` (hub/app/restore_snapshot.py) passe de `(version, table)` à `(version, type_contrôle, cible)` :
  - `027 table_absent port_allocations` (comportement historique inchangé) ;
  - `028 column_absent account_requests.email` — purge si marquée et colonne absente (table entière absente couverte aussi) ;
  - `029 table_present backup_clusters` — purge si marquée et tables legacy encore présentes (notre 029 doit les DROP).
- Même sémantique que `hubctl_cli/park_restore.py` (release Hubctl à suivre — accès écriture requis).
- Tests : 6 nouveaux cas SQLite dans `tests/test_restore_snapshot.py` (purge/no-op par type de contrôle, table absente, dump legacy complet 027+028+029) — 92 tests du périmètre restore/migrations passent.

---

## [3.87.3] - 13 novembre 2026

### ⚡ Rotation des logs Docker + snapshot apps_code sans artefacts

Diagnostic disque (13/11/2026) : `hub-docker-socket-proxy` accumulait 1,7GB de logs json en 5 jours (chaque healthcheck `GET /_ping` loggé par haproxy), sans aucune rotation ; le snapshot PRA du 08/11 pesait 3,5GB car `apps_code` embarquait les artefacts de build des APPS (target Rust 3GB, vendor 700MB, .docker/addok/data 2,3GB…).

| Fonctionnalité | Description |
|----------------|-------------|
| **Rotation logs compose** | Bloc `logging:` (json-file, max-size/max-file) ajouté aux services `db`, `docker-socket-proxy` (10m×3), `hub-dns`, `wireguard-server` de `docker-compose.yml` et aux services `db`, `docker-socket-proxy` de `docker-compose.test.yml` — tous les services du Hub ont désormais une rotation bornée. S'applique aux containers au moment de leur recréation (`docker compose up -d --force-recreate`). |
| **Snapshot apps_code filtré** | `snapshot_collectors.run_apps_code_tar` exclut désormais les répertoires d'artefacts (`__pycache__`, `node_modules`, `target`, `dist`, `build`, `.pytest_cache`, `.venv`, `venv`, `.next`, `coverage`, `.turbo`) et tout fichier individuel > 50MB (modèles gguf, addok.db… — les données utilisateur restent couvertes par `include_app_backups`). Réduction attendue d'un snapshot complet : ~3,5GB → ~0,5GB. Nouveau test `test_apps_code_tar_excludes_oversized_files_and_build_dirs`. |

---

## [3.87.2] - 12 novembre 2026

### 🐛 Fix : deadlocks PostgreSQL sur les fermetures SSE — ordre de verrouillage global sessions → segments

Bug du 12/11/2026 sur le hub 60 : 31 deadlocks PostgreSQL en 33 h
(`deadlock detected`, AB-BA `user_sessions` ↔ `app_usage_segments`),
fermetures de segments/sessions parfois perdues (`ended_at`/`logout_at`
NULL), warnings récurrents alimentant le compteur d'alerte
`db_write_errors_last_hour`, et messages « 📴 Session fermée » mensongers.

Cause racine (prouvée, cf. rapport du 12/11) : deux ordres de verrouillage
opposés sur les mêmes lignes —
- `handle_app_disconnect` (events.py) : tout est flushé au `commit()`, et le
  tri des mappers SQLAlchemy (`_sort_key` alphabétique :
  `app.models.AppUsageSegment` < `app.models.UserSession`) émet
  `UPDATE app_usage_segments` **avant** `UPDATE user_sessions` ;
- `end_session` (audit.py) : l'autoflush déclenché par le SELECT segments de
  `close_open_segments` émet `UPDATE user_sessions` **avant** les segments
  (signature « Query-invoked autoflush » des logs hub-db).

Correctif — ordre de verrouillage global unique : toute transaction touchant
la paire {session, ses segments} verrouille d'abord la ligne `user_sessions`
(`SELECT ... FOR UPDATE`), puis seulement les segments :

| Élément | Détail |
|---------|--------|
| **`hub/app/routes/audit.py`** | Nouveau helper `lock_session_for_close(db, session_id)` : re-sélection de la ligne avec `.with_for_update()` et retour de la ligne **rafraîchie sous verrou**. Appliqué à `end_session` et aux deux boucles de `cleanup_stale_sessions` (doublons + stalées). Fermetures de segments encapsulées dans `no_autoflush` (émission déterministe au commit, verrou déjà tenu). Fermeture idempotente : ligne déjà fermée sous verrou par un writer concurrent → no-op (et `end_session` retourne `None`, supprimant les fermetures mensongères). |
| **`hub/app/routes/events.py`** | `handle_app_disconnect` : même préambule `lock_session_for_close` avant `close_app_segment` — c'est le seul chemin « segments-first » (flush au commit), au cœur de l'AB-BA. |
| **`hub/app/usage_tracking.py`** | Filet : `close_open_segments` et `close_app_segment` ordonnent les segments par `id` (ordre déterministe des UPDATE multi-lignes). |
| Chemins inchangés | `start_session`, `update_session_activity`, `revoke_session`, `ensure_session_presence` émettaient déjà sessions → segments (autoflush) : plus aucun chemin segments-first, la classe de deadlock est éliminée (les segments d'une session ne sont accessibles qu'en tenant son verrou → sérialisation). |

Tests (TDD rouge/vert) :

| Test | Preuve |
|------|--------|
| **`hub/tests/test_sse_deadlock_lock_order.py`** (nouveau) | 5 tests : la première prise de verrou de `end_session`/`handle_app_disconnect`/`cleanup_stale_sessions` doit être un `SELECT user_sessions FOR UPDATE` (vérifié par recompilation du statement avec le dialecte PostgreSQL via l'événement `before_execute`, SQLite ignorant `FOR UPDATE`) ; aucun UPDATE session intercalé par autoflush avant le premier statement segments ; `ORDER BY app_usage_segments.id` émis ; régression fermetures successives. Rouge sans le fix (4/4), vert avec (5/5). |
| **`hub/tests/test_pg_deadlock_concurrency.py`** (nouveau, PG réel via `TEST_DATABASE_URL`) | Reproduit le scénario prod : `handle_app_disconnect` + `handle_user_disconnect` en parallèle sur la même session. **Pré-fix : `asyncpg.exceptions.DeadlockDetectedError` + session non fermée dès l'itération 3.** Post-fix : 20 itérations concurrentes, compteur `db_write_errors_last_hour` à zéro, tout fermé. |

Suites : hôte 1645 passed, conteneur (image runtime) 1574 passed ; échecs
restants pré-existants sur main (tests env-dépendants `test_app_deploy`,
`test_vpn_dns`, artefact conteneur éphémère `test_purge_orphan_backups`,
flaky xdist `test_oidc_authorize`) — identiques avec et sans le fix.

Post-déploiement (hub 60, surveillance 24 h attendue) : `deadlock detected`
et « Fermeture segment » à 0 dans les logs hub-db/hub-api, compteur
`db_write_errors_last_hour` retombé à 0, `app_usage_segments`/`user_sessions`
sans ligne ouverte > 1 jour.

---

## [3.87.1] - 12 novembre 2026

### 🐛 Fix : dashboard — modale de groupe lisible + groupes à une seule app affichés en carte directe

Deux défauts d'affichage du regroupement d'applications du dashboard
(`frontend/public/js/modules/dashboard.js`) :

| Élément | Détail |
|---------|--------|
| **Cartes écrasées dans la modale de groupe** | Les tuiles réutilisaient `col-sm-6 col-md-4 col-lg-3` : sur desktop, les breakpoints Bootstrap s'appliquant au viewport (et non à la largeur de la modale `modal-lg`), la modale affichait 4 colonnes de ~180 px — titres tronqués, descriptions écrasées, badges et sélecteur de rôle tassés. `renderAppTile` rend désormais `col-12 col-sm-6` quand `opts.inModal` est actif : 2 colonnes de ~390 px quel que soit le viewport (1 colonne sous 576 px). |
| **Carte de groupe inutile pour une seule app** | Un groupe ne comptant qu'une seule application visible pour l'utilisateur ouvrait une modale… pour une carte. `renderAppsGrid` éclate désormais ces groupes : la carte de l'app rejoint la grille (tri alphabétique) avec le badge du groupe conservé (`showGroupBadge: true`, la catégorie prime si elle existe, comportement identique à la vue filtrée). |

Vérifié en runtime (conteneur hub-frontend) : modale 800 px / 2 colonnes,
carte directe avec badge groupe, aucune régression sur les apps hors groupe
ni sur la vue filtrée. Aucun changement backend ni CSS additionnel.

---

## [3.87.0] - 11 novembre 2026

### ✨ Feat : onglet « Dépendances » — graphe des dépendances inter-apps dans le dashboard admin

La table Hub `app_dependencies` (déclarations `depends.yaml` poussées au
register par `_push_deps_to_hub`) n'avait aucune exposition UI : le endpoint
admin `GET /api/apps/dependencies` (`apps.py`) existait sans vue. Nouvel
onglet admin « Dépendances » (après « Applications ») :

| Élément | Détail |
|---------|--------|
| **`frontend/public/js/modules/admin-app-dependencies.js`** (nouveau) | Graphe SVG statique, sans lib externe ni animation (perf/stabilité) : nœuds classés appelants / mixtes / services appelés ; statut par nœud (active / inactive / **hors registre** — visualise immédiatement les cibles déclarées inexistantes au registre, ex. `exemple-apps` ou `exemple-app`) ; couleur d'arête par fraîcheur `last_sync_at` (≤ 7 j / 7–30 j / jamais) ; tooltip HTML (note métier, dates, nom d'app) ; clic nœud = focus des liens connexes. Tableau compact sous le graphe (source → cible, badge fraîcheur, dernière synchro, note), clic ligne = même focus. |
| **`frontend/public/js/api.js`** | `getAppDependencies()` sur le pattern `HubAPI.request`. |
| **`frontend/public/index.html`** | Onglet `#deps-tab` → pane `#tabDependencies` (statut, wrapper + tooltip, table), include du module. |
| **`frontend/public/js/modules/init.js`** | Dispatch `shown.bs.tab` → `loadDependenciesGraph()`. |

Backend inchangé (endpoint admin réutilisé tel quel). Aucune dépendance
nouvelle ; rendu `innerHTML` + `escapeHtml`, interactions par délégation
d'événements, aucun timer.

---

## [3.86.0] - 11 novembre 2026

### ✨ Feat : notifications actionnables — aperçu du contenu et lien d'action direct

Les notifications système n'offraient qu'un titre : le body (compteurs, seuils)
était caché dans le panneau de détail et aucune action n'était proposée —
« Voir dashboard Audit » restait une phrase à suivre à la main. Côté apps, le
bouton d'action (`action_url`/`action_label`, supporté par le modèle et le
schéma M2M de longue date) n'était visible qu'après ouverture du détail.

| Élément | Détail |
|---------|--------|
| **`hub/app/alert_checker.py`** | `ALERT_ACTIONS` : pic d'erreurs → onglet Erreurs d'Audit, échecs auth en rafale → filtre code 401. `deliver_alerts()` persiste `action_url`/`action_label` (modèle inchangé, champs simplement remplis). Sans action volontairement : Storm 401 SSE / charge SSE / erreurs écriture DB (compteurs mémoire ou 401 exclus d'`error_logs` par design anti log-storm — un deeplink donnerait une liste vide) et app offline (pas de cible Audit naturelle). M2M inchangé (les apps envoient déjà body + action) |
| **`frontend/public/js/modules/notifications.js`** | Lignes des deux modes (Ma boîte + Gestion, y compris sous-lignes ×N) : aperçu du body sur 1 ligne sous le titre + lien d'action inline. Deeplink `#audit?tab=…&f_…=…` → navigation in-app (showPage audit + `auditAlertDetails`, filtres pré-remplis — mécanisme existant simplement alimenté depuis les notifications) ; `http(s)` apps → nouvel onglet. Dans les deux cas : tracking clic existant + marquage lu automatique (« traiter = lire »), badge et lignes resynchronisés sans reload complet en Ma boîte (sync groupe ×N). Bouton du panneau de détail routé par le même handler (`notifOpenAction`) ; `notifClickAction` (mort) supprimé ; `action_url` ne transite plus par un attribut `href` |
| **`frontend/public/css/style.css`** | `.notif-snippet` (ellipsis CSS strict, aucune animation) + `.notif-open-link` (bleu apps / ambre Audit) |
| **Tests** | +8 (rouges constatés avant implémentation) : mapping d'actions par alerte + persistance `deliver_alerts`. alert_checker 20 vert, périmètre messages 93 vert (hôte) |

---

## [3.85.9] - 11 novembre 2026

### 🐛 Fix : console notifications lisible en mobile — largeur minimale de table + scroll horizontal

Sur mobile (~390 px), la colonne Titre de la console notifications s'écrasait en pile verticale
caractère par caractère (modes « Ma boîte » et « Gestion ») : les colonnes latérales à largeur
fixe (~34 rem au total — case 2,2 + App 11 + Reçu 7,5 + État 7 + Actions 6) laissaient le Titre
(`text-break`, largeur minimale ≈ 1 caractère) se comprimer à volonté. `.table-responsive` ne
déclenche son scroll horizontal que si la table ne peut pas rentrer — or elle « rentrait »
toujours, en écrasant le titre.

| Élément | Détail |
|---------|--------|
| **`frontend/public/css/style.css`** | `.notif-table { min-width: 46rem }` — largeur de lecture minimale (colonnes fixes ~34 rem + ~12 rem de titre) ; en dessous, le scroll horizontal natif Bootstrap prend le relais (seul scroll local autorisé par la charte HUBCSS). Desktop (> 46 rem de contenu) : aucun effet |
| **Vérification** | Rebuild `hub-frontend` : CSS servi contenant `min-width: 46rem` (curl), logs nginx propres, rendu contrôlé à largeur 390 px |

---

## [3.85.8] - 10 novembre 2026

### ⚡ Refactor : résolution structurelle du groupe dédupliqué — archivage/restauration/suppression admin propagent à toute la pile

Suite des remontées du 10/11 (mark-read 3.85.6, suppression destinataire) : dans la vue Gestion, archiver / restaurer / supprimer définitivement une ligne groupée ×N ne traitait que son occurrence représentante — le reste de la pile réapparaissait au refetch. Défect structurel : la clé de déduplication était copiée-collée dans chaque action, sans propagation admin.

| Élément | Détail |
|---------|--------|
| **`hub/app/routes/messages.py`** | SOURCE UNIQUE de la clé : `_group_partition()` (window functions de GET /me et de la vue Gestion refactorées dessus, plus aucune copie locale) + `_group_key_conditions()` ; résolveur générique `_resolve_group(db, message_id, user_id=None)` à deux périmètres (destinataire : visibles/actifs/non expirés ; admin : toutes occurrences de la clé). `mark_message_read` et `_hide_group_for_user` refactorés dessus. Actions admin propagées : `POST /{id}/archive`, `POST /{id}/restore`, `DELETE /{id}/hard` → `affected` = occurrences réellement traitées ; `DELETE /batch` étend les ids sélectionnés aux groupes complets (union sans doublon). La purge par filtres (`DELETE /`) reste hors périmètre (opère déjà sur des ensembles), `delivered`/`click` restent par occurrence (tracking technique) |
| **`frontend/public/js/modules/notifications.js`** | Gestion : toasts au compte réel (« N message(s) archivé(s) (groupe complet) »), confirms explicites sur la propagation groupe (unitaire ×N et actions en masse) |
| **Tests** | +4 : archive/restore/hard/batch propagent au groupe (message hors groupe épargné, `affected` exacts) — rouges prouvés avant implémentation. Domaine messages : 66 vert (hôte) ; suite docker 1564 vert — échecs préexistants documentés inchangés (`test_vpn_dns`/`test_workshop_overrides` : env hôte, non liés) |

---

## [3.85.7] - 10 novembre 2026

### ✨ Nav : retrait de l'onglet « Messages » — accès aux notifications par la cloche uniquement

La console notifications unifiée (« Ma boîte » / « Gestion ») est accessible
depuis la cloche 🔔 de la navbar (`showPage=inbox`, même section) : l'onglet
« Messages » était un doublon depuis la fusion messages/notifications
(choix utilisateur du 10/11/2026).

| Élément | Détail |
|---------|--------|
| **`frontend/public/index.html`** | Retrait du `<li>` de l'onglet « Messages » (id `navMessages` + badge `navMessagesBadge`) |
| **`frontend/public/js/modules/notifications.js`** | `updateMessageBellBadge` ne pilote plus que la cloche ; `updateNavMessagesBadge` devenue no-op documentée (9 appels conservés dans `notifications.js`/`sse.js` — le fetch stats mort est supprimé) ; `_applyNavMessagesBadgeCount` / `_badgeCountFromStats` supprimées |
| **`frontend/public/js/modules/auth-navigation.js`** | 2 lignes mortes retirées (`navMessages?.classList`) |
| **Conservé** | Préférence « badge navbar par priorité » (Préférences + GET/PUT `badge-priorities`) : ne pilote plus d'affichage, retrait à décider séparément |

---

## [3.85.6] - 10 novembre 2026

### ⚡ Fix : actions groupées sur les notifications dédupliquées — « Marquer lu » et « Supprimer » traitent toute la pile

Console « Mes notifications » (group_similar) : « Marquer lu » sur des lignes dédupliquées ×N laissait le badge **« Non lu »** (×22→×21, horodatage qui recule, unread_total -1 par ligne malgré le toast « N marquée(s) ») — et symétriquement, « Supprimer » ne masquait qu'une occurrence, la ligne réapparaissant au refetch avec un nouveau représentant. Cause : front et routes n'opéraient que sur le représentant (occurrence la plus récente du groupe), sans propagation — il fallait N clics pour vider une pile ×N.

| Élément | Détail |
|---------|--------|
| **`hub/app/routes/messages.py`** | `POST /{id}/read` propage la lecture à toutes les occurrences visibles du groupe (titre + priorité + coalesce(sender_app_slug, sender_type), cible user/broadcast, actifs non expirés — miroir du group_similar), receipts strictement per-user ; helper `_apply_read_receipts` extrait de `/read-all` (même garde d'unicité `(message_id, user_id)`, incident 09/11) ; statut `already_read` préservé ; `marked_read` renvoyé. `DELETE /me/{id}` + `/me/delete-batch` : `_hide_group_for_user` masque tout le groupe, `hidden` = occurrences réellement masquées |
| **`frontend/public/js/modules/notifications.js`** | toasts comptent les occurrences réelles (`marked_read` / `hidden`, fallback 1 par ligne pour compat ancien backend), mention « (groupe complet) » quand la propagation dépasse 1 ; badge cloche du slide-over décompte `marked_read` au lieu de 1 fixe |
| **Tests** | +5 : propagation mark-read (vue groupée lue, ×N intactes), propagation suppression single + batch, 2 garde-fous per-user (aucun receipt créé pour les autres users) — rouges prouvés via stash avant implémentation. Domaine messages 62 vert (hôte, main fusionné avec 3.85.5) ; suite docker 1555 vert — échecs préexistants documentés inchangés (`test_vpn_dns`/`test_workshop_overrides` : env hôte, non liés) |

---

## [3.85.5] - 10 novembre 2026

### ⚡ Fix : livraison auto du GET /me tolérante aux courses — fin des 500 UniqueViolation sur l'inbox

Incident 10/11 02:56 (user vpoulidor, messages 917+922) : l'auto-delivery de l'inbox insère les receipts manquants au commit du `GET /api/messages/me`. Si une requête concurrente (double onglet, refresh, POST /read simultané) crée le même receipt entre le SELECT de page et le commit, l'INSERT en masse lève `UniqueViolationError` sur `uq_message_read_receipts_message_user` → **500 sur un simple GET d'inbox** (`messages.py:914`). Le durcissement 3.83.3 couvrait read/delivered/click, pas ce chemin.

| Élément | Détail |
|---------|--------|
| **`hub/app/routes/messages.py`** | Le commit de la livraison auto absorbe `IntegrityError` (rollback + log info `📭`) : la livraison est best-effort, l'inbox répond 200 avec ses messages — le prochain GET refera proprement (le receipt concurrent porte déjà `delivered_at`) |
| **Tests** | `hub/tests/test_messages_me_delivery_race.py` +1 : commit #1 simulé en IntegrityError → **200** avec inbox complète, GET suivant nominal (rouge : 500). Domaine messages 74 vert ; suite hôte 1621 vert (préexistants + flaky xdist documentés inchangés) |

---

## [3.85.4] - 10 novembre 2026

### ⚡ Fix : auto-approbation atelier par fallback nom — fin des boucles legacy_reregistration

Incident 09-10/11/2026 (nocturne) : ouverture de l'atelier `exemple-app-ws`. L'auto-approbation workshop matchait l'app de base **uniquement par slug dérivé** (`<slug>-ws` → `<slug>` = `exemple-app`), mais l'app principale porte le slug `exemple-app` → aucun match → atelier resté `pending`/`inactive` à vie, SA créé désactivé → 403 au mint → martèlement register/token sans état entre cycles : **2019 réinscriptions legacy + 2523×403 + 257×429 en 2h35** (22:38 → 01:14, fin à la fermeture de l'atelier). Le plafond M2M par client_id (3.85.2) a contenu la casse (257 429s au lieu de ~2500, **zéro collatéral** — les autres apps ont continué à minter normalement toute la nuit).

| Élément | Détail |
|---------|--------|
| **`hub/app/routes/apps.py`** | Fallback dans les 2 sites d'auto-approbation de `register_app` (création + ré-enregistrement) : si le slug dérivé n'a pas de correspondance, chercher une app de base **approvée non-atelier au même nom** que l'atelier (les ateliers héritent du nom de l'app principale). Match ambigu (≥2 candidats) → pas d'auto-approbation (approbation manuelle). Logs `🔁` dédiés au fallback nom |
| **Tests** | `hub/tests/test_workshop_autoapprove.py` +4 (création par nom, garde « sans base → pending », ré-enregistrement par nom d'un atelier orphelin pending, invariant slug dérivé prioritaire) — domaine register/workshop 148 vert, suite hôte complète 1622 vert (échecs préexistants documentés inchangés) |
| **Filet socle** | Le martèlement applicatif est par ailleurs coupé à la source par HUBsocle 2.34.4 (cooldown M2M après 403 persistants) — défense en profondeur |
| **Conflit release** | Deux 3.85.3 sorties simultanées (celle-ci, préparée sur branche, et « rechargement auto après maj » poussée à 02:55) — entrée renumérotée 3.85.4, merge CHANGELOG résolu en gardant les deux contenus |

---

## [3.85.3] - 10 novembre 2026

### ✨ Rechargement automatique de la page admin après une mise à jour

Après le retour du Hub (mise à jour terminée), la page admin se recharge
automatiquement : token, données chargées et SSE peuvent être périmés après le
redémarrage — seule une page fraîche garantit un retour sain.

| Élément | Détail |
|---------|--------|
| **Succès** | verdict visible + compte à rebours 8 s (« La page sera rechargée automatiquement… ») + bouton « Recharger maintenant » ; `#admin` posé pour revenir directement sur la vue admin |
| **Échec** | verdict + journal + remède affichés, bouton « Recharger maintenant » seulement (l'admin lit le diagnostic avant de recharger) |
| **Tests** | `node --check` ; navigation réelle vérifiée sur le déploiement 3.85.3 (voir rapport de session) |

---


## [3.85.2] - 9 novembre 2026

### ⚡ Fix : rate limit M2M keyé par client_id pour IP interne — fin des 429 en cascade

Incident 09/11/2026 (84×429 dans error_logs sur `POST /api/auth/token`, visibles dans l'audit) : le durcissement M2M de 3.83.2 (audit 08/11) comptait **5 req/60 s par IP** sur le grant `client_credentials`, or toutes les apps du parc mintent derrière la **même IP gateway Docker (10.42.0.1)**. Dès que plus de 5 apps remintent leur token dans la même fenêtre (vagues synchronisées observées après reconnexion du beacon SSE — le TTL M2M 60 min fait reminter chaque heure), le 6ᵉ appel et suivants prennent un 429 ; les apps réessaient avec backoff et finissent par réussir — aucune app n'était cassée, mais l'audit se remplissait d'erreurs.

| Élément | Détail |
|---------|--------|
| **`hub/app/routes/auth.py`** | Nouveau `_check_m2m_rate_limit(request, client_id)` pour la branche `client_credentials` : IP interne de confiance (`is_docker_network_ip` / whitelist `APP_WHITELIST_NETWORKS`) → compteur **keyé par client_id**, limite `_M2M_RATE_LIMIT_MAX_INTERNAL=20`/60 s (borne anti-hammering conservée PAR service account) ; IP externe → compteur **keyé par IP**, limite 5/60 s inchangée vs 3.83.2. Dict dédié `_m2m_attempts` (plus de partage du dict login) |
| **Sécurité** | La borne visée par l'audit du 08/11 reste effective des deux côtés : guessing externe toujours 5/60 s/IP, hammering interne 20/60 s **par client_id** (vs non borné avant 3.83.2) ; bcrypt ~100 ms rend le guessing insensible (analyse 3.45.x inchangée) |
| **Tests** | `tests/conftest.py` : `reset_rate_limiter` vide aussi `_m2m_attempts` (interférence inter-fichiers attrapée par la boucle graduée) ; +2 tests dans `test_m2m_hardening.py` (3 apps derrière la même gateway : 3×10 mints sans 429 ; borne interne par client_id tient : 20×401 puis 429). Suite hôte complète : 1618 vert — seuls les échecs préexistants documentés (`test_app_deploy`×2, `test_vpn_dns`) restent, identiques sur main |

---

## [3.85.1] - 9 novembre 2026

### 🐛 Badge « divergé » affiché à tort sur le méta aligné

`system-update.js` testait `if (meta.drifted)` — or un tableau vide `[]` est truthy
en JavaScript : la carte « Parc » affichait « divergé » même sans aucun submodule
dérivé. Test resserré sur `meta.drifted && meta.drifted.length` (constaté sur le
déploiement réel 3.85.0, statut hôte derrière = 0 / dirty = false / drifted = []).

---


## [3.85.0] - 9 novembre 2026

### ✨ Centre de mises à jour : routes proxy + onglet « Mises à jour » (admin)

Le Hub expose le service hôte `hubctl update --serve` à ses administrateurs : détection des mises à jour du socle (diff git), déclenchement de `hubctl update hub` avec suivi live du log et reconnexion automatique pendant le redémarrage du Hub. Détail du service hôte : hubctl 0.18.0.

| Élément | Détail |
|---------|--------|
| **`hub/app/routes/system_update.py`** | router admin `/api/admin/system` : `GET /update-status` (passthrough du statut git par composant ; `?refresh=1` forwardé au service hôte ; service down ⇒ 200 `{"service":"unreachable"}`), `POST /update` (strict + audit `SYSTEM_UPDATE_TRIGGERED`), `GET /update-job` (tolérant) ; timeout GETs branché sur `settings.update_timeout` (30 s) ; token atelier réutilisé, 503 dédié (hint `journalctl -u hubctl-update`) |
| **Settings** | `update_service_url` (défaut `http://host.docker.internal:8778`) + `update_timeout` (30.0) |
| **Front — badge navbar** | point orange à côté de la version (admin only) quand `origin/main` est en avance ; clic → page admin onglet Mises à jour ; 403/unreachable silencieux |
| **Front — onglet Mises à jour** | statut par composant (version → version cible, badges à jour / X → Y / divergé / arbre sale / erreur + remèdes), bouton « Mettre à jour le Hub et le socle » (confirmation, anti double-clic, 409 → rattachement au job), panneau live : polling 2 s sans plafond, « Hub en redémarrage… » après 3 échecs consécutifs, verdict + tail du log, re-fetch `/api/health` au retour |
| **Sécurité** | `get_current_admin` sur les 3 routes (401/403 testés), échappement HTML sur toute valeur serveur, pas d'animations (polling sobre) |
| **Tests** | +9 `test_system_update_routes.py` (passthrough strict/tolérant, 401/403, audit en DB, forward `?refresh=1`, timeout sentinelle, 409 payload réel sans accent) — les échecs `test_app_deploy`/`test_vpn_dns`/`test_workshop_overrides` sont préexistants sur main (prouvés identiques sur l'image main) |

---


## [3.84.0] - 9 novembre 2026

### ✨ Console Notifications unifiée : une seule page adaptée au rôle (Ma boîte / Gestion)

Refonte ergonomique de la gestion des notifications envoyées par les apps (spec `docs/superpowers/specs/2026-11-09-notifications-console-design.md`, travail isolé branche `feat/notifications-console`) : la page admin « Messages » et l'inbox « Mes Notifications » fusionnent en **une page « Notifications »** — mode **Ma boîte** pour tous (avec actions de gestion pour les admins via le toggle **Gestion**).

| Élément | Détail |
|---------|--------|
| **Suppression réelle côté user** | fini le localStorage (`hub_inbox_hidden_ids`) : « supprimer de ma boîte » = `hidden_at` sur le read-receipt du destinataire (migration **065**), broadcast intact pour les autres. Endpoints `DELETE /api/messages/me/{id}`, `POST /api/messages/me/delete-batch` (≤ 500), `POST /api/messages/me/purge-read` (« Vider les lus ») |
| **`GET /api/messages/me` enrichi** | réponse **toujours paginée** `{messages, total, page, page_size, pages, unread_total}` (fin de la limite en dur 200 — ⚠️ breaking pour tout consommateur externe de `/me`, le front du Hub est migré dans la même release), filtres `unread/priority/sender_app/search`, **`?group_similar=true`** : regroupement SQL (window functions) des alertes identiques par (titre, priorité, source) → carte ×N dépliable, comptes justes même paginés |
| **Vue Gestion (admins)** | absorbe l'ancienne page admin : filtres statut/priorité/source, taux de lecture par message, archivage/suppression en masse, export CSV, Composer (extrait dans `messages-compose.js`), préférences badge |
| **Modal « Purger… » (admins)** | expose la purge existante avec comptage avant action (`GET /api/messages/purge-preview`, mêmes gardes que la purge : urgents jamais lus exclus des comptes) : lus / expirés / plus vieux que X jours (`older_than_days` — nouveau param, l'ancien `hours` est une fenêtre récente) / par app ; scope actifs uniquement (`status=active`, épargne les archivés même en hard) ; garde-fou urgent non lu sur toutes les options ; nouveaux params `read_only`, `expired_only`, `exclude_urgent_unread`, `older_than_days`, `status` sur `DELETE /api/messages/` |
| **Actions rapides** | ✓ marquer lu, 🗑 supprimer au fil des lignes, sélection multiple + barre d'actions, dans les deux modes |
| **Front** | `notifications.js` (console unifiée) + `messages-compose.js` remplacent `messages-inbox.js` (supprimé) ; SSE inchangé, badges cloche + nav pour tous ; navigation « Messages » désormais visible de tous les utilisateurs ; `escapeAttr` (utils.js) pour tous les contextes d'attribut (usernames libres → XSS par attribut) |
| **Tests** | +30 tests (pagination/filtres/grouping/hidden, suppression user, purge-preview/purge étendue) — suite hôte 1601 vert, conteneur 1534 vert ; échecs préexistants hors périmètre documentés (app_deploy×2, vpn_dns : présents sur la base ; workshop_overrides : environnement conteneur) |
| **Non modifié** | contrat M2M `/api/messages/m2m/*`, `alert_checker.py` (les alertes restent des messages distincts — regroupement visuel uniquement), rétention 90 j |

---


## [3.83.3] - 9 novembre 2026

### ⚡ Correctifs pic d'erreurs messages : receipts dupliqués + ids SSE invalides

Incident 09/11/2026 (pic d'erreurs Hub : 42 erreurs/10 min sur `POST /api/messages/{id}/read`) — deux causes racines distinctes :

| Élément | Détail |
|---------|--------|
| **Payload alertes amputé** | `deliver_alerts()` émettait le SSE `user_message` **sans `id`** → le front (`messages-inbox.js`) retombait sur `Date.now()` comme identifiant et rappelait POST /read avec un timestamp à 13 chiffres → overflow int32 asyncpg (« invalid input for query argument »), 500 en boucle. Le payload transporte désormais l'id du `UserMessage` persisté (même contrat que `_message_to_brief()`) |
| **Unicité effective des receipts** | L'UNIQUE(message_id, user_id) de la migration 014 était un **no-op** sur toutes les installations : `create_all` construit la table depuis un modèle sans contrainte AVANT `apply_migrations()`, donc son `CREATE TABLE IF NOT EXISTS` ne s'applique jamais. Des courses (read + read-all concurrents) ont créé de vrais doublons → « Multiple rows were found when one or none was required » en boucle. Modèle : index unique nommé `uq_message_read_receipts_message_user` ; migration **064** : dédoublonnage (ROW_NUMBER, pattern 055) + `CREATE UNIQUE INDEX IF NOT EXISTS` |
| **Routes tolérantes** | read/delivered/click : `scalars().first()` au lieu de `scalar_one_or_none()` — tolère des doublons pré-migration au lieu de 500 |
| **Garde front** | `messages-inbox.js` : seul un id entier plausible (> 0, ≤ int32) autorise `markMessageRead` ; sinon id d'affichage anonyme, la carte se ferme sans POST |
| **Tests** | 2 nouveaux (id dans le payload SSE, rejet du doublon par la contrainte) — suite hôte 1583 vert ; 3 échecs préexistants inchangés (`test_app_deploy`×2, `test_vpn_dns`, présents sur la base 3.83.2) |
| **Déployé et vérifié en prod dev** | migration 064 appliquée au boot, index présent en DB ; trafic réel : 43 erreurs /read à 02:08 → 0 après déploiement (2-3 résidus à la minute du redémarrage) |

---

## [3.83.2] - 8 novembre 2026

### ⚡ Post-audit SSE : logs, visibilité, alertes admin, hygiène DB, durcissement M2M

Suite de l'audit 08/11/2026 (anti-churn 3.83.1) — 5 correctifs sur les risques logging/auth/alerting identifiés :

| Élément | Détail |
|---------|--------|
| **Scrub logs** | `_ScrubSecretsFilter` : valeurs `token=`/`beacon_token=` masquées (`***`) dans les access logs uvicorn — un JWT admin valide 8 h était loggé en clair à chaque reconnexion query_token. Rotation json-file 50 Mo × 3 sur hub-api et hub-frontend (croissance illimitée auparavant) |
| **Visibilité 401** | Compteur mémoire des 401 SSE (exclus logs + error_logs pour cause de bruit legacy) exposé dans les stats (`sse_auth_failures_1h`) — un storm d'échecs d'auth n'est plus invisible |
| **Durcissement M2M** | Rate limit sur `client_credentials` (5/60 s/IP — seule branche sans limite, endpoint exposé 80/443/8000) + TTL court `m2m_access_token_expire_minutes` (60 min, était 8 h) |
| **Hygiène DB** | Purge `user_sessions` (rétention `session_retention_days`=180 j, seule table sans rétention : 37,6 k lignes) branchée sur la purge 24 h + index `login_at` (migration **063**) |
| **Alertes admin** | Vérificateur périodique (`ALERT_CHECK_INTERVAL_S`=60 s) : pic d'erreurs (>30/10 min), échecs 401/403 API (>20/10 min), storm 401 SSE (>100/h), deadlocks DB (>10/h), capacité SSE (>500 clients), app offline (>5 min) → `UserMessage` persistée aux admins actifs + SSE temps réel, cooldown 30 min par clé. Tout configurable via Settings `alert_*`, désactivable (`ALERT_CHECK_INTERVAL_S=0`) |
| **Tests** | 33 nouveaux tests (scrub 5, visibilité 4, M2M 2, purge 4, alertes 11 + scope SSE) — suite hôte 1580 vert, échecs préexistants inchangés (`test_app_deploy`×2, `test_vpn_dns`) |
| **Déployé et vérifié en prod dev** | boot : purge sessions 180 j + alertes 60 s activées, migration 063 appliquée, index créé, 0 traceback |

---

## [3.83.1] - 8 novembre 2026

### ⚡ SSE : présence en mémoire anti-churn + instrumentation du flux

Audit SSE du 08/11/2026 : le navigateur recrée ses flux SSE toutes les ~60 s (churn erratique, 7-55 s de vie mesuré), et chaque cycle coûtait INSERT session + end_session + heartbeat DB par client (~5,3 tx/s à 80 users) avec deadlocks sweeper×fermeture sur `user_sessions`/`app_usage_segments` (37,5 k lignes accumulées, 1 583 fermetures/24 h pour 1 utilisateur).

| Élément | Détail |
|---------|--------|
| **Instrumentation** | `event_generator` journalise la cause de sortie (`client_disconnect`/`server_shutdown`/`event_shutdown`/`cancelled`/`error`) + durée de vie ; exceptions passent de silencieuses à `logger.exception`. Log de connexion `🔌` avec user_id, app_slug, mode d'auth (bearer/query_token/beacon), IP source (X-Forwarded-For) et clients actifs |
| **Heartbeat en mémoire** | `_emit_heartbeat` ne touche plus la DB : dicts `_last_seen`/`_last_app_slug`. Flush batché par le sweeper (`_flush_presence_to_db`, 1 transaction / 120 s pour tous les utilisateurs connectés) — ~5,3 tx/s → ~0 |
| **Session réutilisée** | Grâce de déconnexion 5 s → **300 s** (`DISCONNECT_GRACE_S`) : un reconnect dans la fenêtre annule le `end_session` planifié ; garantie session au connect uniquement hors fenêtre de churn (`RECONNECT_REUSE_WINDOW_S`) ou si l'app courante change — via `ensure_session_presence` |
| **Ordre de démarrage** | `start_cleanup_task` (health checker) déplacé après la garantie session : en test (StaticPool SQLite partagé), un rollback de pool concurrent pouvait annuler une transaction en vol |
| **Tests** | `test_sse_presence_memory.py` (4 cas anti-churn), instrumentation (3 cas), heartbeat réécrit pour le contrat mémoire, `test_sse_app_param` déterminisé — portée SSE 20/20, suite hôte 1552 vert (échecs préexistants `test_app_deploy`×2, `test_vpn_dns` + flaky parallèle) |
| **Validé en prod dev** | post-déploiement : 1 session ouverte à travers tous les cycles de churn, 0 fermeture `sse_disconnect`, 0 deadlock sur fenêtre continue |

---

## [3.83.0] - 8 novembre 2026

### ⚡ Ateliers : overrides compose par app (`.workshop.overrides.env`) — fix durable Task 0

Bug Task 0 (exemple-app) : le contrat `.workshop.env` (7 clés + `HTTPS_ENABLED` à la création) ne couvre pas les ports host métier d'une app — l'atelier retombait sur les ports prod du `.env` (ex. `PHILIA_NG_PORT=42690` déjà bindé) → conflit de bind → restart loop à chaque recréation.

| Élément | Détail |
|---------|--------|
| **`WS_OVERRIDE_RESERVED` + `_ws_overrides_path()` + `_ws_override_env()`** | nouveau fichier optionnel `APPS/<slug>/.workshop.overrides.env` (dotenv, committé par l'app), fusionné dans l'env subprocess de `docker compose -p X-ws` ; précédence `_compose_base_env()` < overrides < `.workshop.env` (les clés de l'allocateur gagnent toujours) |
| **`_ws_compose_env()`** | point d'intégration unique — couvre create (build/up/rollback), maj (rebuild), ship (down -v) et remove ; les chemins prod (recreate, deploy_service) restent en `_compose_base_env()` seul, volontairement inchangés |
| **Robustesse** | fichier absent → no-op strict (comportement identique) ; illisible ou non-UTF-8 → warning + ignore, jamais de crash d'opération ; clés réservées présentes dans les overrides → ignorées avec warning (possédées par l'allocateur) |
| **Contrat inchangé** | le `.workshop.env` généré reste strictement aux clés du contrat — contract tests 3.82.0 verts sans modification d'assertion ; `hubctl` (0.17.0) implémente la parité côté CLI |
| **hub/tests** | nouveau `test_workshop_overrides.py` (8 tests : no-op, merge, précédence, clés réservées + warning, create path, illisible, non-UTF-8) — périmètre atelier/workshop : 139 verts |

> Premier utilisateur : exemple-app 3.5.2 (`.workshop.overrides.env` committé, `PHILIA_NG_PORT=5290` / `PHILIA_NG_HTTPS_PORT=5291`).

---

## [3.82.0] - 7 novembre 2026

### ⚡ Ateliers : fin des compteurs incohérents — unlink protégé, réconciliation fantômes/orphelins (diagnostic 2026-11-07)

Cause racine du diagnostic (« Ateliers: 1, badges ATELIER: 5, gestion des applications: 0 ») : trois sources de vérité divergentes — le disque (marqueurs `.workshop.env`), la DB (lignes `is_workshop` des auto-enregistrements `{slug}-ws` jamais purgées après suppression hors UI), et le filtre admin par défaut (`workshop_filter=false` sans re-fetch).

| Élément | Détail |
|---------|--------|
| **hub/app/workshop_service.py — `_op_remove` / `_op_ship`** | unlink `.workshop.env` protégé (try/except OSError) : un filesystem read-only (Errno 30 réel du 06/11) ne court-circuite plus la purge DB de la fiche `{slug}-ws` (cause des badges ATELIER fantômes). down -v OK + purge DB OK = opération en **succès**, warning journalisé dans les steps (« fs possiblement read-only ») |
| **hub/app/routes/atelier.py — `_scan_ghosts()` / `_scan_orphans()`** | `GET /api/admin/atelier` expose désormais `ghosts` (fiches DB `is_workshop=True` sans marqueur disque, tolérant DB down → `[]`) et `orphans` (projets compose `-ws` actifs sans marqueur, via labels Docker, tolérant Docker down → `[]`) — clé `ateliers` inchangée (rétro-compat) |
| **hub/app/routes/atelier.py — `POST /api/admin/atelier/reconcile`** | nouvelle route admin : purge des fiches fantômes via `_delete_workshop_app_row` (cascade SA M2M + ports + credentials), garde `is_workshop=True`, skip si marqueur disque présent, body `{"slugs": [...]}` optionnel (défaut : tous les ghosts), audit best-effort `ATELIER_RECONCILE` (pattern close-all) |
| **frontend/public/js/modules/admin-atelier.js** | onglet Ateliers : section « Fiches fantômes » (cases pré-cochées + purge confirmée → POST reconcile → refresh) et section « Projets Docker orphelins » (informative), rendues seulement si non vides |
| **frontend/public/js/modules/admin-apps.js** | le sélecteur Mode (Production/Atelier/Tout) déclenche un **re-fetch serveur** au changement (fini le refiltrage client d'une liste fetchée en `false` qui affichait toujours 0 atelier) ; idem pour « Réinitialiser » et `focusPendingApps()` |
| **hub/tests** | +13 tests : succès `_op_remove`/`_op_ship` malgré OSError(30) avec purge DB vérifiée, détection ghosts/orphans (exclusions prod, DB/Docker down), endpoint reconcile (auth, purge, skip, garde is_workshop, erreurs sans 500). Périmètre atelier/workshop : 132 verts |

> Nettoyage d'état associé (op du 07/11, hors repo) : 10 lignes `-ws` fantômes purgées via la cascade officielle, marqueur zombie `exemple-app` supprimé, atelier sauvage worktree `exemple-app-ws` détruit (down -v + purge), volumes orphelins (`exemple-app-ws-postgres-data`, `exemple-app-ws_*`) supprimés.

---

## [3.81.0] - 6 novembre 2026

### ⚡ Rôles : viewer par défaut pour l'admin Hub sans rôle + héritage atelier (spec 2026-11-06)

| Élément | Détail |
|---------|--------|
| **hub/app/models.py — `get_apps_with_roles_and_workshops()`** | source unique de résolution du claim JWT `apps` : rôle explicite > héritage atelier > défaut `viewer` pour l'admin Hub sur toute app non-système. L'atelier `{X}-ws` d'un admin hérite du rôle résolu de sa base `X` (viewer au minimum), sans barrière d'accès atelier. Non-admin : héritage uniquement avec activation `user_workshop_access` (défaut `user` conservé sans rôle sur la base) |
| **hub/app/models.py — `workshop_role_for_app()`** | héritage du rôle explicite de l'app de base pour les non-admins (au lieu du `user` systématique), même défaut et mêmes conditions d'accès |
| **hub/app/routes/auth.py — `/launch` et `/sso`** | suppression des fallbacks locaux `apps[slug] = "admin"` : un admin Hub sans rôle explicite reçoit désormais `viewer` (fini le rôle admin implicite divergent qui disparaissait à la première rotation de refresh token) |
| **hub/app/routes/apps.py — `list_apps`** | `my_role` de l'admin affiche le rôle résolu (explicite > hérité > viewer) — dashboard cohérent avec l'accès réel ; l'héritage atelier est aussi affiché pour les non-admins (explicit ws > base > `user`) |
| **JWT** | claim `apps` de l'admin désormais complet (viewer implicite sur tout le parc non-système) — tokens légèrement plus volumineux, comportement uniforme sur login / `/login` / grant refresh_token / `/token/refresh` / `/launch` / `/sso` |
| **Tests** | nouveau `hub/tests/test_role_default_admin.py` (15 tests, cycle rouge→vert : fallback viewer, héritage admin/non-admin, gardes d'accès, /launch, /sso, list_apps) ; `test_app_launch_admin_without_explicit_role_gets_admin` → `..._gets_viewer` |
| **Hors périmètre** | `GET /api/auth/me` inchangé (liste les rôles explicites ; frontend Hub hors dépôt) — l'autorité d'accès reste le claim JWT |

---

## [3.80.5] - 5 novembre 2026

### ⚡ Sécurité : hook pre-commit gitleaks chaînable

| Élément | Détail |
|---------|--------|
| **scripts/hooks/pre-commit-gitleaks.sh** | scan gitleaks des fichiers stagés — bloque le commit si secret détecté (`gitleaks:allow` pour exemption justifiée) |
| **scripts/hooks/install-hooks.sh** | installe le hook dans `.git/hooks/pre-commit` en chaînant tout pre-commit existant (ne casse pas les hooks en place) |
| **CONTRIBUTING.md** | section « Hooks » : activation par `./scripts/hooks/install-hooks.sh` |
| **Contexte** | préparation open source — prévention de la réintroduction de secrets (cf. purge 0.80.x) |

---

## [3.80.4] - 4 novembre 2026

### ⚡ Scrub open source (IPs, slugs d'apps, docs internes) + community files

| Élément | Détail |
|---------|--------|
| **INTEGRATION.md** | IPs réelles → RFC 5737 ; slugs d'exemple `salarie`/`exemple-app`/`referentiels` → `mon-api`/`mon-app`/`mon-referentiel` (38 remplacements) |
| **docs/ROUTAGE-DNS.md** | réécrit avec des exemples génériques (`hub.exemple.priv`, `mon-app`, `192.0.2.10`, « organisation ») — la doc technique dnsmasq/SNI reste complète |
| **docs/GUIDE_INSTALLATION.md** | IP exemple scrubée ; section Mise à jour alignée sur la routine officielle (`docs/ops/mise-a-jour.md` du méta-repo) au lieu de `git pull`/`compose` en sous-module |
| **.env.example / hub-profile.example.json** | IP serveur → 192.0.2.x |
| **README** | badge + texte version à jour (3.80.4) |
| **Community files** | `SECURITY.md` + `CONTRIBUTING.md` ajoutés (renvois méta-repo) |
| **.gitignore** | fichiers internes conservés localement ignorés (`/project.md`, `/SECURITY_AUDIT.md`, `/docs/superpowers/`…) |
| **CHANGELOG** | référence à un rapport interne purgé neutralisée |

---

## [3.80.3] - 4 novembre 2026

### ⚡ Scrub secrets avant open source (historique réécrit)

| Élément | Détail |
|---------|--------|
| **INTEGRATION.md** | couple `CLIENT_ID`/`CLIENT_SECRET` d'un service account d'exemple remplacé par les placeholders `<VOTRE_CLIENT_ID>` / `<VOTRE_CLIENT_SECRET>` (compte déjà supprimé du registre Hub) |
| **hub/tests/test_vpn.py** | fixtures `_VALID_PRIVKEY`/`_VALID_PUBKEY` (paire WireGuard d'exemple) remplacées par une paire dummy générée |
| **hub/tests/test_security_config.py** | fixture `VALID_FERNET` remplacée par une clé Fernet dummy générée |
| **hub_backup.json** | export d'environnement (2025-12-31) retiré de tout l'historique via `git filter-repo` |
| **Objectif** | préparation open source : aucune clé exploitable dans l'arbre ni l'historique |

---

## [3.80.2] - 4 novembre 2026

### ⚡ fix(routing) — hub.hosts toujours écrit (vide si pas de zone)

- `routing_service.publish()` : `hub.hosts` est désormais **toujours** écrit dans le dossier DNS généré, même sans `hub_dns_zone` configuré (contenu vide).
- Cause : `dns/dnsmasq.conf` référence `addn-hosts=/etc/dnsmasq.d-generated/hub.hosts` inconditionnellement ; avec la zone vide, `publish()` purgait le dossier sans réécrire le fichier → erreur `failed to load names from …/hub.hosts` à chaque (re)start/HUP du résolveur `hub-dns`.
- Test : `test_publish_with_empty_zone_keeps_empty_hub_hosts` (remplace `…_wipes_files`) — le dossier généré contient exactement `hub.hosts` (vide), aucun vhost.

---

## [3.80.1] - 4 novembre 2026

### ⚡ Licence open source AGPLv3 + en-têtes SPDX

Le cœur du HUB passe en licence ouverte (préparation de la publication publique) :

| Élément | Détail |
|---------|--------|
| **Licence** | `LICENSE.md` (propriétaire « usage interne ») remplacé par `LICENSE` — **GNU AGPLv3** ; le cœur (API, front, orchestration) devient distribuable sous licence libre |
| **En-têtes SPDX** | `SPDX-License-Identifier: AGPL-3.0-or-later` + copyright POULIDOR Audit & Développement (Valérian Poulidor) ajoutés aux **345 fichiers sources** (backend Python, frontend TS/JS/CSS, scripts) — shebangs et déclarations d'encodage préservés, insertion au niveau octets |
| **README** | Mention « Projet interne » remplacée par la référence AGPLv3 ; la marque et les logos restent réservés (politique de marque du méta-repo POULIDOR) |

---

## [3.80.0] - 4 novembre 2026

### 🆕 Reset self-service par code admin (chantier D)

Un administrateur génère un code court `XXXX-XXXX` (affiché une seule fois, remis
hors-bande) depuis la page utilisateurs ; l'utilisateur — 2FA ou non — le saisit dans
la modale « Mot de passe oublié » (nouvel onglet « Code de reset ») et redéfinit son
mot de passe lui-même. L'admin ne connaît jamais le mot de passe choisi.

| Fonctionnalité | Description |
|----------------|-------------|
| **Codes de reset admin** | Table `password_reset_codes` (code hashé bcrypt, usage unique, brûlé dès la vérification, TTL 15 min configurable `password_reset_code_ttl_minutes`, un seul code actif par utilisateur) ; endpoint `POST /api/users/{id}/password-reset-code` (admin) + bouton/modal dans la page utilisateurs |
| **Onglet « Code de reset »** | La modale « Mot de passe oublié » gagne 2 onglets (Code TOTP / Code de reset) ; le flux TOTP existant est inchangé ; la vérification délivre le JWT `pwd_reset` usage-unique existant (étape 2 inchangée) |
| **Hygiène des sessions** | Nouveau helper `invalidate_user_sessions()` : révocation de tous les refresh tokens + SSE `force_logout` câblé sur les **4 chemins** de changement de mot de passe (change-password volontaire, recover TOTP, reset par code, reset admin `PATCH /users` avec champ `password`) — ⚠️ changement de comportement visible : l'utilisateur est déconnecté partout après un changement de mot de passe (standard OWASP) |
| **Sécurité** | Erreurs 401 génériques (anti-énumération), vérification en temps constant (hash fantôme), rate limiting dédié 5 tent./60 s sur la vérification (indépendant du login), alphabet sans 0/O/1/I (~40 bits), audit `password_reset_code_generated` / `password_reset_code_verified` |

**Hors périmètre (suivis séparés)** : outil hub-mcp `hub_user_reset_password` (après mise
en prod de la route), email/SMS, codes de secours imprimables, WebAuthn.

---

## [3.79.3] - 3 novembre 2026

### 🆕 Nouvelle version

> ⚠️ Cette entrée a été créée automatiquement par `sync_version.py`.
> Veuillez compléter les détails des modifications.

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.79.3] - 3 novembre 2026

### 🔧 Fix front — handlers de la vue routage (data-args)

| Élément | Détail |
|---|---|
| Cause | le dispatcher app.js passe les args parsés de `data-args` (jamais l'élément) — les handlers de la vue routage recevaient `undefined` au clic (TypeError, sélection d'app et actions inertes) |
| Fix | `data-args="$el"` sur les 11 boutons générés (sélection d'app, copier/ouvrir URL, renommage, services tiers, libération de ports, sous-onglets) — pattern hub |
| Cache | bump des `?v=` (reload obligatoire Ctrl+Shift+R) |

---

## [3.79.2] - 3 novembre 2026

### 🆕 Nouvelle version

> ⚠️ Cette entrée a été créée automatiquement par `sync_version.py`.
> Veuillez compléter les détails des modifications.

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.79.2] - 3 novembre 2026

### 🔧 Hotfix — retry des reloads au boot (routage)

Au boot, hub-api pouvait finir son lifespan avant que hub-frontend/hub-dns
soient running → exec 409 « is not running » → erreur persistante dans la vue
Accès & routage alors que tout tournait. Les reloads en attente sont désormais
retentés (4 × 5 s) ; une commande qui répond en échec reste fatale (rollback).
Tests unitaires du retry.

---

## [3.79.1] - 3 novembre 2026

### 🆕 Nouvelle version

> ⚠️ Cette entrée a été créée automatiquement par `sync_version.py`.
> Veuillez compléter les détails des modifications.

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.79.1] - 3 novembre 2026

### 🔧 Fix pass — review indépendant du routage (post 3.79.0)

| Élément | Détail |
|---|---|
| Front | refs DOM mortes après retrait des vues ports/réseau (création d'app et init admin refonctionnent) ; échappement guillemets ; plages réelles depuis /ports/status |
| API création | slug en collision avec un routage existant → 400 clair (validation cross-table, review I3) |
| dnsmasq | `local-service` (pas de résolveur ouvert vers Internet, review I4) + `bind-dynamic` (hot-plug wg0, review I5) ; zone en format hosts via addn-hosts (conf-dir alpine inopérant) |
| Publication | résolution host→IP pour address= (host.docker.internal, noms LAN — cas exemple-app-ws) ; app système « hub » jamais routée |
| Durabilité | dossiers dns-generated/gateway trackés (.gitkeep, contenus gitignorés — install fresh sans dossiers root, review I6) |
| Event loop | opérations bloquantes (gethostbyname, SDK docker, RSA, UDP) en asyncio.to_thread (review I8) |
| Hooks | unset_hub_host → republication (review M5) |

Live après fix : résolutions 42 noms (locales + host.docker.internal), SNI 200,
création en collision → 400.

---

## [3.79.0] - 3 novembre 2026

### ⚡ Routage par noms DNS — vue « Accès & routage » (chantier B)

| Élément | Détail |
|---|---|
| Vue admin « Accès & routage » | Remplace l'onglet Réseau et la modal Pool de ports : bandeau zone/résolveur/wildcard, onglets Applications (liste + panneau détail) / Services tiers / Ports (lecture seule + libération orphelins) ; publication automatique — le registre des apps est la source de vérité |
| Résolveur `hub-dns` | dnsmasq 2.90 (build local alpine, host network, 64m, cap_drop ALL + NET_BIND_SERVICE) ; autoritaire sur `<hub_dns_zone>` ; forward le reste au résolveur hôte ; port 53 jamais publié |
| Routage SNI | vhosts générés par `routing_service.publish()` dans `gateway/`, consommés par hub-frontend (`/etc/nginx/vhosts.d/`) ; proxy_pass = `frontend_url` du registre (scheme conservé) |
| WireGuard | `DNS = <hub_host>` poussé dans les configs clients quand la zone est configurée (config download + QR) |
| Création d'app | sans saisie de ports — allocation automatique idempotente ; `dns_name = slug` à la création (réponse enrichie) |
| Modèle | `Application.dns_name` + table `routed_service` (services tiers visibles — PhiliaNG migrée) + migration 061 |
| PKI | wildcard `*.<hub_dns_zone>` émis par pki.py (réémission au changement de zone) |
| Garde-fous | `test_compose_socket_proxy.py` (chantier A) + `test_compose_routing.py` + `test_frontend_js_syntax` (collision let/function attrapée) |
| Publication | `_apply_dir` in-place (bind mounts EBUSY au rename) + rollback contenu + `routing_last_error` dans le bandeau |
| Live | zone `hub.example.lan` configurée : résolution 42 noms, SNI exemple-app cert wildcard Verification OK → app 200, renommage live régénéré, WG DNS ✓ ; suite hôte 1485 passed / 3 failed PRÉ-EXISTANTS au HEAD (oidc×2 + slo, prouvés par run baseline) |
| Vues supprimées | onglet Réseau (propagation .env — obsolète), modal Pool de ports, section ports du formulaire |

---

---

## [3.78.0] - 2 novembre 2026

### ⚡ Durcissement : accès Docker via proxy filtré — plus aucun socket direct sur hub-api

| Élément | Détail |
|---|---|
| Nouveau service | `docker-socket-proxy` (tecnativa `v0.5.0`) — **seul** conteneur montant `/var/run/docker.sock` |
| hub-api | `DOCKER_HOST=tcp://docker-socket-proxy:2375` ; montage socket supprimé (base + overlay dev) ; `group_add` GID docker retiré (`entrypoint.sh` saute déjà le setup GID sans socket) |
| Whitelist | Audit des appels réels (SDK python + CLI compose) → `CONTAINERS`, `ALLOW_START/STOP/RESTARTS`, `EXEC`, `NETWORKS`, `BUILD+SESSION+GRPC` (buildx), `IMAGES`, `VOLUMES`, `INFO`, `SYSTEM`, `VERSION`, `PING`, `EVENTS`, `POST` |
| Familles fermées (403) | `secrets`, `swarm`, `services`, `tasks`, `nodes`, `plugins`, `commit`, `configs`, `distribution`, `auth` |
| Garde-fou | `hub/tests/test_compose_socket_proxy.py` — socket uniquement dans le proxy (base + dev), `DOCKER_HOST` requis, pas de GID docker sur hub-api, familles dangereuses fermées (5 tests, sans PyYAML) |
| Doc | `docs/DOCKER_SOCKET_PROXY.md` — whitelist preuve par preuve (fichier:ligne), procédure d'ajout d'une famille, rollback |
| `.env.example` | `HOST_DOCKER_GID` documenté obsolète (sans effet depuis cette version) |
| Vérification live | Matrice 403/200 complète depuis hub-api, scan 107 conteneurs, logs/stats/restart, `exec_run wg show` (VPN), build buildx + `up` + `down -v` via le proxy, suite auth 33/33 in-container |
| Limite connue | hub-api reste l'exécuteur des déploiements (rôle orchestrateur) avec une whitelist large ; scission exécuteur dédié = option « 1b » non engagée |

---

## [3.77.6] - 1er novembre 2026

### ⚡ Sauvegardes : les ateliers {slug}-ws ne sont plus sauvegardés

| Élément | Détail |
|---|---|
| Vue admin Sauvegardes | GET /api/admin/backups : les ateliers (`{slug}-ws`) n'apparaissent plus — ni la liste, ni les compteurs (Applications, Sauvegardes, Stockage total) |
| Stats | GET /api/admin/backups/stats : compteurs hors backups d'ateliers |
| Gardes endpoints | POST /api/apps/{slug}/backup/trigger et POST /api/apps/{slug}/backup (push M2M) → 400 « Les ateliers (...) ne sont pas sauvegardés » pour un slug `-ws` |
| Choke-point | `perform_app_backup` lève `BackupExecutionError` sur slug `-ws` : aucun chemin (scheduler, snapshot, trigger) ne peut créer un backup d'atelier |
| Scheduler + Snapshots | Filtre SQL `~slug.endswith("-ws")` dans `periodic_backup_scheduler` (main.py) et `run_app_backups` (snapshot_collectors.py) |
| Tests | hub/tests/test_backups_workshop_exclusion.py (6 tests TDD rouge→vert) — suite verte : 1463 (hôte) / 1419 + 1 skip (Docker, Postgres) |
| Convention | `{slug}-ws` réservée aux ateliers (hubctl + workshop_service) — même convention que l'exclusion audit usage |

---


## [3.77.5] - 31 octobre 2026

### 🔐 Fix — redirection HTTP → HTTPS sur le frontend du Hub

- **Corrigé** : avec `HUB_HTTPS_ENABLED=true`, le bloc `:80` de
  `nginx-ssl.conf` servait encore le front et le proxy `/api/` en clair
  (dual-stack hérité) — `curl http://<hub>/` répondait `200` au lieu de
  renvoyer vers HTTPS. Le bloc 80 devient un simple
  `return 301 https://$host:${HTTPS_PORT}$request_uri` : tout le service
  (front, `/api/`, SSE, uploads 1G) existe déjà sur le 443.
- `${HTTPS_PORT}` est substitué par `docker-entrypoint.sh` avec
  `HUB_HTTPS_PORT` (port **publié**, cf. compose) — même mécanisme que le
  socle ; nginx interne écoute toujours 443.
- `docker-compose.yml` : `HUB_HTTPS_PORT` désormais passé au conteneur
  `hub-frontend` (l'entrypoint en a besoin pour construire l'URL).
- **Non-cassage vérifié** : les appels machine passent par l'API `:8000`
  (réseau interne) ou par `:443` (`ALLOWED_ORIGINS` déjà https-only) ;
  le vhost PhiliaNG (`service.example.org`, `listen 80` sans `default_server`)
  garde son comportement propre.
- Syntaxe validée par `nginx -t` dans un conteneur jetable sur le réseau
  compose (upstream `hub-api` résolu).

---

## [3.77.4] - 29 octobre 2026

### 🐛 Fix — `make test-docker` : INTERNALERROR pytest avec pytest 9.2

- `pytest.ini` : `asyncio_default_fixture_loop_scope = function` — le rebuild
  de l'image (pour `respx`) a remonté pytest 9.1.1 → 9.2.0 et pytest-asyncio
  1.4.0 → 1.4.1 (déps non épinglées) ; l'avertissement « fixture loop scope
  unset » devient une `PytestDeprecationWarning` émise au configure, fatale
  via `filterwarnings = error::DeprecationWarning` → INTERNALERROR avant tout
  test. Clé recommandée posée explicitement (défaut futur de pytest-asyncio).

---

## [3.77.3] - 29 octobre 2026

### 🐛 Fix — gate `make test-docker` vert

- **`test_snapshot_streaming`** : gabarit mémoire 256 Mo / limite 160 Mo
  (au lieu de 512/320) — sous `make test-docker` (xdist `-n 4`, container de
  test plafonné à 2 Go), les enfants de test s'ajoutaient aux 4 workers pytest
  → pression memcg → enfant tué → faux échec. Le nouveau gabarit démontre la
  même propriété (chargement whole-file > limite → MemoryError ; streaming →
  passe) avec ~3× moins de pression. Hôte inchangé : 3/3 vert.
- **`respx>=0.22` ajouté à `hub/requirements.txt`** : importé par
  `test_atelier_routes` et `test_vault_routes` (présent sur l'hôte de dev,
  absent de l'image → 2 erreurs d'import à chaque `make test-docker`,
  préexistantes au 3.77.2).

---

## [3.77.2] - 29 octobre 2026

### 📣 Pour les utilisateurs
- **Corrigé** : la création d'un snapshot complet du Hub échouait (« Snapshot
  interrompu ») dès que le parc dépasse ~2 Go de données — le Hub manquait de
  mémoire pendant la compression et redémarrait. Les snapshots fonctionnent à
  nouveau quelle que soit la taille du parc, sans ralentir le Hub pendant
  l'opération.

### 🐛 Fix — pipeline d'assemblage des snapshots streaming (OOM memcg 2 Go)

- **Incident 29/10/2026** (snapshot `bc438ade`, 26 apps ≈ 4,4 Go) : OOM kill
  kernel d'uvicorn à ~1,9 Go RSS pendant l'étape `assembling` (dmesg,
  `Memory cgroup out of memory`, memcg du container hub-api, `mem_limit: 2g`)
  → redémarrage du Hub → kill-switch au boot → snapshot marqué
  « Snapshot interrompu (redemarrage du Hub) ». Les logs sont restés muets
  ~60 s avant l'OOM : l'event loop était bloquée par la compression synchrone.
- **Trois chargements entiers du payload en RAM corrigés en streaming** :
  - `snapshot_engine` : `cctx.compress(src.read())` → `copy_stream(...,
    size=<taille du tar>)` — la taille pledged fait embarquer le content-size
    au frame zstd, requis par le `dctx.decompress()` one-shot de
    `restore_snapshot.py` (compat restore vérifiée par test).
  - `snapshot_crypto.encrypt_file` : `AESGCM.encrypt(nonce, read_bytes())` →
    GCM streaming par chunks de 4 Mo (`Cipher`/`update_into`), sortie
    **byte-identique** à l'ancienne enveloppe (ciphertext‖tag) —
    `decrypt_snapshot_bytes` et les snapshots existants restent lisibles sans
    migration.
  - checksum : `sha256(read_bytes())` → `_sha256_file` par chunks.
- **Event loop dégagée** : tar+compress, encrypt et checksum passent par
  `asyncio.to_thread` — le Hub reste réactif (health checks, UI) pendant
  l'assemblage d'un snapshot multi-Go.
- **Tests** : `hub/tests/test_snapshot_streaming.py` reproduit l'OOM sous
  `RLIMIT_AS` dans un subprocess (payload 512 Mo / limite 320 Mo) — l'ancien
  code y échoue en `MemoryError`, le streaming passe ; compatibilité
  déchiffrement one-shot et frame zstd couverte. Suites snapshot/crypto/
  restore : 33/33. Suite complète : 1454 passed — 3 échecs OIDC préexistants
  (identiques sur la base sans le fix, flakiness d'ordre).

---

## [3.77.1] - 28 octobre 2026

### 🐛 Fix — pilotage du coffre depuis l'UI

- **Timeout du backup côté frontend** : `vaultBackup()` porte désormais un
  timeout de 130 s (au lieu du défaut 30 s de `request()`) — l'opération peut
  durer ~110 s côté hôte, le client abortait avant la fin.
- **409/500 du service réencapsulés** : POST /backup renvoie
  `{"detail": payload["erreur"]}` avec le même code HTTP — le client api.js ne
  lit que `error.detail`, le toast affiche désormais « backup déjà en cours »
  au lieu d'un message générique. Les shapes 200 restent des passthrough bytes
  stricts.
- **GET /status : code HTTP du service vérifié** : un 401/403/405/500 JSON du
  service hôte n'est plus fusionné avec `{"service": "ok"}` (faux état sain)
  ⇒ 502 « Réponse invalide du service atelier hôte. ».
- **Échec PRA visible** : après un backup 200 avec `pra.error`, toast warning
  « Sauvegarde réussie mais réplication PRA en échec » au lieu du success ;
  statut du coffre rafraîchi aussi en cas d'échec du backup (catch).

| Fonctionnalité | Description |
|----------------|-------------|
| **Pilotage du coffre plus fiable** | Sauvegarde du coffre : plus d'interruption prématurée sur les opérations longues (timeout aligné sur la durée réelle), message clair quand une sauvegarde est déjà en cours, état du coffre toujours exact même si le service hôte répond une erreur, et échec de réplication PRA désormais signalé. |

---

## [3.77.0] - 28 octobre 2026

### 📣 Pour les utilisateurs

- **Ajouté** : pilotage du coffre de sauvegarde hubctl depuis l'interface (Admin → Sauvegardes → Coffre) : état du coffre, divergences par app, sauvegarde en un clic (le disque n'est jamais modifié ; restauration toujours en CLI).

### ⚡ Coffre hubctl pilotable depuis l'UI

- **Ajouté** : router `/api/admin/vault/{status,backup}` (admin-only) — proxy vers le service hôte hubctl-atelier, audit `VAULT_BACKUP`, timeout configurable `vault_backup_timeout` (défaut 120 s)
- **Ajouté** : workspace « Coffre » (4e workspace de l'onglet Sauvegardes) — état du coffre, divergences par app cliquables, répartition du contenu, sauvegarde avec confirmation + spinner, dégradations propres (service down / coffre verrouillé)
- **Périmètre** : la restauration, l'import et la passphrase restent en CLI

---

## [3.76.0] - 28 octobre 2026

### 📣 Pour les utilisateurs

- **Ajouté** : bouton « ⬆ Mettre à jour » dans la vue santé des applications — l'admin peut mettre à jour une app (récupération du code, rebuild, redémarrage, vérification de santé) directement depuis l'interface du Hub, avec suivi de progression et confirmation. La mise à jour rejoue aussi l'allocation des ports/IP de l'app.

### ⚡ Vue santé — pilotage du pipeline de déploiement

- **Ajouté** : `updateApp`/`pollDeployStatus` (frontend/public/js/modules/apps-health.js) — branchés sur le pipeline existant `POST /api/apps/{slug}/deploy` (pull `--ff-only` + PAT → réécriture `.env` → `make build` → `make up` → health) ; polling 2 s plafonné à 600 s **évalué en tête de tick** (aucune sortie anticipée ne contourne le plafond), conclusion uniquement sur `current.id === deployment_id`, `clearInterval` garanti sur toutes les sorties
- **Gestion d'erreurs** : 409 affiché tel quel sans re-precheck (pas de latence réseau), 422 (body sans `detail` → `'Erreur serveur'`) re-interrogé via `/deploy/precheck` avec liste des checks `fail` (label + détail) et hint transitoire
- **Sécurité** : messages serveur rendus via `textContent` (aucune injection HTML) ; confirmation avant déploiement ; bouton désactivé pendant le cycle

---

## [3.75.3] - 28 octobre 2026

### ⚡ Admin : badge « apps en attente » explicable et actionnable

Incident : le badge « 1 » de l'onglet Applications restait affiché alors qu'aucune
approbation n'était visible. Cause racine : un atelier enregistré avant son app de
base (ex. `exemple-app-ws` vs `exemple-app` — dérivation de slug divergente) reste
`approval_status=pending` à vie ; le badge le compte alors qu'il est invisible
(liste d'approbation dédiée retirée de l'HTML, ateliers masqués par défaut dans le
tableau admin).

- Badge `#pendingAppsBadge` cliquable (`focusPendingApps`) : filtre le tableau
  admin sur « En attente » avec les ateliers rendus visibles — le compteur est
  toujours explicable et actionnable.
- Sémantique unifiée : `updatePendingAppsBadge(count)` devient la source unique
  (SSE/login via `loadPendingAppsCount` ET tableau via `renderAppsTable`),
  comptant les `pending` uniquement (les apps `rejected` ne gonflent plus le
  badge côté tableau — divergence de sémantique entre les deux writers supprimée).
- Purge du code mort `loadPendingApps()` (éléments `pendingAppsTable` /
  `pendingAppsAlert` absents de l'HTML — la fonction aurait levé sur `null`).
- Bouton Supprimer exposé sur les lignes `pending` du tableau admin
  (`DELETE /api/apps/{slug}/pending` déjà fonctionnel : cascade SA + credentials,
  audit log, notification SSE) — permet de nettoyer les fiches d'ateliers
  fantômes sans passer par « Rejeter » d'abord.

---

## [3.75.2] - 28 octobre 2026

### ⚡ Recréation d'app (propagation IP) — overlays https/gpu injectés

- **Corrigé** : `_op_recreate` (propagation d'IP, bouton « Recréer ») lançait `docker compose -f docker-compose.yml up -d --force-recreate` — les conteneurs étaient recréés **sans l'overlay HTTPS de l'app**, qui perdait son port HTTPS (incident exemple-app 27-28/10/2026 : 4231 injoignable après propagation)
- **Nouveau** : `_prod_overlay_files()` injecte `docker-compose.https.yml` (sauf `HTTPS_ENABLED=false` dans le .env de l'app) et `docker-compose.gpu.yml` (si `DOCKER_GPU`), chacun seulement si le fichier existe — parité avec `make up`, `--force-recreate` conservé (indispensable aux variables .env non référencées)
- Tests : 2 nouveaux (overlay injecté / respect de `HTTPS_ENABLED=false`) — 54 passés (workshop + propagation réseau)

---

## [3.75.1] - 28 octobre 2026

### 🆕 Nouvelle version

> ⚠️ Cette entrée a été créée automatiquement par `sync_version.py`.
> Veuillez compléter les détails des modifications.

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.75.0] - 27 octobre 2026

### 🆕 Nouvelle version

> ⚠️ Cette entrée a été créée automatiquement par `sync_version.py`.
> Veuillez compléter les détails des modifications.

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

## [3.75.0] - 27 octobre 2026

### ✨ Ateliers (workshops) — pilotage depuis l'admin Hub et l'agent exemple-app

- **Ajouté** : `GET /api/admin/atelier` — liste native des ateliers actifs (scan `${APPS_HOST_PATH}/*/.workshop.env` + état des conteneurs `-ws` via le socket Docker **read-only** ; Docker injoignable → état « inconnu », jamais 500). Admin-only, audit standard
- **Ajouté** : `POST /api/admin/atelier/close-all` — proxy authentifié vers `hubctl atelier serve` (hôte, localhost:8777, token fichier) ; **600 s** de timeout (`atelier_close_timeout`), 200/207 passthrough, **503 verbatim** si service hôte injoignable
- **Ajouté** : section admin **« Ateliers »** (module `admin-atelier.js`) — tableau des ateliers actifs, actualisation, bouton « Fermer tous les ateliers » avec modale de confirmation (destruction des volumes `-ws`) et résumé closed/failed
- **Config** : `atelier_service_url`, `atelier_service_token_file`, `atelier_close_timeout` ; secret `secrets/atelier_token` (produit par hubctl install)
- Tests : 20 nouveaux (routes admin-only, passthrough, 503, audit) ; suite : 1436 passed

## [3.74.3] - 26 octobre 2026

### ⚡ Sécurité — durcissements P0 de l'audit d'octobre 2026

| Fonctionnalité | Description |
|----------------|-------------|
| **Restore du hub verrouillé (B-1)** | `POST /api/admin/restore` n'accepte plus n'importe quel service account : un SA applicatif pouvait restaurer l'environnement complet du hub (création de comptes admin, écrasement de mots de passe et de clés). Désormais seuls un utilisateur admin et le SA système interne (`HUB_INTERNAL_SA_CLIENT_ID`) passent — refus fail-closed si la variable n'est pas configurée. |
| **Path traversal backup éliminé (B-2)** | `backup_id` (formulaire, query, ou réponse d'une app distante) alimentait directement le nom de fichier de stockage : un id contenant `../` permettait d'écrire hors du dossier de backups. Nouveau helper `safe_backup_filename()` (identifiant atomique strict) appliqué à la réception de backup et à l'import depuis une app distante — un id invalide est rejeté en 400 avant toute écriture. |
| **Open redirect login fermé (B-3)** | `redirect_to` du `POST /api/auth/login` n'était pas validé : une URL externe recevait l'access_token (durée 480 min) en query string. Seuls les chemins relatifs même-origine sont acceptés (rejet de `//`, `\`, backslash et caractères de contrôle), validation fail-fast avant toute écriture en base. |
| **Auto-approbation `/me/shared` supprimée (#6)** | `PUT /api/apps/me/shared` basculait une app `pending` en `approved` (et activait app + SA) dès `is_shared=true`, sur un endpoint accessible par IP+`X-Slug` sans token. Le partage ne change plus le statut : l'approbation reste un acte admin, la propagation aux SA ne concerne que les apps déjà approuvées. |
| **Tests** | 15 nouveaux tests (`hub/tests/test_audit_p0_hardening.py`) + adaptation de `test_endpoint_restore_m2m_auth` qui assertait l'ancien comportement vulnérable. Suite complète : 1415 tests verts. |

---

## [3.74.2] - 24 octobre 2026

### ⚡ build : plus de `network: host` dans les sections build (compose v5/buildx 0.38)

| Fonctionnalité | Description |
|----------------|-------------|
| **make build / up --build débloqués** | compose v5.6.1 + buildx 0.38.1 ne transmettent plus l'entitlement buildkit `network.host` : tout `docker compose build` échouait (« additional privileges requested: pass --allow=network.host »). `network: host` est retiré à la source des sections `build:` (hub-api, hub-frontend) — aucune étape de build n'a besoin du réseau hôte. |
| **Override de test simplifié** | Le `build: !override` de `docker-compose.test.yml` (contournement 3.74.1) devient inutile et est supprimé : il masquait tout futur build arg ajouté au compose principal. Commentaire dans `docker-compose.yml` pour éviter la régression. |

---

## [3.74.1] - 23 octobre 2026

### ⚡ make test-docker : indépendance du .env hôte + hygiène CHANGELOG

| Fonctionnalité | Description |
|----------------|-------------|
| **Tests Docker indépendants du .env prod** | `APPS_HOST_PATH` du `.env` hôte (chemin réel `APPS/`, ex. `/home/<user>/...`) fuyait dans le conteneur de test via l'interpolation compose → 3 tests en échec à tort, dont la non-régression du défaut `apps_host_path`. Surcharge au défaut canonique `/data/apps` dans `docker-compose.test.yml` (le reset par clé `!reset` est ignoré par compose v5) ; le défaut authentique reste couvert par la suite hôte. |
| **Build de test sans entitlement** | `build: network: host` + compose v5 → « additional privileges requested » : l'entitlement buildkit `network.host` n'est pas transmissible à `up --build`. Build redéclaré sans `network: host` dans l'override de test. `make test-docker` complet vert (1358 passed, 1 skipped) avec le `.env` prod en place. |
| **Hygiène CHANGELOG** | Purge d'une table « À compléter » orpheline (vestige du bump 3.72.0 OIDC, sans en-tête de version) et rejet du hunk non commité `[3.72.0] - 17 octobre` (doublon de l'entrée `[3.72.0] - 22 octobre` déjà commitée). |

---

## [3.74.0] - 23 octobre 2026

### ⚡ Launch apps : refresh_token dédié à la session de l'application

| Fonctionnalité | Description |
|----------------|-------------|
| **Refresh token au launch** | `POST /api/auth/apps/{slug}/launch` crée désormais un refresh token utilisateur dédié (7 j, rotation à chaque usage, grâce inter-onglets 60 s) en plus du JWT, tracé en base (`refresh_tokens`, IP + user-agent). Nouveau champ `AppLaunchResponse.refresh_token` (optionnel — absent des flux SSO legacy, comportement inchangé). |
| **Transmission par le portail** | `dashboard.js` (`openApp`) ajoute `&refresh_token=` (URL-encodé) à l'URL de lancement quand le backend en fournit un. Les apps dérivées du template le consomment déjà : `AuthContext` lit `?refresh_token=`, le stocke puis purge l'URL (`history.replaceState`). |
| **Fin des 401 « Token expiré » en impasse** | Quand le JWT de session expire (défaut 8 h), l'app renouvelle silencieusement l'accès via `POST /api/auth/token/refresh` (rotation) sans repasser par le portail — plus besoin de relancer l'app depuis le dashboard après une expiration. |
| **Tests** | TDD : 2 tests (`TestAppLaunch` — champ présent et distinct du JWT + refresh utilisable avec rotation et rôles conservés). Suite : 1400 passed (hôte), 1358 passed + 1 skipped (Docker `pouly-test`). Doc `INTEGRATION.md` § « Ouverture depuis le dashboard Hub » mise à jour. |

---

## [3.73.0] - 22 octobre 2026

### ⚡ Reads M2M admin : lectures admin ouvertes aux Service Accounts autorisés (`can_admin_read`)

| Fonctionnalité | Description |
|----------------|-------------|
| **Flag `can_admin_read`** | Nouvelle colonne `service_accounts.can_admin_read` (booléen, défaut `false` — deny par défaut, migration 060 `ADD COLUMN IF NOT EXISTS`). Aucun SA existant ne change de comportement après déploiement. |
| **Dépendance `get_admin_or_admin_sa`** | Nouvelle garde d'auth (`auth.py`) : user admin OU SA actif porteur du flag, re-vérifiés en base à chaque requête (révocation instantanée, aucun claim JWT consulté). |
| **12 routes GET admin-read ouvertes** | `GET /api/apps/`, `GET /api/apps/{id}`, `GET /api/users/`, `GET /api/users/{id}`, `GET /api/messages/`, `GET /api/messages/stats`, `GET /api/admin/backups/stats`, `GET /api/apps/{slug}/backup/config`, `GET /api/audit/stats`, `GET /api/audit/sessions/active`, `GET /api/audit/errors`, `GET /api/audit/logs` — payloads identiques à ceux d'un admin user. `GET /api/apps/` et `/api/audit/logs` en entity-based : sémantique user (apps propres / own logs) strictement préservée. Aucune route d'écriture ouverte. |
| **Attribution audité** | `PUT /api/service-accounts/{id}` accepte `can_admin_read` ; hook d'audit `sa_admin_read_granted` / `sa_admin_read_revoked`. Champ exposé dans `ServiceAccountResponse`/`ServiceAccountCreated`. |
| **Corrigé au passage** | `PUT /api/service-accounts/{id}` ne renvoie plus 500 (`ResponseValidationError`) : refresh scopé `attribute_names` au lieu du refresh plein (relation `lazy="raise"`). |
| **Tests** | `hub/tests/test_m2m_admin_read_scope.py` : 12 tests (flag/défaut, dépendance, révocation à chaud, sweep paramétré 10 routes × 4 rôles, parité admin `list_apps`, sémantique own-logs, audit grant/revoke). Suite : 1393 passed / 0 failed. Migration 060 vérifiée sur PostgreSQL 16 jetable (colonne + idempotence). |

---

## [3.72.0] - 22 octobre 2026

### ⚡ M2M : `GET /api/users/m2m/list` — listing users pour Service Accounts

| Fonctionnalité | Description |
|----------------|-------------|
| **Nouvel endpoint M2M** | `GET /api/users/m2m/list` (spec exemple-app §5.5) : permet aux apps clientes de résoudre localement les user_id en usernames (et inversement) pour leur affichage — endpoint documenté mais jamais implémenté jusqu'ici (les apps appelantes recevaient un 404). |
| **Réservé Service Accounts** | Auth M2M obligatoire : token utilisateur humain → 403 (pattern `m2m/search`) ; sans token → 401. |
| **Non filtré par app** | Contrairement à `/api/users/m2m/search` (filtrée sur les apps autorisées du SA), la liste n'est pas restreinte : les messages M2M synchronisés via `/api/messages/m2m/sync` peuvent référencer n'importe quel user (expéditeur admin, destinataires cross-apps). |
| **Users inactifs inclus** | Flag `is_active` dans la réponse : une app peut résoudre des identifiants d'anciens messages même si le compte a été désactivé depuis. |
| **Contrat** | Réponse `{users: [{id, username, email, is_active, is_admin}], total}` — conforme à la spec §5.5 ; doc `exemple-app.md` mise à jour (§5.5 et plan d'implémentation). |
| **Tests** | TDD : 2 tests (`TestM2MUsersList` — contrat SA 200 + 403 humain) dans `test_api_users.py` ; suite complète 1382 passed (hôte) et vérification runtime Docker `pouly-test` : 401 sans auth, flow `client_credentials` → 200 conforme. |

---

## [3.71.4] - 12 octobre 2026

### ⚡ Téléchargements résilients : tranches Range avec reprise

| Fonctionnalité | Description |
|----------------|-------------|
| **Reprise sur coupure réseau** | Nouveau module frontend `resilient-download.js` : les gros fichiers sont téléchargés par tranches Range de 4 Mo (206 Partial Content, support natif de FileResponse Starlette). Une coupure TCP en plein transfert ne perd plus le téléchargement : la tranche manquante est retentée avec backoff exponentiel (0,5→8 s), timeout de 90 s par requête pour détecter les connexions pendantes, et la taille finale est vérifiée contre le `Content-Range` annoncé (impossible de livrer un fichier tronqué). Repli automatique en téléchargement simple si le serveur ignore les Range. Corrige l'incident « Failed to fetch » sur export chiffré (coupure à 12,6 Mo / 27 Mo). |
| **Endpoints couverts** | Export chiffré d'un backup d'app (`/export/download`), export ZIP de l'environnement Hub (`/admin/backup/export/download?include_app_backups=true`), téléchargement de snapshot (`/api/admin/snapshots/{id}/download`). Pastille de progression fixe, mise à jour par tranche (aucune animation continue). |
| **Test autonome** | `frontend/tests/test_resilient_download.mjs` (Node ≥ 18, zéro dépendance) : serveur HTTP Range-compliant qui coupe brutalement le socket TCP en pleine tranche ; vérifie la reprise et l'intégrité des 27 208 352 octets reconstitués. Cycle rouge/vert validé. |

---

## [3.71.3] - 12 octobre 2026

### ⚡ install.sh : fusion auto-réparatrice dans un .env conservé

| Fonctionnalité | Description |
|----------------|-------------|
| **Fusion sans écrasement** | Relancer `install.sh` sur un serveur existant ajoute désormais les clés requises manquantes au `.env` conservé (au minimum `APPS_HOST_PATH`, calculée localement) au lieu de sortir sans rien faire. Les secrets non inventables manquants (`POSTGRES_PASSWORD`, `ADMIN_*`, `SECRETS_ENCRYPTION_KEY`) déclenchent une erreur explicite listant les clés. Une valeur existante n'est jamais écrasée. |
| **Automatisation complète** | Avec le fallback Makefile (3.71.2) et `ensure_hub_env()` (hubctl 0.10.7), aucun point d'entrée du stack ne nécessite plus de retouche manuelle du `.env` sur les serveurs installés avant la 3.71.1. |

---

## [3.71.2] - 12 octobre 2026

### ⚡ APPS_HOST_PATH : fallback automatique via le Makefile

| Fonctionnalité | Description |
|----------------|-------------|
| **Mise à jour serveur automatique** | Le Makefile HUB_base calcule `APPS_HOST_PATH` (voisin `../APPS` de HUB_base, depuis son propre emplacement) et l'exporte pour `docker compose` quand le `.env` ne la définit pas. `hubctl update` (make down → git pull → make build → make up-prod) corrige donc les serveurs installés avant la 3.71.1 **sans retouche manuelle du `.env`**. |
| **Priorité respectée** | Environnement du shell > `.env` (lu par compose) > calcul Makefile — une valeur personnalisée existante n'est jamais écrasée. |

---

## [3.71.1] - 12 octobre 2026

### ⚡ APPS_HOST_PATH dynamique — fin du chemin de machine de dev en dur

| Fonctionnalité | Description |
|----------------|-------------|
| **install.sh dynamique** | Le `.env` généré écrit désormais `APPS_HOST_PATH` avec le chemin réel de `APPS/` détecté depuis l'emplacement du script (`SCRIPT_DIR/../APPS`) — plus aucun chemin de machine de dev propagé vers les serveurs déployés. Corrige le precheck « Écriture du dossier APPS » en fail (`PermissionError`) sur serveur distant : le défaut en dur du compose créait un dossier fantôme appartenant à root, non inscriptible par le conteneur hub-api. |
| **Variable obligatoire** | `docker-compose.yml` : `APPS_HOST_PATH` passe en `${VAR:?}` (env du conteneur + bind mount même-chemin hôte/conteneur) — `docker compose up` échoue avec un message explicite si le `.env` ne la définit pas, au lieu de monter silencieusement un chemin faux. |
| **Défaut backend neutre** | `config.py` : `apps_host_path` par défaut `/data/apps` (tests unitaires uniquement) au lieu du chemin de dev ; jamais affiché en runtime puisque le compose fournit toujours la valeur. `.env.example` : placeholder documenté. |
| **Tests** | Non-régression `test_apps_host_path_setting_default` (défaut sans `/home/`) + nouveau `test_apps_host_path_env_override` (lecture env injectée par compose). |

**Migration serveurs existants** : ajouter `APPS_HOST_PATH=<chemin absolu réel>/APPS` dans `HUB_base/.env` puis `docker compose up -d hub-api` (et supprimer l'éventuel dossier fantôme créé en root par le daemon).

---

## [3.71.0] - 11 octobre 2026

### ⚡ Déploiement — precheck des prérequis avant la modale de mise en place

| Fonctionnalité | Description |
|----------------|-------------|
| **Precheck de déploiement** | Nouveau module `deploy_precheck.py` + `GET /api/apps/{slug}/deploy/precheck` : URL git normalisable, dépôt accessible au PAT (API GitHub ; non-GitHub → `git ls-remote`), Makefile racine avec cibles `build:`/`up:` (via raw, sans clone), `.env`/`.env.example`, aucun déploiement en cours, ports + sous-réseau allouables, outils du conteneur (git/make/docker compose v2), démon Docker accessible, `APPS_HOST_PATH` réellement inscriptible (test d'écriture — cas hub tiers en PermissionError). |
| **Bloquant strict** | API GitHub injoignable/timeout = `fail` (décision 2026-10-11), distingué de l'absence (404) grâce à `github_client.fetch_github_raw_with_status`. |
| **UI** | `triggerDeploy` (bouton fusée + fin du formulaire d'ajout) : si `ready=false`, la modale de mise en place n'est PAS ouverte — une modale d'information liste chaque check (ok/fail/warn/skipped) avec le détail de correction. |
| **Défense en profondeur** | Le `POST /{slug}/deploy` rejoue le precheck complet et refuse `422` (JSONResponse : le handler global stringifie `HTTPException.detail` dict ; un refus de validation ne pollue pas le journal d'erreurs). |
| **Tests** | 16 nouveaux (`test_deploy_precheck.py`) + 2 tests du POST alignés sur la garde ; suite : 1378 (hôte) / 1337 (conteneur pouly-test) passed. |

---

## [3.70.2] - 11 octobre 2026

### ⚡ Déploiement — réconciliation subnet Hub ↔ réseau Docker vivant + anti-collision cross-host

| Fonctionnalité | Description |
|----------------|-------------|
| **Réconciliation subnet au déploiement** | `docker_scanner.get_project_network_subnets()` lit les sous-réseaux des réseaux Docker du projet Compose (label `com.docker.compose.project`) ; `port_allocator.reconcile_subnet_with_docker()` aligne l'allocation DB sur le réseau vivant avant l'écriture du `.env` (règle : le réseau gagne). No-op si 0 ou 2+ réseaux, host distant, flag désactivé ; jamais de raise. Corrige l'échec `Échec (2) de 'make'` à l'étape `up` (exemple-app #14/#15) et le crash `network not found` (exemple-app-test #9) : Compose ne recrée plus jamais le réseau d'une app vivante. |
| **Sous-réseaux non scopés par host** | `_used_subnets_for_host` → `_used_subnets` : le démon Docker est partagé et l'host détecté est instable (même machine, plusieurs IP) — supprime les collisions cross-host constatées (10.50.1.0/24 alloué à `exemple-app` ET `exemple-app-test`). |
| **Tests** | 13 nouveaux tests (scanner, réconciliation, pipeline `.env` réconcilié, collision cross-host) + fixture autouse de neutralisation du scan Docker ; suite complète : 1362 (hôte) / 1321 (conteneur pouly-test) passed. |

---

## [3.70.1] - 8 octobre 2026

### ⚡ Sync préférences M2M — authentification des appels vers les apps + tolérance de format

| Fonctionnalité | Description |
|----------------|-------------|
| **Contexte** | La synchronisation d'une catégorie de préférences depuis une app source (ex. exemple-app-api-v3) échouait systématiquement : « L'application a retourné 401 » (découverte) / « L'app source a retourné 401 » (import). |
| **`routes/tags.py`** | `discover_tables`, `discover_columns` et `import_m2m` mintent désormais un token de service éphémère (5 min, `apps={slug: "admin"}`, pattern `app_socle_check.py`) envoyé en `Authorization: Bearer` — les apps du socle exigent `require_role("admin")` sur `/api/hub/data/*`, l'appel nu donnait 401 → 502. |
| **`import_m2m`** | Tolère le format objet `{"values": [...]}` retourné par les routes `/api/hub/data/values` des apps du socle, en plus de la liste JSON directe. |
| **Dépendance côté apps** | À combiner avec le correctif app `hub_data.py` (session en `Depends(get_db)` au lieu de `async with`, sinon 500 une fois l'auth passée) — exemple-app-api-v3 v1.20.10. |

---

## [3.70.0] - 7 octobre 2026

### ⚡ Onglet « Environnement » dans la page Sauvegardes — export/import réactivé (look Ops Console)

| Fonctionnalité | Description |
|----------------|-------------|
| **Contexte** | Les routes d'export/import d'environnement ont été réimplémentées en 3.69.0 (backend), mais l'écran UI correspondant avait disparu lors d'une refonte : le module `backup-import.js` restait chargé sans aucun point d'entrée. |
| **`index.html`** | 3ᵉ onglet workspace « Environnement » dans la page Sauvegardes (à côté d'Applications et Snapshots) + `backupWorkspaceEnv` : avertissement « données sensibles », résumé de l'environnement en 4 stats, deux panneaux — Export (3 switches dont clés JWT désactivées par défaut ; boutons JSON / ZIP) et Import (fichier, preview compteurs + version/auteur/date/clés, catégories cochables avec compteurs, mode de conflit, restauration de clés, carte de résultat succès/avertissements/erreurs). Les 37 IDs DOM attendus par `backup-import.js` sont recâblés via le délégateur `data-action`. |
| **`app-backups.js`** | `switchBackupWorkspace` gère `'env'` → `loadBackupInfo()` (résumé + reset formulaire). |
| **`backup-import.js`** | `downloadBackupExport(format)` : `data-args="json\|zip"` aligne l'option « backups des apps » qui décide du format (JSON sinon ZIP). Rétro-compatible sans argument. |
| **Validation** | Contrôle croisé 37/37 IDs présents sans doublon, toutes les `data-action` résolues, syntaxe JS vérifiée, frontend de test rebuilde (page servie avec le nouveau workspace). |
| **Hors scope** | Aucune modif backend ; import réel non exécuté en prod (endpoints déjà validés en 3.69.0). |

---

## [3.69.2] - 7 octobre 2026

### ⚡ Compteur de connectivité — les ateliers ne comptent plus comme offline

- **`GET /api/apps/status`** : le `summary` (« X/Y applications en ligne »,
  toast admin) ne compte plus que les **apps de production**. Les ateliers
  (`-ws`, `is_workshop=true`, le plus souvent arrêtés) gonflaient le
  dénominateur — ex. « Connectivité 14/20 applications en ligne » avec 6
  ateliers éteints (demande 2026-10-07).
- Les entrées par app restent dans `apps[]` (champ `is_workshop` ajouté) :
  les icônes wifi de la table admin continuent de fonctionner, y compris
  dans la vue filtre « Atelier ».
- **Tests** : `test_api_apps_status.py` (2) — atelier présent dans `apps[]`
  mais exclu du summary ; non-régression sans atelier. Suite hôte 1349
  passed, conteneur ciblé 26 passed.

---

## [3.69.1] - 6 octobre 2026

### ⚡ `make test-docker` : copie de `scripts/purge_orphan_backups.py` dans le conteneur

| Fonctionnalité | Description |
|----------------|-------------|
| **Cause** | Depuis 3.67.0, la cible `test-docker` copiait `tests/`, `pytest.ini` et `sync_version.py` mais pas le script racine `scripts/purge_orphan_backups.py` (le contexte image ne contient que `./hub`) → 6 tests échouaient en conteneur (`test_script_exists` cherchait `/scripts/...` inexistant, + 5 erreurs d'import). |
| **`Makefile`** | La cible `test-docker` copie désormais le script sous `/code/scripts/` (mkdir préalable — `docker cp` ne crée pas le parent). |
| **`hub/tests/test_purge_orphan_backups.py`** | Le chemin du script est résolu en remontant les parents de `__file__` (hôte : `<repo>/scripts`, conteneur : `/code/scripts`), repli sur le comportement historique sinon. |
| **Validation** | `make test-docker` : 1306 passed, 1 skipped, 0 failed. Hôte : 6/6 tests purge. |

---

## [3.69.0] - 6 octobre 2026

### ⚡ Réimplémentation des routes export/import d'environnement (`/api/admin/backup/*`)

| Fonctionnalité | Description |
|----------------|-------------|
| **Contexte** | Les routes `/api/admin/backup/{export,export/download,import,import/upload,import/preview}` avaient été supprimées du backend (commit `e0708e0`, remplacées par le système de snapshots) alors que les clients en dépendent toujours : outils MCP `hub_backup_export_env` / `hub_backup_import_env` (template + 9 apps) et module frontend `backup-import.js`. Symptôme visible : tout upload > 10 Mo recevait un 413 « max 10 MB » du middleware anti-DoS (qui court-circuite avant le 404), masquant la disparition des routes. |
| **`hub/app/routes/admin_backup.py`** | Nouveau router `/api/admin/backup` (admin requis) : l'export réutilise `app.hub_self_backup` (`_do_export` / `_build_zip_archive`, source unique déjà utilisée par le flux AppBackup — inclut uuid/oidc_sub/prenom/nom et paire backup PRA) ; l'import est réimplémenté, adapté au schéma actuel : modes `skip`/`update`/`error`, flags `include_*`, restauration JWT **et** paire backup avec `restore_jwt_keys`, secrets SA écrits au format moderne (`sa_<name>.env`, valeur `ENC:` préservée telle quelle + avertissement si la `SECRETS_ENCRYPTION_KEY` cible diffère), `user_app_roles_updated` compté, checksums SHA-256 vérifiés à l'import ZIP, politique de rétention appliquée par app importée. |
| **`hub/app/main.py`** | Router monté ; middleware anti-DoS : les 3 routes d'upload d'environnement (`/import`, `/import/upload`, `/import/preview`) passent à la limite élargie (`MAX_UPLOAD_BODY_SIZE_MB`, défaut 512 MB) au lieu de la limite globale 10 MB. |
| **Tests** | Nouveau `tests/test_admin_backup_routes.py` (22 cas : présence des routes, 413 > 10 Mo révolus, export JSON/ZIP avec skip des backups sans fichier, modes skip/update/error, création SA + fichier de credentials, upload ZIP avec checksum + rétention + ligne `AppBackup`, preview JSON/ZIP, admin requis). Hôte : 1342 passed. Stack docker `pouly-test` (image rebuilde) : 1296 passed, 6 échecs pré-existants `test_purge_orphan_backups.py` (script racine non copié par la cible `test-docker` depuis 3.67.0, hors périmètre). |
| **Vérification runtime** | Endpoints exercés dans le conteneur de test avec auth admin réelle : export 200, download 200, preview 200, import 200, **import/upload 11 Mo → 200** (413 révolu), logs propres. |

---

## [3.68.0] - 6 octobre 2026

### ⚡ Téléchargement de la CA racine depuis la page Hub (suppression des avertissements HTTPS)

- **Backend — nouveau router `app/routes/pki_ca.py`** (monté dans `main.py`) :
  - `GET /api/pki/ca.crt` : sert `certs/ca/ca.crt` en `FileResponse`
    (`application/x-x509-ca-cert`, nom imposé `POULIDOR-Hub-Root-CA.crt`),
    404 explicite si la PKI n'est pas initialisée ;
  - `GET /api/pki/ca/info` : sujet, validité, empreinte SHA-256 (JSON) ;
  - endpoints **sans authentification** : une CA est publique par nature
    (`ca.key` jamais servi — testé), et une app distante doit pouvoir
    récupérer la chaîne de confiance avant de détenir des credentials
    (URL alignée sur `test_remote_app_m2m.py::test_1_download_ca_certificate`,
    qui appelait un endpoint jusqu'ici inexistant) ;
  - chemin en dur, aucune entrée utilisateur → pas de traversal.
- **Frontend — bouton bouclier « Certificat » dans la navbar** (visibilité
  login/logout alignée sur le changement de mot de passe) ouvrant la modale
  `#certificateModal` :
  - empreinte SHA-256 + validité chargées depuis `/api/pki/ca/info`
    (contrôle utilisateur du fichier installé) ;
  - téléchargement blob de `/api/pki/ca.crt` (pattern existant `api.js`),
    toast succès/échec ;
  - guide d'installation par plateforme en accordéon : Windows (GUI +
    `certutil`), macOS (Trousseau), Linux (`update-ca-certificates`),
    Firefox (magasin séparé), Android, iOS (profils + confiance manuelle).
  - nouveau module `js/modules/cert-download.js` (fonctions globales,
    dispatch `data-action` standard) ; tags `?v=` synchronisés.
- **Docs** : `docs/HTTPS.md` — la voie recommandée d'import de la CA passe
  par le bouton de l'interface ; procédures manuelles conservées.
- **Tests** : `hub/tests/test_pki_ca.py` (4) — téléchargement public (PEM +
  content-type + filename), 404 hermétique PKI absente, `/info`, `ca.key`
  jamais servi ; test 404 rendu hermétique (certs/ existe toujours en
  conteneur — constaté au cycle Docker). Suite conteneur : 12/12.

---

## [3.67.0] - 6 octobre 2026

### ⚡ Script ops : purge one-shot des backups morts (hubs déployés)

- **`scripts/purge_orphan_backups.py`** : purifie le stockage backup d'un
  Hub déployé en un geste — pilote `docker exec` sur le conteneur
  hub-api (aucun accès disque hôte, fonctionne sur tout déploiement
  après `git pull`). Traite les deux niveaux de reliques :
  1. lignes `app_backups` mortes (app propriétaire supprimée, FK
     `SET NULL`) — supprimées après rapport ;
  2. fichiers `*.zip` / `*.encrypted` sous `storage/backups` non
     référencés par aucune ligne survivante — orphelins purgés.
- **Sécurités** : dry-run par défaut (`--apply` requis), garde d'âge
  `--days` (défaut 7 — un backup en cours d'écriture n'est jamais
  orphelin), fichiers référencés par une ligne vivante toujours
  intouchables, retrait des répertoires vides, `--container` pour
  surcharger le nom du conteneur.
- **Tests** : `test_purge_orphan_backups.py` (6) — logique pure
  (référencé conservé, récent protégé, déterminisme, référence NULL).

---

## [3.66.0] - 6 octobre 2026

### ⚡ Propagation IP en un geste + mapping slug→répertoire

- **`POST /api/config/network/propagation-plan/apply-and-recreate`** :
  applique l'IP effective du Hub aux `.env` des apps validées **puis**
  lance la recréation de leurs conteneurs (opérations atelier tracées,
  statut pollable par app). Fin du workflow en deux temps manuels —
  une seule validation admin enchaîne écriture + `up --force-recreate`
  + health-check. Une app déjà occupée garde son `.env` écrit, sa
  recréation est sautée (`skipped_operation_active`) au lieu d'un 409
  qui annulerait le lot.
- **Résolution slug → répertoire** (`_resolve_app_dir`) : le slug
  registre peut différer du nom du répertoire `APPS/` (ex.
  `exemple-app` ↔ `exemple-app`, `exemple-app` ↔
  `exemple-app`) — résolution par `APP_SLUG` du `.env` avec
  fallback `<slug>`. Ces apps n'apparaissent plus en `no_env` dans le
  plan : elles sont désormais propageables et recréables.
- **`GET /api/config/network/propagation-plan`** : nouveau champ
  `env_dir` par app (renseigné si le répertoire diffère du slug) pour
  la transparence du mapping dans l'UI.
- **UI onglet Réseau** : bouton « Propager et recréer (N) » (lot tracé,
  polling global avec récap succès/échecs), bouton « Sélectionner les
  apps à propager » (coche toutes les apps `pending`) ; « Propager la
  sélection » reste disponible pour l'écriture seule.
- **Tests** : 9 nouveaux (`test_network_propagation.py`) — mapping,
  apply sur répertoire mappé, batch, gardes (exclusions, opération
  active, validation payload, auth).

---

## [3.65.0] - 6 octobre 2026

### ⚡ Limite de body configurable — uploads de sauvegarde/import élargis à 512 MB

- **`hub/app/main.py`** — le middleware anti-DoS rejetait en 413 tout body
  > 10 MB (constante `MAX_BODY_SIZE`) sur TOUTES les routes : tout import
  de backup > 10 Mo était bloqué côté API alors même que nginx accepte
  1 G (`client_max_body_size`, cf. 3.47.5). Le middleware choisit
  désormais le seuil selon la route : limite globale pour tout le monde,
  limite élargie pour les 3 routes d'upload de backup
  (`/api/admin/restore`, `/api/apps/{slug}/backup/import`,
  `/api/apps/{slug}/backup/restore/upload`). Le message 413 reflète la
  limite réellement appliquée. Constante remplacée par une lecture
  `get_settings()` à chaque requête (singleton, coût négligeable).
- **`hub/app/config.py`** — deux nouveaux champs `Settings` :
  `max_body_size_mb` (défaut 10) et `max_upload_body_size_mb`
  (défaut 512) — ajustables via `.env` sans rebuild.
- **`docker-compose.yml` / `.env.example`** — passage des deux clés
  (`MAX_BODY_SIZE_MB`, `MAX_UPLOAD_BODY_SIZE_MB`, pattern
  `${VAR:-défaut}`) ; `.env.example` documente la contrainte
  `MAX_UPLOAD_BODY_SIZE_MB <= client_max_body_size` nginx (1 G).
- **Tests** — nouveau `tests/test_api_body_limit.py` (9 cas : rejet
  global 413 + message, tolérance sous le seuil, les 3 routes d'upload
  acceptent entre les deux seuils et rejettent au-delà de la limite
  élargie, route métier non concernée). RED→GREEN via stack
  `pouly-test`. Suite docker complète : 1263 passed (2 échecs
  pré-existants `test_app_deploy.py`, présents sur HEAD sans ce
  changement — port allocator, à traiter séparément).

---

## [3.64.0] - 6 octobre 2026

### ⚡ Audit erreurs : l'acteur M2M exposé dans la liste et le détail

- **`GET /api/audit/errors`** : chaque entrée renvoie désormais
  `service_account_id` et `service_account_client_id` (résolu par lot,
  même logique que l'export CSV). Jusqu'ici une erreur venue d'une app
  M2M affichait « - » dans la colonne Utilisateur de la liste — seul le
  CSV et le détail identifiaient l'appelant.
- **`GET /api/audit/errors/{id}`** : expose aussi
  `service_account_client_id` (résolution par id), pour cohérence
  liste / détail / export.
- **UI Audit (`audit-errors.js`)** : helper partagé `errorActorHtml()` —
  username pour un user, badge « M2M » + `client_id` tronqué (libellé
  complet au survol) pour un service account, repli sur l'id brut si
  l'acteur a disparu entre-temps. Utilisé par la table des erreurs et le
  modal détail.
- **Tests** : 4 nouveaux (`TestErrorEndpointsExposeActor`) — acteur M2M,
  acteur user inchangé, ids orphelins sans crash, détail. Suite audit
  complète verte (26) + suite hôte intégrale (1296).

---

## [3.63.4] - 5 octobre 2026

### 🔧 Catalogue GitHub : TTL sur les réservations de ports (fuite prepare)

- **`app/port_allocator.py`** : une allocation **sans application associée**
  est désormais une *réservation* qui expire après
  `PORT_RESERVATION_TTL_MINUTES` (défaut 30 min, `0` = comportement
  historique). Passé ce délai, ports et sous-réseau redeviennent
  attribuables et la ligne est purgée paresseusement à l'allocation
  suivante (`sweep_expired_reservations`, commit indépendant). Bug
  2026-10-05 : chaque « Ajouter au HUB » abandonné fuyait définitivement
  ses ports + sous-réseau (nettoyage manuel uniquement).
- N'expirent **jamais** : les allocations adossées à une app existante et
  les lignes legacy sans `allocated_at` ; idempotence `(slug, host)`
  conservée (prepare → création garde ses ports).
- **`app/config.py`** + **`.env.example`** : nouveau réglage
  `port_reservation_ttl_minutes` / `PORT_RESERVATION_TTL_MINUTES=30`.
- **`app/routes/github_catalog.py`** : docstring de `/prepare` documentant
  la sémantique de réservation.
- Tests : 6 nouveaux cas (`test_port_allocation.py`) — libération après
  TTL, blocage réservation fraîche, app-adossée immortelle, TTL=0, ligne
  legacy, ré-allocation propre du même slug. Suites : hôte 1292 passed,
  docker 1252 passed, 1 skipped.

---

## [3.63.3] - 5 octobre 2026

### 🔧 VPN : minors revue finale (0444, bannière stale, purge tests)

- **`app/routes/vpn.py`** : un `wg0.conf` préexistant aux perms
  fantaisistes (ex. 0444) faisait échouer la régénération en EACCES
  (500) — remise à niveau 0600 avant ouverture (le propriétaire peut
  toujours chmod). RED→GREEN, test dédié.
- **`admin-vpn.js`** : le bouton Enregistrer (réglages serveur)
  rafraîchit le statut — la bannière « conf absente » obsolète
  disparaît sans ré-entrer dans l'onglet.
- **`Makefile` — `test-docker`** : purge de `/code/sync_version.py`
  (conteneur de test persistant, lane déterministe).
- **Hygiène** : helper test orphelin supprimé. Suites : hôte test_vpn
  37 passed, docker 1246 passed.

---

## [3.63.2] - 5 octobre 2026

### 🔧 Fix — lane docker de tests verte (sync_version.py copiée)

- **`Makefile` — `test-docker`** : copie `sync_version.py` dans le
  conteneur de test. `test_sync_version_guard.py` (3.62.10) importe ce
  module situé à la racine du repo, absent du contexte image (`./hub`)
  → `ModuleNotFoundError` à la collection depuis 3.62.10 (10 tests
  invisibles à la lane docker). Repassée verte : 1245 passed, 0 erreur.

---

## [3.63.1] - 5 octobre 2026

### 🔧 Durcissement VPN toggle (suite incident 02/10)

- **`app/routes/vpn.py`** : `GET /api/vpn/status` expose `conf_exists` —
  l'état résiduel « activé + initialisé + conf absente » devient visible
  par l'UI (invisible jusqu'ici, c'est l'état dans lequel l'incident
  02/10 avait laissé le serveur).
- **`app/routes/vpn.py` — `_regenerate_and_sync`** : création de
  `wg0.conf` via `os.open(mode 0o600)` — `write_text`+`chmod` laissait
  une fenêtre umask (~0644) sur un fichier contenant la clé privée
  serveur (revue froide 05/10). `chmod` conservé pour les fichiers
  préexistants (O_TRUNC garde leurs perms).
- **Frontend admin-vpn.js** : garde anti double-fire sur le toggle
  (réentrance + disable pendant la requête) — cause déclencheuse de
  l'incident 02/10 (OFF×2 puis ON×2 en 10 ms dans l'audit).
- **Frontend admin-vpn.js + index.html** : bannière d'état résiduel
  avec bouton **Réparer** (rejoue PUT server-config qui régénère la
  conf depuis la DB), garde anti double-clic, undefined-safe.
- **docs/GUIDE_ADMIN.md** : cycle OFF (supprime la conf) / ON
  (auto-régénère) + `hubctl vpn sync` post-reboot documentés.
- **Tests** : conf_exists absent/présent, garde toggle ON sans serveur
  initialisé, création 0600 même si chmod échoue (RED→GREEN). Suite
  hôte complète : 1285 passed.

---

## [3.63.0] - 5 octobre 2026

### ⚡ Audit sécurité — échecs de login tracés + appelant rattaché aux erreurs

- **`login_failed` dans l'audit** : toute tentative de connexion infructueuse
  sur `/api/auth/token` (grant password) et `/api/auth/login` crée désormais une
  entrée `AuditLog(action="login_failed")` avec l'**identifiant tenté** en
  `target` (même pour un utilisateur inexistant), l'utilisateur rattaché si
  existant, la raison lisible en `details` (Utilisateur inconnu / Mot de passe
  incorrect / Compte désactivé), IP, user-agent et code de réponse. Le mot de
  passe n'est **jamais** logué (body brut jamais persisté).
- **Rattachement de l'appelant aux `error_logs`** : le helper
  `_resolve_error_actor()` décode le JWT Bearer des requêtes en erreur
  (signature vérifiée via le trousseau, expiration ignorée, scope `pwd_reset`
  exclu) pour renseigner `error_logs.user_id` (claim `uid`, fallback `sub`) ou
  `error_logs.service_account_id` (par `client_id`). Colonnes déjà présentes
  (migration 003) — alimentées pour la première fois ; aucun rattachement en
  cas de token absent/invalide (fallback NULL, jamais d'exception). Appelé
  uniquement sur le chemin d'erreur : zéro impact sur le chemin nominal.
- **Filtre username de l'audit élargi** : `GET /api/audit/logs?username=X`
  matche désormais aussi le `target` des `login_failed` (recherche d'un
  identifiant tenté inexistant possible).
- **UI Audit** : colonne « Utilisateur » dans la table des Erreurs (le modal
  détail l'affichait déjà — jusqu'ici toujours « - »), export CSV erreurs avec
  colonne Utilisateur (`username` ou `sa:<client_id>`), label + badge rouge
  « Connexion échouée » dans l'onglet Logs.
- **Tests** : 12 cas d'intégration (`tests/test_api_audit_login_failed.py`)
  couvrant login_failed × 4 scénarios, rattachement Bearer valide/expiré/M2M/
  forgé/absent, filtre username élargi. Rouge/vert prouvé par stash ; suite
  hôte 1281 passed ; suite Docker (Postgres) 1230 passed.
- Note release : l'entrée 3.62.11 (hotfix install-version-guard) avait été
  commitée sans bump VERSION — rattrapée par ce bump 3.63.0.

---

## [3.62.11] - 4 octobre 2026

### 🔧 Hotfix — `scripts/install-version-guard.sh` : chemin double `hooks/hooks/`

- Même correctif que le méta-repo 0.71.10 : l'installateur écrivait
  `<GIT_DIR>/hooks/hooks/pre-push-version-guard.sh` dans le hook chaîné
  (double `hooks/`) → `No such file or directory` à chaque push des clones
  où le hook est installé (fail-closed). Chemin corrigé ; hook file local
  (.git/modules/HUB_base/hooks/pre-push) réparé. Hotfix immédiat
  post-merge (fichier introduit en 3.62.10).

---
## [3.62.10] - 4 octobre 2026

### 🔧 Outillage release — garde-fous anti-écrasement croisé

- **Incident 2026-10-04** : deux sessions releaseant en parallèle ont
  produit un doublon de numéro (méta 0.71.6 ×2) puis une régression de
  VERSION (0.71.6 poussée après 0.71.7).
- **`sync_version.py` — le `bump` vérifie AVANT toute écriture** :
  - origin pas strictement en avance (`get_origin_version()` : fetch
    upstream + VERSION distant ; silencieux hors-ligne — jamais de blocage
    d'un workflow légitime) ; abort code 2 avec la marche à suivre ;
  - numéro cible non déjà pris dans le CHANGELOG (double bump parallèle).
- **Hook pre-push** (`scripts/hooks/pre-push-version-guard.sh` +
  `scripts/install-version-guard.sh`) : dernier rempart git — refuse le
  push d'une VERSION strictement inférieure à celle de la branche distante ;
  multi-hooks chaînables, partagé avec les worktrees via le common dir.
- **Tests** : +10 (`test_sync_version_guard.py` : origin en
  avance/égal/derrière/indéterminable, collision CHANGELOG, bump exit 2
  sans écriture). Hook validé E2E sur repo simulé.

---

## [3.62.9] - 4 octobre 2026

### 🔧 Fix — Dashboard : badge « Échec déploiement » sur les cartes d'app

- **Scénario rapporté** : une app créée depuis le front dont le déploiement
  échoue (clone/build/up/health) restait affichée sur le menu principal
  comme les autres — carte active, rien de fonctionnel derrière, aucune
  indication pour l'utilisateur ni l'admin.
- **`frontend/public/js/modules/dashboard.js`** : badge rouge « Échec
  déploiement » à côté du nom quand `last_deployment.status === 'failed'`
  (pattern badge ATELIER existant), tooltip avec la date de l'échec et le
  rappel du redéploiement possible depuis l'administration. L'information
  existait déjà côté API (`last_deployment` exposé par `GET /api/apps`,
  consommé uniquement par la vue admin pour le bouton 🚀) — elle est
  désormais visible sur le menu principal. Statut success : aucun changement
  d'affichage.
- Release 3.62.8 (logging horodaté + epoch_ms) poussée en parallèle par une
  autre session : bump patch décalé en conséquence (3.62.9).

---

## [3.62.8] - 4 octobre 2026

### ⚡ Logging horodaté à l'heure du Hub + contrat `epoch_ms`

- **`app/logging_config.py`** (nouveau) : `setup_logging()` configure root +
  loggers uvicorn (`uvicorn`, `uvicorn.error`, `uvicorn.access`) avec le
  format commun du parc `2026-10-04T12:34:56.789Z | LEVEL | logger | message`
  — ISO 8601 UTC, règle parc n°10. Niveau via `LOG_LEVEL` (défaut INFO).
  `HubClock` porte l'horloge de référence (offset nul côté hub : le Hub EST
  l'autorité temps) ; `HubTimeFormatter` horodate chaque record depuis
  `HubClock.now_utc()`. Idempotent ; les filtres existants (ex.
  `_SuppressLegacySSE401Filter` sur uvicorn.access) sont préservés.
- **`main.py`** : `setup_logging()` appelé à l'import (après la config
  logging uvicorn, qui tourne avant l'import de l'app) — tous les logs
  hub (access inclus) portent désormais un timestamp.
- **`GET /api/system/time`** : nouveau champ **`epoch_ms`** (ms epoch du
  Hub) — contrat de synchro d'horloge du parc : les apps l'utilisent pour
  aligner l'horodatage de leurs logs sur l'heure du Hub (point médian
  request/réponse). Champs existants (`utc_now`, `utc_now_human`,
  `timezone`) inchangés (rétro-compat frontend + MCP).
- **Tests** : `tests/test_logging_config.py` (12 tests — HubClock offset/
  fallback, format ISO Z, setup idempotent, contrat epoch_ms).

---

## [3.62.7] - 4 octobre 2026

### 🔧 Fix — Vue « Apps GitHub » : 4 anomalies relevées par la campagne de tests boutons

Campagne de tests fonctionnels de tous les contrôles (04/10/2026, Hub réel) —
7/9 contrôles OK au premier passage, 4 anomalies corrigées :

- **Persistance du toggle cartes/table** : `_ghState.view` initialisé vide
  pour que `_ghGetView()` lise `localStorage` au boot — la valeur `'cards'`
  initiale court-circuitait la lecture (toujours cartes après reload malgré
  le choix table).
- **`fetched_at` global** (backend) : `MAX(fetched_at)` sur tout le cache au
  lieu du seul résultat filtré — un filtrage sans résultat n'affiche plus
  « jamais synchronisé ».
- **Timezone** : `formatRelative`/`_ghFormatDay` traitent les timestamps
  naïfs comme UTC (`_ghParseUtc`) — « sync il y a 2 h » juste après une sync
  réussie sur un poste en UTC+2.
- **`integrated_app_slug`** (backend + front) : `GET /api/apps/github/repos`
  renvoie le slug de l'APP liée au repo (par `git_repo_url` ou par slug
  dérivé) ; « Voir l'app » l'utilise — repo POULIDOR trouve désormais l'app
  « hub », exemple-app-2 l'app « exemple-app » (avant : recherche sur « poulidor » /
  « exemple-app » → 0 résultat).

Tests : +4 (`TestGlobalFetchedAt`, `TestIntegratedAppSlug` ×3). Suite verte
: 116 tests (catalogue, hub_host, deploy, github_client, repo_access, token
monitor). Validation E2E sur le Hub réel post-déploiement.

---

## [3.62.6] - 3 octobre 2026

### 🎨 UI — Refonte de la vue « Apps GitHub » (alignement design system)

- **En-tête standard** : `h5` + `bi-github` + badge compteur de repos ;
  état du token en badge Bootstrap (valide/invalide/absent) au lieu du
  texte inline coloré ; boutons « Token… » et « Synchroniser » en `btn-sm`
  (fix régression : l'icône `bi` du bouton sync n'est plus écrasée par un
  emoji au premier clic).
- **Toolbar** en `card admin-apps-toolbar` (pattern onglet Applications) :
  recherche avec icône + bouton de réinitialisation, filtre owner, toggle
  d'affichage **cartes/table** mémorisé en localStorage (défaut : cartes).
- **Vue cartes** : tuile icône colorée par état (dispo/intégré/archivé),
  badge d'état, description clampée 2 lignes, meta de mise à jour + action.
  **Vue table** : dense, pattern table admin (Dépôt/Description/Owner/Maj/
  État/Action).
- **Formulaire token** déplacé en modal Bootstrap standard
  (`#githubTokenModal`, pattern modals global) avec aide sur les scopes —
  fin du bloc dépliable stylé inline.
- **Empty states** en pattern `bi` : aucun token (CTA « Configurer le
  token »), cache vide (CTA « Synchroniser »), filtres sans résultat
  (CTA « Réinitialiser »).
- **« Voir l'app »** devient fonctionnel : bascule vers l'onglet
  Applications avec recherche préremplie (remplace le stub toast).
- **CSS** : classes dédiées `.gh-*` réutilisant les tokens `:root`
  (`--vpn-*-bg`, `--text-muted`) — zéro valeur hexadécimale en dur, zéro
  style inline, une seule transition légère (pattern `.app-card`).
- Référence croisée : la bannière du formulaire d'app pointe désormais
  `openGithubTokenModal` (l'ancienne action inline a été retirée).
- Fichiers : `frontend/public/index.html`, `frontend/public/js/modules/
  admin-apps-github.js`, `admin-app-form.js`, `frontend/public/css/
  style.css`. Maquettes validées au préalable (Visual Companion).

---

## [3.62.5] - 3 octobre 2026

### 🔧 Fix — Déploiement GitHub des apps : 3 causes d'échec corrigées (E2E exemple-app)

Campagne de tests E2E du déploiement d'apps depuis le front-end (token GitHub
PAT), cobaye `exemple-app-test` (template v0.3.0) — 2026-10-03.

- **`app/config.py` — `get_hub_host()` en exécution conteneurisée (critique)** :
  l'IP LAN de l'hôte n'étant jamais une interface locale du conteneur, le check
  anti-déplacement invalidait systématiquement `HUB_HOST` et retombait sur
  l'IP interne Docker (`10.42.x.x`) — en cascade : `.env` des apps déployées
  avec un `HUB_HOST` injoignable (`hub_connected=false`, aucun
  auto-enregistrement), health check du pipeline en `Connection refused`
  (déploiement `failed` alors que l'app tourne), URLs d'app fausses en DB,
  certs SSL avec un mauvais SAN. En conteneur (`/.dockerenv` ou env
  `container`, détecté par `_running_in_container()`), la valeur configurée
  est désormais conservée ; la validation anti-déplacement garde du sens hors
  conteneur uniquement.
- **`app/github_client.py` — `normalize_github_url()`** : les URLs SSH
  (`git@host:path`, `ssh://git@host/path`) sont converties en HTTPS au trois
  points d'entrée — `POST /api/apps` (stockage), `check_repo_access`
  (préflight), clone du pipeline (défense pour les apps existantes). Le PAT ne
  s'applique qu'au HTTPS et le conteneur hub n'a pas de binaire ssh
  (« cannot run ssh » — 4 échecs exemple-app du 18 au 26/09).
- **`app/routes/github_catalog.py` — `already_integrated` par slug** : un repo
  dont le slug dérivé est pris par une app existante sans `git_repo_url`
  (déployée manuellement) apparaissait « Dispo » puis 409 « slug déjà utilisé »
  à la création (cas exemple-app). La carte est désormais « Intégré ».
- **`hub/Dockerfile`** : `pytest.ini` + `hub/tests` embarqués dans l'image —
  suite exécutable in-conteneur sans docker cp ni `-o asyncio_mode=auto`
  (workflow verify-feature).
- **Tests** : +8 nouveaux (`test_hub_host.py` ×8, `TestNormalizeGithubUrl`
  ×10 params, `TestAlreadyIntegratedBySlug` ×2,
  `test_run_deployment_normalize_ssh_url`) ; `test_hub_host_locality.py`
  adapté (les cas « stale » simulent l'exécution hors conteneur). Scope vert :
  299 tests (deploy, catalogue, repo_access, token monitor, config ports,
  allocation, API apps, workshop, réseau, health instance).

---

## [3.62.4] - 2 octobre 2026

### 🔧 Fix — VPN : le toggle ON régénère `wg0.conf` absente (auto-réparation)

- **Incident (02/10/2026 16:53)** : toggle OFF puis ON en 1 s via l'UI → le OFF
  supprime `wg0.conf` du volume `hub_base_vpn-config` (comportement historique),
  mais le ON ne le régénérait pas : conteneur `hub-wireguard` démarré sans
  config, tunnel mort malgré `vpn_enabled=true` (« conf=ABSENTE » côté
  `hubctl vpn sync`, retour tunnel OK uniquement après régénération manuelle
  depuis la DB). Audit : `VPN_DISABLED`×2 → `VPN_ENABLED`×2 (double-fire UI).
- **`app/routes/vpn.py` — `toggle_vpn`** : au toggle ON, si le serveur est
  initialisé (`vpn_server_private_key` en DB) mais `wg0.conf` absent, la conf
  est régénérée depuis la DB (clés + peers) via `_regenerate_and_sync` (port
  `vpn_listen_port`, subnet `vpn_subnet`). Si la conf existe déjà, comportement
  inchangé (aucune réécriture).
- **Tests** (`tests/test_vpn.py` — `TestVpnToggle`) : reproduction rouge/vert
  du scénario incident (OFF → ON → conf régénérée avec `[Interface]` +
  `ListenPort`) + garde-fou « conf déjà présente → pas de réécriture ». Suite
  complète verte (1223 passed).

---

## [3.62.3] - 30 septembre 2026

### 🔧 Fix — auto-détection robuste du hub-host (Hub déplacé/cloné)

- **Problème** : un `HUB_HOST` configuré (`.env` ou DB `app_settings`) hérité
  d'un clone, d'un déplacement de poste ou d'une restauration PRA était servi
  tel quel par `get_hub_host()` — toutes les apps resynchronisaient l'ancienne
  IP (ex. `192.0.2.10`) via `GET /api/config/hub-host` à chaque
  `make up`/`build`/`sync-env`, en boucle.
- **`app/config.py` — `get_hub_host()`** : si la valeur configurée est une IP
  n'appartenant à aucune interface locale du poste, fallback sur l'IP
  auto-détectée (`get_server_ip()`) + warning loggué (1× par IP). Un hostname
  (DNS) reste toujours accepté. Nouveau helper `_is_local_ip()` : check UDP
  connect (aucun paquet émis), caché 60 s (la route est appelée à chaque
  requête de config).
- **`app/main.py` — `GET /api/config/hub-host`** : lit désormais
  `get_settings()` à l'appel au lieu de la référence module-level `settings`
  figée à l'import (bug latent : servait un état périmé après reconstruction
  du singleton).
- **Tests** : `tests/test_hub_host_locality.py` (7 cas — fallback, warning
  unique, IP locale conservée, hostname, cache) ; conftest global neutralise
  la localité (`_is_local_ip=True`) pour les tests de mécanique utilisant des
  IP de documentation RFC 5737 ; suite complète verte (1221 hôte, 1181+1 en
  stack Docker isolée `pouly-test`).

---

## [3.62.2] - 27 septembre 2026

### 🔧 Fix — crash démarrage hub-api : greenlet absent avec SQLAlchemy 2.1

- **Dépendances** (`hub/requirements.txt`) : `sqlalchemy>=2.0.0` →
  `sqlalchemy[asyncio]>=2.0.25,<3.0`.
- **Cause** : la contrainte flottante résolvait SQLAlchemy 2.1.x, qui
  n'installe plus `greenlet` automatiquement (devenu extra optionnel).
  L'import de `sqlalchemy.ext.asyncio` (utilisé par `app/database.py`)
  levait alors `ImportError: The SQLAlchemy asyncio module requires that
  the Python 'greenlet' library is installed` → hub-api en crash boucle
  au démarrage, hub et apps indisponibles.
- **Fix** : l'extra `[asyncio]` garantit la présence de greenlet pour
  SQLAlchemy 2.0.x comme 2.1.x ; borne haute `<3.0` pour bloquer toute
  future rupture de major. Alignement sur la convention déjà en vigueur
  dans `HUBapp_TEMPLATE_base` et les apps du parc.

---

## [3.62.1] - 26 septembre 2026

### 🔧 Fix — scories release : entrée CHANGELOG orpheline + test flaky

- **CHANGELOG** : suppression de l'entrée placeholder orpheline « [3.61.1] -
  20 septembre 2026 » (scorie d'un bump auto `sync_version.py`, doublon de
  l'entrée réelle du 19/09 — même numéro de version, contenu « À compléter »).
- **Tests** : `test_run_deployment_success` était flaky (~1,6 %) — le
  placeholder `POSTGRES_PASSWORD=x` dans le `.env` de test réapparaissait
  quand `secrets.token_urlsafe(24)` générait un secret commençant par `x`
  (~1/64), faisant échouer l'assertion `not in`. Remplacé par `?` (hors
  alphabet urlsafe `[A-Za-z0-9_-]`) → assertion déterministe.

---

## [3.62.0] - 26 septembre 2026

### ⚡ Notifications temps réel sans refresh + badge messages filtrable

- **SSE erreurs enrichi** : `_log_error_to_db` (point unique de création
  d'`ErrorLog`) émet désormais `audit_updated` avec
  `{"action": "error_logged", "error_code", "source_app_slug"}` — le badge
  erreurs de la navbar (Audit) et la liste Erreurs (sous-onglet visible) se
  mettent à jour sans refresh (debounce 800 ms côté client).
- **Nouvel événement SSE `active_users_changed`** (cooldown 2 s,
  `notify_active_users_changed`) émis à chaque changement de présence :
  start/end session (login, logout, force logout, déconnexion SSE, cleanup
  stales), ouverture/fermeture de segment d'usage par app — les vues Audit
  (Vue d'ensemble, Uptime, Par Application, Sessions) se rafraîchissent en
  direct ; le dashboard reste couvert par `stats_updated`.
- **Badge messages navbar filtrable par priorité** : nouvelle préférence
  GLOBALE instance `messages_badge_priorities` (AppSetting, défaut toutes,
  `[]` = badge masqué) + endpoints admin `GET/PUT
  /api/messages/badge-priorities` (422 si priorité invalide). Le PUT émet
  `messages_updated` + `settings_updated` → recalcul du badge chez tous les
  admins ouverts. Frontend : le badge somme `stats.by_priority` sur les
  priorités cochées ; roue crantée à côté des chips « Priorité » de la vue
  admin Messages (popover 3 checkboxes, sauvegarde au changement).
- Perf : aucun nouveau polling, aucune animation, fetchs conditionnés à la
  visibilité des vues. 10 nouveaux tests (présence/cooldown, badge-priorities) ;
  suite complète 1213 passed.

---

## [3.61.1] - 19 septembre 2026

### 🔧 Fix — SAN des certs apps : localhost inclus

- `POST /api/apps/cert/issue` : le SAN des certs émis inclut désormais
  `PKI_CERT_DOMAINS` (localhost, hub.local, hub-frontend) en plus du host du
  HUB — `curl --cacert ca.crt https://localhost:<port>` passe la vérification
  (accès opérateur depuis le serveur). Changement de SAN → ré-émission
  automatique par `cert_sync` (boucle 6 h ou restart de l'api app).

---

## [3.61.0] - 19 septembre 2026

### 🆕 HTTPS des apps — émission des certificats par la PKI du HUB

- **`POST /api/apps/cert/issue`** (auth M2M) : émission/remplacement du cert serveur
  d'une app signé par la « Hub Local CA » (RSA 4096, SAN = `PKI_CERT_DOMAINS` +
  `PKI_CERT_IPS` + `HUB_HOST`, validité 365 j). Ré-émission automatique si expiration
  proche (marge 30 j) ou changement de SAN (ex : changement d'IP du serveur).
- Garde anti cross-slug : un service account ne peut demander un cert que pour les
  slugs qu'il possède.
- Consommé par `backend/cert_sync.py` des apps template (boucle 6 h + au démarrage) :
  les apps basculent d'un cert auto-signé à un cert signé par la CA sans intervention.
- Cherry-pick de `feat/apps-github-refonte` (f315af4, 63b13c2, e90a99d — revus) sur
  main 3.60.1 ; 7 tests dédiés (`tests/test_app_certs.py`).

---

## [3.60.1] - 19 septembre 2026

### ⚡ Fix — nginx : imports de gros backups (client_max_body_size 1G)

| Changement | Description |
|------------|-------------|
| **`client_max_body_size` 50M → 1G** | `frontend/nginx.conf`, `nginx-ssl.conf` (80/443) et `nginx-philia-ng.conf` (proxy externe). La limite volontaire de 3.47.4 (posée contre le défaut nginx 1 Mo) bloquait en 413 tout import de backup > 50 Mo — incompatibles avec les backups volumineux (incident exemple-app > 150 Mo). Contexte local : relève à 1 G |

---

## [3.60.0] - 18 septembre 2026

### ⚡ Feature — Ajout d'applications fiable et non intrusif (spec 2026-09-17)

| Changement | Description |
|------------|-------------|
| **Préflight d'accès Git** | Nouveau `hub/app/repo_access.py` : `git ls-remote` avec le PAT (mécanique `http.extraheader` réutilisée du déploiement — token jamais en ligne de commande ni en log) + diagnostic API GitHub. Messages actionnables : SSO d'organisation non autorisé, droit `repo` manquant, token absent/invalide, GitHub inaccessible, URL non GitHub |
| **Création bloquée (défense serveur)** | `POST /api/apps/` refuse (422 « Dépôt Git inaccessible : … ») et **n'insère aucune ligne** si `git_repo_url` n'est pas clonable — fin des cartes fantômes créées puis jamais déployées |
| **Catalogue GitHub** | `POST /api/apps/github/prepare` renvoie `repo_access {ok, reason_key, reason}` (non bloquant) ; nouveau `POST /api/apps/github/check-access` (admin) pour re-vérifier une URL éditée dans le formulaire |
| **Formulaire d'ajout** | Bandeau vert « Accès au dépôt vérifié » / rouge avec liens « configurer le token » et « réessayer » ; bouton Enregistrer désactivé si accès refusé ; re-check à la soumission si l'URL change ; garde slug invalide avec message clair (fin des 422 obscurs) ; champ slug `required` |
| **Déploiements parallèles** | Verrou global `asyncio.Lock` remplacé par `Semaphore(max_concurrent_deployments)` (défaut 3, borné 1-6, configurable via `.env`) ; le 409 ne vise plus que la même app (`has_active_deployment_for`) ; les déploiements excédentaires attendent en file (statut DB `pending` jusqu'à l'acquisition d'un slot) |
| **Requalification au boot** | Les `app_deployments` restés `pending/running` après crash/redémarrage sont requalifiés `failed` (« Interrompu par redémarrage du HUB ») — fin des 409 permanents (pattern kill-switch backups/snapshots) |
| **UX non intrusive** | Modal de déploiement refondée en suivi multi-déploiements : polling global unique, modal purement visuelle et **fermable sans arrêter le suivi** (l'ancien `hidden.bs.modal` qui tuait le polling est supprimé), toasts de fin succès/échec, suivi prolongé à 30 min |
| **Post-création allégée** | Modal fermée dès la création réussie ; refresh token + rechargements admin/dashboard en arrière-plan non bloquants (`silent=true`) |
| **Délégateur durci** | `preventDefault()` **systématique** sur les `form[data-action]` (un handler absent ne déclenche plus le submit natif = plus de rechargement de page parasite) ; fallback visible « Action indisponible — rechargez la page » sur click/submit/change/input |
| **Badge « Non déployée » retiré** | Décision produit : plus aucun badge de statut de déploiement dans l'admin |
| **Tests** | +16 tests (repo_access 7, prepare/check-access 6, garde 422 3) ; suite hôte 1196 PASS ; suite docker (`make test-docker`) 1156 PASS + 1 skip ; fix `test_frontend_js_syntax` (skip module-level) et 422 littéral (starlette 0.48) pour la validation conteneur |

---

## [3.59.4] - 17 septembre 2026

### ⚡ Fix UI — vue d'ensemble Audit écrasée au retour d'un autre onglet

| Correctif | Description |
|-----------|-------------|
| **Symptôme** | Page Audit & Suivi : après avoir consulté « Uptime utilisateurs », la heatmap « Activité du parc » de la vue d'ensemble revenait écrasée (bande étroite) jusqu'à un F5 |
| **Cause** | Un re-render déclenché pendant l'onglet caché (SSE `audit_updated` → `loadAuditPageOverview`) crée l'instance ECharts sur un panneau `display:none` → canvas mesuré à 0 px (fallback 100 px) ; au retour, `renderUsageHeatmap` réutilisait cette instance avec `setOption()` **sans `chart.resize()`** — contrairement à `renderUserDayTimeline` et `renderUserDayBreakdown` qui l'appellent déjà |
| **Fix** | `chart.resize()` en fin de `renderUsageHeatmap` — re-mesure le conteneur désormais visible |
| **Validation E2E** | Scénario headless Chromium (session admin réelle) : render caché w=0 → retour : canvas 100 px / ECRASÉ avant fix ; après fix le render du retour re-mesure à 662 px (= conteneur) / OK — reproduction déterministe |

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
  en continu pendant et après le backup (validé sur banc interne, preuve non publiée).

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
