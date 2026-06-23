---
name: backup-restore-ops
description: 'Run or implement Hub backup, restore, and app-backup operations: Hub snapshots (full system pg_dump + files + app backups + Docker config, encrypted S3 storage), app ZIP backups, trigger/restore apps, manage retention, encrypted exports, Vaultwarden metadata, or wire new Hub tables into backup flows.'
argument-hint: 'Action to perform, for example: create snapshot, restore snapshot, configure snapshot S3/cron/retention, receive app backup, trigger app backup, restore app backup, or inspect Vaultwarden metadata'
---

# Backup, Restore, and Snapshot Operations

Use this skill when the task involves Hub full-system snapshots or centralized backups of registered applications.

## What This Repository Uses

- **Hub snapshots** (full system): engine in [hub/app/snapshot_engine.py](../../../hub/app/snapshot_engine.py), API routes in [hub/app/routes/snapshots.py](../../../hub/app/routes/snapshots.py), config via [hub/app/snapshot_config.py](../../../hub/app/snapshot_config.py). User-facing reference: [BACKUP.md](../../../BACKUP.md).
- **Registered app backups**: routes in [hub/app/routes/app_backups.py](../../../hub/app/routes/app_backups.py), storage in [storage/backups](../../../storage/backups).
- **Hub self-backup** (for the system app "hub"): extracted to [hub/app/hub_self_backup.py](../../../hub/app/hub_self_backup.py).
- Backup metadata is persisted through `Application` and `AppBackup` in [hub/app/models.py](../../../hub/app/models.py), with schemas in [hub/app/schemas.py](../../../hub/app/schemas.py).
- Snapshot metadata is in `HubSnapshot` model (same files).
- Vaultwarden-specific full backups are documented in [vaultRestorHUB.md](../../../vaultRestorHUB.md). The Hub stores the app ZIP and metadata; the app decrypts its own secret bundle.
- Tests: [hub/tests/test_snapshots.py](../../../hub/tests/test_snapshots.py) and [hub/tests/test_app_backups_vaultwarden.py](../../../hub/tests/test_app_backups_vaultwarden.py).

## Hub Full-System Snapshots

1. Snapshots capture **everything**: PostgreSQL dump of all tables, files (secrets, backups), per-app backups (via `perform_app_backup`), and Docker compose config.
2. **Creation**: `POST /api/admin/snapshots` (async, 202 Accepted). Progress via `GET /api/admin/snapshots/in-progress`.
3. **Configuration**: `GET/PUT /api/admin/snapshots/config` — cron, retention (max count + min age), S3 endpoint/bucket/region/keys.
4. **Restore**: `POST /api/admin/snapshots/{id}/restore` with `confirm_full_restore=true`. Runs pg_restore + extracts files. Destructive — requires explicit confirmation.
5. **Bare-metal restore**: Download the snapshot, decrypt with Hub's RSA private key, extract docker-compose.yml, deploy, pg_restore, extract files.

3. When adding a Hub-owned table to the snapshot.
   - The pg_dump collector automatically includes all tables. No code change needed.
   - If you need to add file-based data, update `snapshot_collectors.py`'s `run_files_tar`.

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

- `confirm_full_restore=true` is mandatory for snapshot restore (destructive).
- `clear_existing=true`, full Vaultwarden restore, and JWT-key restore are destructive or security-sensitive; require explicit user intent before executing them.
- Keep `app_slug` as the primary continuity key for backups so deleted/recreated apps can be reassociated.
- Do not rebuild, filter, recompress, or partially parse app ZIP contents beyond metadata extraction needed by the Hub.
- Production uses PostgreSQL and filesystem mounts; automated tests use SQLite and temp paths, so call out any manual checks that cannot be covered.
- Only one snapshot can run at a time (409 Conflict if one is already running).
