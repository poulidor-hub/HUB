---
description: "Add a Hub FastAPI endpoint with the repository's route, schema, auth, app-contract, and pytest patterns. Use when implementing a new API endpoint, CRUD route, M2M route, admin operation, or protected Hub behavior."
name: "Add Hub Backend Endpoint"
argument-hint: "Describe the endpoint: method, path, behavior, auth entity, request body, response shape, side effects, and whether models/tests/docs must change"
agent: "agent"
---

Implement the requested backend endpoint for this Hub repository.

Follow these repository-specific rules:

- Start from the existing Hub backend structure before editing:
  - routes live in [hub/app/routes](../../hub/app/routes)
  - schemas live in [hub/app/schemas.py](../../hub/app/schemas.py)
  - models live in [hub/app/models.py](../../hub/app/models.py)
  - auth helpers live in [hub/app/auth.py](../../hub/app/auth.py)
  - database and migrations live in [hub/app/database.py](../../hub/app/database.py) and [hub/migrations](../../hub/migrations)
  - route registration happens in [hub/app/main.py](../../hub/app/main.py)
- Reuse dependency helpers such as `get_db`, `get_current_user`, `get_current_admin`, and `get_current_entity`. For M2M endpoints, require a `ServiceAccount` from `get_current_entity` and validate app access with the app slug.
- Match the async FastAPI + SQLAlchemy style used in existing route modules such as [hub/app/routes/apps.py](../../hub/app/routes/apps.py), [hub/app/routes/messages.py](../../hub/app/routes/messages.py), [hub/app/routes/discovery.py](../../hub/app/routes/discovery.py), and [hub/app/routes/app_backups.py](../../hub/app/routes/app_backups.py).
- Preserve Hub-wide side effects when relevant: audit logging, error traceability, SSE notifications, app-role changes embedded in future JWTs, backup metadata, and integration docs.
- Add or update pytest coverage in [hub/tests](../../hub/tests), following patterns from [hub/tests/conftest.py](../../hub/tests/conftest.py) and the test module that matches the endpoint family.
- If the endpoint changes how third-party applications integrate with the Hub, update [INTEGRATION.md](../../INTEGRATION.md), [how2endpoint.md](../../how2endpoint.md), [exemple-app.md](../../exemple-app.md), or [vaultRestorHUB.md](../../vaultRestorHUB.md) as appropriate.
- If the endpoint requires a schema or persistence change, update the minimum necessary combination of:
  - [hub/app/models.py](../../hub/app/models.py)
  - [hub/app/schemas.py](../../hub/app/schemas.py)
  - [hub/migrations](../../hub/migrations)
  - [frontend/public](../../frontend/public)

Execution checklist:

1. Read the relevant route module and adjacent patterns before writing code.
2. Implement the endpoint in the most appropriate existing route module, or create a new route module only if there is no suitable home.
3. Register any new router in [hub/app/main.py](../../hub/app/main.py).
4. Add focused tests for success, auth failures, validation failures, and not-found or conflict cases when relevant.
5. Run the smallest useful validation, usually `cd hub && pytest tests/<matching_test_file>.py -v`, and report what was verified.

Keep the change minimal and consistent with the Hub's existing conventions.