---
description: "Use when editing the Hub FastAPI backend, SQLAlchemy models, JWT/auth flows, route modules, Docker discovery, app contracts, or runtime configuration."
name: "Hub Backend Guidelines"
applyTo:
  - "hub/*.py"
  - "hub/app/**/*.py"
---

# Hub Backend Guidelines

- This repository is the Hub itself: it owns authentication, users, service accounts, app registration, app behavior metadata, audit/error logs, SSE events, messages, and centralized app backups. Do not move app business data into the Hub unless the task explicitly extends a Hub-owned contract.
- Keep backend flows async end-to-end: `async def`, `AsyncSession`, SQLAlchemy `select`, explicit `await`, and `selectinload` for relationships that use `lazy="raise"`.
- Preserve the existing layering:
  - route handlers in [hub/app/routes](../../hub/app/routes)
  - auth, JWT, refresh token, and service-account helpers in [hub/app/auth.py](../../hub/app/auth.py)
  - runtime settings and key handling in [hub/app/config.py](../../hub/app/config.py)
  - database setup and SQL migration runner in [hub/app/database.py](../../hub/app/database.py)
  - ORM models in [hub/app/models.py](../../hub/app/models.py)
  - request/response schemas in [hub/app/schemas.py](../../hub/app/schemas.py)
  - Docker discovery in [hub/app/docker_scanner.py](../../hub/app/docker_scanner.py)
  - app health checks in [hub/app/health_checker.py](../../hub/app/health_checker.py)
- Reuse dependency helpers such as `get_db`, `get_current_user`, `get_current_admin`, and `get_current_entity`. M2M endpoints should require `get_current_entity` and then check for `ServiceAccount` rather than inventing a second auth path.
- JWT payloads carry `sub`, `apps`, `jti`, and `type`. Use `create_access_token(username=..., apps={...}, is_service_account=...)`; do not use template-only arguments such as `roles=[...]`.
- Preserve app-source traceability: keep `X-Slug` support, `request.state.source_app_slug`, `request.state.request_id`, audit logs, error logs, and `X-Request-ID` responses aligned when adding routes.
- When changing behavior visible to registered applications, update the relevant schema/model/route, tests in [hub/tests](../../hub/tests), and the integration docs such as [INTEGRATION.md](../../INTEGRATION.md), [how2endpoint.md](../../how2endpoint.md), [exemple-app.md](../../exemple-app.md), or [vaultRestorHUB.md](../../vaultRestorHUB.md).
- Keep startup/shutdown behavior in [hub/app/main.py](../../hub/app/main.py) non-blocking: migrations, default admin creation, token cleanup, Docker sync, log purge, backup scheduler, and discovery service-account initialization must start cleanly and cancel cleanly.
- Treat secrets carefully. Service-account credentials live under [secrets/service_accounts](../../secrets/service_accounts); never log plain client secrets, encryption keys, JWT private keys, or restored secret bundles.