---
name: deploy-troubleshoot
description: 'Deploy or troubleshoot the Hub itself: Docker Compose services, PostgreSQL connectivity, Hub API health, static frontend nginx proxy, SSE drops, Docker socket discovery, app auto-registration whitelist, JWT key/secrets setup, and backup storage mounts. Use for Hub deployment, container startup failures, registration diagnostics, or post-deploy health checks.'
argument-hint: 'Deployment task, for example: start Hub, debug hub-api, debug frontend proxy, fix auto-registration, inspect SSE, diagnose Docker discovery'
---

# Deploy and Troubleshoot

Use this skill when the task involves deploying this Hub repository or diagnosing runtime connectivity between the Hub, its frontend, PostgreSQL, Docker, and registered applications.

## What This Repository Uses

- Orchestration: [docker-compose.yml](../../../docker-compose.yml) with `db`, `hub-api`, and `hub-frontend` services; [docker-compose.override.yml](../../../docker-compose.override.yml) enables dev hot reload.
- Backend service: [hub/Dockerfile](../../../hub/Dockerfile), [hub/entrypoint.sh](../../../hub/entrypoint.sh), and `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- Frontend service: [frontend/Dockerfile](../../../frontend/Dockerfile), [frontend/nginx.conf](../../../frontend/nginx.conf), and static assets under [frontend/public](../../../frontend/public).
- Runtime config and keys: [hub/app/config.py](../../../hub/app/config.py), [secrets](../../../secrets), and `SECRETS_ENCRYPTION_KEY` / JWT key generation settings.
- App discovery and health: [hub/app/docker_scanner.py](../../../hub/app/docker_scanner.py), [hub/app/health_checker.py](../../../hub/app/health_checker.py), and routes in [hub/app/routes/apps.py](../../../hub/app/routes/apps.py).

## Procedure

1. Check container state and logs.
   - Start or rebuild: `docker compose up -d --build`.
   - Inspect logs: `docker compose logs -f hub-api` or `docker compose logs -f hub-frontend`.
   - Verify the API: `curl http://localhost:8000/health`.

2. Validate required environment.
   - `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `SECRETS_ENCRYPTION_KEY` are required by [docker-compose.yml](../../../docker-compose.yml).
   - `AUTO_GENERATE_KEYS` controls JWT key generation; generated keys live under [secrets](../../../secrets).
   - `HUB_HOST` controls URLs shared with applications when auto-detection is not enough.

3. Diagnose frontend/API proxy issues.
   - The browser calls relative `/api` paths; [frontend/nginx.conf](../../../frontend/nginx.conf) proxies them to `hub-api:8000`.
   - SSE uses `/api/events/stream` and requires `proxy_buffering off`, `proxy_cache off`, and long read/send timeouts.
   - This frontend has no npm build; rebuild the `hub-frontend` container after static asset changes.

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

## Common Diagnostics

- `hub-api` fails on startup: check missing required env vars, failed SQL migration, database health, JWT key generation, or secrets directory permissions.
- Auto-registration returns 403: update `APP_WHITELIST_NETWORKS` or ensure the app call originates from an allowed Docker/internal network.
- Frontend loads but API calls fail: inspect [frontend/nginx.conf](../../../frontend/nginx.conf), container DNS `hub-api`, and the `hub-api` healthcheck.
- SSE reconnects repeatedly: check the `/api/events/stream` nginx location and backend event loop logs in [hub/app/routes/events.py](../../../hub/app/routes/events.py).
- Docker scan shows nothing: verify `/var/run/docker.sock` mount and `DOCKER_GID`, then inspect [hub/app/docker_scanner.py](../../../hub/app/docker_scanner.py) filters.

## Guardrails

- Do not commit real secrets, generated private keys, or service-account secret files.
- Do not hardcode localhost into app-facing URLs; browser users and remote apps cannot use the Hub container's localhost.
- Keep backend-to-database and frontend-to-backend routing inside Docker Compose unless the deployment task explicitly changes topology.
