---
description: "Use when writing or updating Hub pytest suites, async fixtures, auth tokens, API tests, M2M tests, migration tests, SSE tests, or backup tests."
name: "Hub Backend Test Guidelines"
applyTo:
  - "hub/tests/**/*.py"
  - "test_remote_app_m2m.py"
---

# Hub Backend Test Guidelines

- Reuse the fixtures and helpers in [hub/tests/conftest.py](../../hub/tests/conftest.py) before introducing new ones: `client`, `async_session`, `async_engine`, `test_user`, `admin_user`, `test_app`, `service_account`, `get_auth_header`, and `create_test_user_with_token` cover the common cases.
- Keep API tests async and aligned with the existing stack: `AsyncClient`, `ASGITransport`, pytest async fixtures, and dependency overrides for `get_db`.
- The suite uses in-memory SQLite while production uses PostgreSQL. If a change depends on PostgreSQL-specific SQL, Docker, filesystem mounts, or container networking, add the narrowest automated coverage possible and call out the manual verification path.
- Generate test JWTs with `create_access_token(username=..., apps={...}, is_service_account=...)`. For M2M tests, set `apps={app_slug: role}` and pass `X-Slug` when the route resolves the source application from headers.
- Group tests by Hub domain and endpoint family, following existing files such as [hub/tests/test_api_auth.py](../../hub/tests/test_api_auth.py), [hub/tests/test_api_apps.py](../../hub/tests/test_api_apps.py), [hub/tests/test_discovery.py](../../hub/tests/test_discovery.py), [hub/tests/test_messages_lifecycle.py](../../hub/tests/test_messages_lifecycle.py), and [hub/tests/test_app_backups_vaultwarden.py](../../hub/tests/test_app_backups_vaultwarden.py).
- For relationship-heavy assertions, explicitly load relationships with `selectinload`; many ORM relationships use `lazy="raise"` to catch accidental async lazy loads.
- Cover app behavior contracts with success, auth/authorization failures, validation failures, and observable side effects such as audit logs, SSE notifications, backup metadata, or OpenAPI filtering when relevant.
- Preferred verification commands are `cd hub && pytest tests -v` locally, or `docker compose exec hub-api pytest tests -v` inside the running container. Use `--cov=app --cov-report=html` when coverage is useful.