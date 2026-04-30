---
description: "Use when writing or updating Hub SQL migrations, schema changes, or hub/migrations files. Covers filename format, fail-fast runner behavior, and model/schema synchronization."
name: "Hub SQL Migration Guidelines"
applyTo:
  - "hub/migrations/*.sql"
  - "hub/migrations/**/*.sql"
---

# Hub SQL Migration Guidelines

- Name migration files with the existing numeric prefix format used by the runner in [hub/app/database.py](../../hub/app/database.py): `NNN_description.sql`, for example `024_add_application_behavior_flags.sql`.
- Never reuse a numeric prefix. The runner detects duplicate versions and raises `MigrationError` before serving the API.
- The runner records the numeric prefix in `schema_migrations`, applies one transaction per migration, and fails startup on any SQL error. Do not rely on failing statements being skipped.
- Keep every SQL statement terminated by `;`. The runner strips `--` comment lines and splits file content on semicolons.
- Prefer additive, forward-only changes that match the startup flow: migrations run automatically during Hub startup via [hub/app/main.py](../../hub/app/main.py) and [hub/app/database.py](../../hub/app/database.py).
- Write migrations with production PostgreSQL in mind, but keep them understandable for the SQLite-backed tests when possible. If behavior is PostgreSQL-only, add or update focused runner tests and note the manual production check.
- If a schema change affects persisted Hub behavior, update the matching Python and UI layers in the same task when relevant:
  - [hub/app/models.py](../../hub/app/models.py)
  - [hub/app/schemas.py](../../hub/app/schemas.py)
  - [hub/app/routes](../../hub/app/routes)
  - [hub/tests](../../hub/tests)
  - [frontend/public](../../frontend/public)
  - [INTEGRATION.md](../../INTEGRATION.md) or another integration spec
- If renaming or correcting an already-shipped migration, preserve compatibility with existing databases through [hub/app/database.py](../../hub/app/database.py) migration aliases rather than pretending the old version never existed.
