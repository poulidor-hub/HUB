---
name: release-sync
description: 'Prepare or verify a Hub release by syncing VERSION with INTEGRATION.md and CHANGELOG.md through sync_version.py, checking changelog coverage, and validating the standard Hub Docker/pytest commands. Use for version bump, release preparation, changelog sync, or version consistency checks.'
argument-hint: 'Requested release action, for example: check consistency, bump patch, bump minor, sync current version, or prepare release 3.8.0'
---

# Release Sync

Use this skill when the task is about preparing a Hub release, bumping a version, synchronizing versioned docs, or checking whether the repository is release-ready.

## What This Repository Uses

- [VERSION](../../../VERSION) is the source of truth.
- The automation entry point is [sync_version.py](../../../sync_version.py).
- [INTEGRATION.md](../../../INTEGRATION.md) exposes the integration guide version to applications.
- [CHANGELOG.md](../../../CHANGELOG.md) must contain the current release entry.
- [hub/app/routes/docs.py](../../../hub/app/routes/docs.py) serves integration docs, changelog, and version data through the API.

## Procedure

1. Determine the requested release action.
   - For validation only, use the consistency path.
   - For a bump, decide whether it is `patch`, `minor`, or `major`.
   - This script does not support setting an arbitrary explicit version; edit [VERSION](../../../VERSION) only when the user explicitly asks, then run sync.

2. Verify the current state before changing files.
   - Inspect [VERSION](../../../VERSION).
   - Check whether [CHANGELOG.md](../../../CHANGELOG.md) already contains the target version.
   - Review uncommitted changes before running synchronization.

3. Use the repository's supported automation.
   - Show current version: `python sync_version.py show`.
   - Validate consistency: `python sync_version.py check`.
   - Synchronize current version: `python sync_version.py sync`.
   - Bump release: `python sync_version.py bump patch`, `python sync_version.py bump minor`, or `python sync_version.py bump major`.

4. Confirm the synchronized file set after the script runs.
   - [VERSION](../../../VERSION)
   - [INTEGRATION.md](../../../INTEGRATION.md)
   - [CHANGELOG.md](../../../CHANGELOG.md)

5. Verify release notes and runtime follow-through.
   - Complete any automatically created CHANGELOG entry before calling the release ready.
   - Restart or rebuild containers when mounted docs/version data must be refreshed: `docker compose restart hub-api` or `docker compose up -d --build` depending on the change.

6. Prefer repo-native verification.
   - Backend verification: `cd hub && pytest tests -v` or `docker compose exec hub-api pytest tests -v`.
   - Frontend static asset verification: `docker compose up -d --build hub-frontend` and check the UI in the browser.

## Guardrails

- Do not use template commands such as `make sync-env`, `npm run build`, or `scripts/bump_version.py`; they are not part of this repository.
- Treat a missing changelog entry as incomplete release preparation, even if version synchronization succeeded.
- Do not commit generated secrets, backup files, or environment-specific runtime artifacts as part of release sync.
