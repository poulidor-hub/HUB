---
name: deploy-troubleshoot
description: 'Deploy or troubleshoot this Hub repository: Docker Compose services, PostgreSQL connectivity, Hub API health, static frontend nginx proxy, SSE drops, Docker socket discovery, app auto-registration whitelist, JWT keys/secrets, PKI/cert expectations, and backup storage mounts.'
argument-hint: 'Hub deployment task, for example: start Hub, debug hub-api, debug frontend proxy, fix stale frontend, inspect SSE, diagnose Docker discovery'
---

# Deploy and Troubleshoot

Use this skill when the task involves deploying this Hub repository or diagnosing runtime connectivity between the Hub API, PostgreSQL, Docker, the static frontend, and registered applications.

For stale frontend, browser cache, CSS/JS not updating, or nginx cache headers, also use the [cache-busting-deployment](../cache-busting-deployment/SKILL.md) skill.

## What This Repository Uses

- Orchestration: [docker-compose.yml](../../../docker-compose.yml) with `db`, `hub-api`, and `hub-frontend`; [docker-compose.override.yml](../../../docker-compose.override.yml) enables backend hot reload in development.
- Backend service: [hub/Dockerfile](../../../hub/Dockerfile), [hub/entrypoint.sh](../../../hub/entrypoint.sh), [hub/app/main.py](../../../hub/app/main.py), and `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- Frontend service: [frontend/Dockerfile](../../../frontend/Dockerfile), [frontend/nginx.conf](../../../frontend/nginx.conf), [frontend/docker-entrypoint.sh](../../../frontend/docker-entrypoint.sh), and static assets under [frontend/public](../../../frontend/public).
- Runtime config and keys: [hub/app/config.py](../../../hub/app/config.py), [.env.example](../../../.env.example), [secrets](../../../secrets), and `SECRETS_ENCRYPTION_KEY` / JWT key generation settings.
- App discovery and health: [hub/app/docker_scanner.py](../../../hub/app/docker_scanner.py), [hub/app/health_checker.py](../../../hub/app/health_checker.py), and routes in [hub/app/routes/apps.py](../../../hub/app/routes/apps.py).
- Health endpoints: `GET /health` and `GET /api/health` return the Hub API status from [hub/app/main.py](../../../hub/app/main.py).

## Procedure

1. Check container state and logs.
   - Start or rebuild: `docker compose up -d --build`.
   - Production-style start without the dev override: `docker compose -f docker-compose.yml up -d --build`.
   - Inspect logs: `docker compose logs -f hub-api` or `docker compose logs -f hub-frontend`.
   - Verify the API: `curl http://localhost:8000/health`.

2. Validate required environment.
   - `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `SECRETS_ENCRYPTION_KEY` are required by [docker-compose.yml](../../../docker-compose.yml).
   - `AUTO_GENERATE_KEYS` controls JWT key generation; generated keys live under [secrets](../../../secrets).
   - `HUB_HOST` controls URLs shared with applications when auto-detection is not enough.
   - `APP_WHITELIST_NETWORKS` controls which app networks may auto-register.

3. Diagnose frontend/API proxy issues.
   - The browser calls relative `/api` paths; [frontend/nginx.conf](../../../frontend/nginx.conf) proxies them to `hub-api:8000`.
   - SSE uses `/api/events/stream` and requires `proxy_buffering off`, `proxy_cache off`, and long read/send timeouts.
   - This frontend has no npm build; rebuild the `hub-frontend` container after static asset or nginx config changes.
   - For stale UI reports, verify [frontend/nginx.conf](../../../frontend/nginx.conf) and use the cache-busting skill.

4. Diagnose app auto-registration.
   - `/api/apps/register` accepts calls only from whitelisted Docker/network IPs via `APP_WHITELIST_NETWORKS` and `is_docker_network_ip` in [hub/app/routes/apps.py](../../../hub/app/routes/apps.py).
   - Auto-registered apps start as `pending`; an admin must approve them unless the route behavior is explicitly changed.
   - If URLs are wrong, check `HUB_HOST`, `custom_host`, app `api_port`, `frontend_port`, and `frontend_https_port` handling.

5. Diagnose Docker discovery.
   - The Hub reads `/var/run/docker.sock` read-only and needs the host Docker group id through `DOCKER_GID`.
   - If scanning fails, confirm the socket mount and group id before changing scanner code.

6. Verify data and backup mounts.
   - PostgreSQL data is in the `postgres_data` Docker volume.
   - App backups are stored under [storage/backups](../../../storage/backups), mounted to `/code/storage/backups`.
   - Service-account credential files are under [secrets/service_accounts](../../../secrets/service_accounts).

7. Treat HTTPS as deployment-specific unless implemented in this repo.
   - [.env.example](../../../.env.example) documents certificate variables under `certs/server/hub/`, but this repository snapshot has only [frontend/nginx.conf](../../../frontend/nginx.conf) and exposes HTTP from the frontend container.
   - If TLS is terminated by a reverse proxy, preserve `/api/` forwarding and SSE buffering/timeouts there too.
   - If TLS is added directly to `hub-frontend`, add explicit cert mounts, port mappings, nginx `listen 443 ssl`, and matching validation steps.

## Common Diagnostics

- `hub-api` fails on startup: check missing required env vars, failed SQL migration, database health, JWT key generation, or secrets directory permissions.
- Auto-registration returns 403: update `APP_WHITELIST_NETWORKS` or ensure the app call originates from an allowed Docker/internal network.
- Frontend loads but API calls fail: inspect [frontend/nginx.conf](../../../frontend/nginx.conf), container DNS `hub-api`, and the `hub-api` healthcheck.
- SSE reconnects repeatedly: check the `/api/events/stream` nginx location and backend event loop logs in [hub/app/routes/events.py](../../../hub/app/routes/events.py).
- Docker scan shows nothing: verify `/var/run/docker.sock` mount and `DOCKER_GID`, then inspect [hub/app/docker_scanner.py](../../../hub/app/docker_scanner.py) filters.
- Users see an old UI after deploy: verify `index.html` no-store headers, fixed asset no-store/no-cache headers, and bumped CSS/JS query-string URLs.

## Guardrails

- Do not commit real secrets, generated private keys, or service-account secret files.
- Do not hardcode localhost into app-facing URLs; browser users and remote apps cannot use the Hub container's localhost.
- Keep backend-to-database and frontend-to-backend routing inside Docker Compose unless the deployment task explicitly changes topology.
- Keep nginx `/api/` and SSE proxy behavior intact when changing cache or TLS rules.
