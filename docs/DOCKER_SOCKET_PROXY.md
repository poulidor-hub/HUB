# Proxy Socket Docker — architecture & whitelist (chantier A 1a)

> Depuis la v3.78.0, **aucun conteneur du HUB ne monte `/var/run/docker.sock` directement**,
> hormis `docker-socket-proxy` (tecnativa, image pinée `v0.5.0`). Le service `hub-api` y
> accède via `DOCKER_HOST=tcp://docker-socket-proxy:2375`.

## Pourquoi

Le montage direct du socket équivaut à root sur l'hôte : une compromission de l'API FastAPI
donnait le contrôle du daemon Docker (conteneurs, images, réseaux, volumes) et donc de tout
le serveur. Le proxy filtre les familles d'endpoints de l'API Docker selon une whitelist
dérivée d'un **audit des appels réels** du code (SDK Python + CLI compose). Toute famille
non listée répond `403 Forbidden`.

## Whitelist (audit du 02/11/2026)

| Variable proxy | Pourquoi | Preuve (appel réel) |
|---|---|---|
| `POST=1` | Gouverne **tous** les verbes non-GET (le proxy n'a pas de variable DELETE) | `start/stop/build/up`, `DELETE` des recréations compose |
| `CONTAINERS=1` | Scan, ports occupés, logs, stats, inspect | `docker_scanner.py:299,513`, `apps_health.py:147,460`, `apps.py:3768,3913`, `atelier.py:67` |
| `ALLOW_START=1` / `ALLOW_STOP=1` / `ALLOW_RESTARTS=1` | Restart/stop/start des conteneurs | `apps_health.py:341,372,405`, `vpn.py:89`, Makefile `restart` |
| `EXEC=1` | `wg show` dans le conteneur WireGuard ; `compose exec` (db-shell, tests) | `vpn.py:78`, Makefile `test-docker`, `db-shell` |
| `NETWORKS=1` | Sous-réseaux occupés (allocation ports), création/suppression par compose | `docker_scanner.py:536,563`, `port_allocator.py:42,271`, `workshop_service.py:266` |
| `BUILD=1` + `SESSION=1` + `GRPC=1` | Build compose moderne = buildx (flux hijackés session/grpc) | Makefile `build` via `deploy_service.py:376`, `workshop_service.py:624,773` |
| `IMAGES=1` | Pull implicite `compose up`, cache de build | Makefile `up` |
| `INFO=1` / `SYSTEM=1` / `VERSION=1` / `PING=1` | `client.info()/version()/ping()` | `docker_scanner.py:250,585,588` |
| `VOLUMES=1` | `compose down -v` (destroy atelier, clean) | `workshop_service.py:826,863` |
| `EVENTS=1` | Accordé par défaut du proxy (flux lecture seule) | — |

**Familles volontairement fermées** (aucun usage, `403`) : `AUTH`, `COMMIT`, `CONFIGS`,
`DISTRIBUTION`, `NODES`, `PLUGINS`, `SECRETS`, `SERVICES`, `SWARM`, `TASKS`,
`ALLOW_PAUSE`, `ALLOW_UNPAUSE`.

## Points d'attention

- **`POST=1` couvre aussi DELETE/PUT** : c'est une limite de conception du proxy (cfg
  haproxy : `deny unless METH_GET || env(POST)`). Le filtrage par famille d'endpoints
  reste actif.
- **buildx exige `SESSION`/`GRPC`** : sans eux, `compose build` échoue sur les moteurs
  récents (builder classique supprimé). Ne pas retirer ces variables pour « durcir ».
- **`entrypoint.sh`** : l'ajustement du GID docker est gardé par
  `if [ -S /var/run/docker.sock ]` — sans montage, il est sauté (aucun `group_add` sur
  hub-api). `HOST_DOCKER_GID` dans `.env` est devenu sans effet pour hub-api.
- **hubctl et les scripts hôte** ne passent pas par le proxy : ils tournent côté hôte avec
  le socket direct (hors périmètre, inchangés).
- **Limites (scope 1a)** : hub-api reste l'exécuteur des déploiements avec une whitelist
  large (c'est son rôle d'orchestrateur). Le durcissement structurel — scission de
  l'exécuteur dans un worker dédié avec un proxy étroit pour l'API — reste une option
  (chantier A 1b, non engagé).

## Ajouter une famille à la whitelist

1. **Auditer d'abord** : identifier l'appel réel (fichier:ligne) qui la justifie ; pas de
   famille ouverte « au cas où ».
2. Ajouter la variable dans `docker-compose.yml` (service `docker-socket-proxy`) avec le
   commentaire de justification.
3. Étendre la matrice de test et le garde-fou
   `hub/tests/test_compose_socket_proxy.py` si la famille était interdite.
4. Vérifier en live : endpoint autorisé → 200, un échantillon des interdits → toujours 403.

## Rollback

`git revert` du commit de bascule puis `docker compose up -d hub-api` depuis le checkout
principal : le montage direct du socket et le `group_add` reviennent tels quels. Le proxy
peut rester en place (sans consommateur) ou être retiré :

```bash
docker compose -p hub_base rm -sf docker-socket-proxy
```

## Garder-fou anti-régression

`hub/tests/test_compose_socket_proxy.py` (à exécuter côté checkout, pas dans le conteneur) :

```bash
python3 -m pytest --noconftest hub/tests/test_compose_socket_proxy.py -v
```

Il échoue si : le socket reapparaît sur `hub-api` (base ou overlay dev), `DOCKER_HOST`
disparaît, un GID docker est re-ajouté au service, ou une famille interdite est ouverte.
