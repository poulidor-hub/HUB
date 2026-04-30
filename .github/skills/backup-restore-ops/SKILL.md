---
name: backup-restore-ops
description: 'Run or implement Hub backup, restore, and app-backup operations: export/import Hub configuration, preserve service-account secrets and JWT keys, receive app ZIP backups, trigger app backups/restores, manage retention, encrypted exports, Vaultwarden metadata, or wire new Hub tables into backup flows.'
argument-hint: 'Action to perform, for example: export Hub, import Hub, receive app backup, trigger app backup, restore app backup, add table to backup, or inspect Vaultwarden metadata'
---

# Backup and Restore Operations

Use this skill when the task involves Hub environment backup/import or centralized backups of registered applications.

## What This Repository Uses

- Hub environment export/import endpoints live in [hub/app/routes/backup.py](../../../hub/app/routes/backup.py), with CLI helpers [hub/export_env.py](../../../hub/export_env.py) and [hub/import_env.py](../../../hub/import_env.py). The user-facing reference is [BACKUP.md](../../../BACKUP.md).
- Registered app backup storage and restore orchestration live in [hub/app/routes/app_backups.py](../../../hub/app/routes/app_backups.py), using [storage/backups](../../../storage/backups).
- Backup metadata is persisted through `Application` and `AppBackup` in [hub/app/models.py](../../../hub/app/models.py), with schemas in [hub/app/schemas.py](../../../hub/app/schemas.py).
- Vaultwarden-specific full backups are documented in [vaultRestorHUB.md](../../../vaultRestorHUB.md). The Hub stores the app ZIP and metadata; the app decrypts its own secret bundle.
- Tests live in [hub/tests/test_backup.py](../../../hub/tests/test_backup.py) and [hub/tests/test_app_backups_vaultwarden.py](../../../hub/tests/test_app_backups_vaultwarden.py).

## Hub Environment Backup

1. Identify whether the task is API or CLI.
   - CLI export: `docker compose exec hub-api python export_env.py -o /code/secrets/service_accounts/hub_backup.json`.
   - CLI import: `docker compose exec hub-api python import_env.py /code/secrets/service_accounts/hub_backup.json --mode update`.
   - API export/import: routes under `/api/admin/backup/*` in [hub/app/routes/backup.py](../../../hub/app/routes/backup.py).

2. Preserve security boundaries.
   - Hub backups can include bcrypt password hashes, service-account secret material, and JWT keys.
   - Never print plain service-account secrets, `SECRETS_ENCRYPTION_KEY`, or JWT private key contents in logs or responses.
   - Treat `--restore-jwt` and modes that update existing entities as operationally sensitive.

3. When adding a Hub-owned table to export/import.
   - Update [hub/app/routes/backup.py](../../../hub/app/routes/backup.py), [hub/app/schemas.py](../../../hub/app/schemas.py), and tests.
   - Keep natural keys stable where possible (`username`, `app_slug`, `client_id`, `backup_id`) so imports can upsert predictably.

## Registered App Backups

1. App-to-Hub push.
   - Apps upload ZIP backups to `POST /api/apps/{slug}/backup` with an M2M or admin token.
   - Preserve original ZIP bytes, checksum, `backup_id`, app version, table/file counters, Vaultwarden flags, and retention behavior.

2. Hub-triggered backup.
   - Admin or scheduler calls `perform_app_backup`, which obtains a short-lived service-account token with `apps={slug: "admin"}` and calls the app's `/api/admin/backup`.
   - Honor `backup_mode`, `backup_cron`, `backup_retention`, and `backup_quiesce`.

3. App restore orchestration.
   - The Hub retrieves the stored ZIP and posts it to the target app restore endpoint with the requested restore flags.
   - The Hub must not decrypt app secret bundles; it only relays opaque backup data and metadata.

4. Admin storage operations.
   - Direct unencrypted backup downloads are disabled; use encrypted export/download routes.
   - Retention deletes old physical files and database rows; test both metadata and filesystem effects when changing it.

## Guardrails

- `clear_existing=true`, full Vaultwarden restore, and JWT-key restore are destructive or security-sensitive; require explicit user intent before executing them.
- Keep `app_slug` as the primary continuity key for backups so deleted/recreated apps can be reassociated.
- Do not rebuild, filter, recompress, or partially parse app ZIP contents beyond metadata extraction needed by the Hub.
- Production uses PostgreSQL and filesystem mounts; automated tests use SQLite and temp paths, so call out any manual checks that cannot be covered.
