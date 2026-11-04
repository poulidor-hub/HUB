-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

ALTER TABLE hub_snapshots ADD COLUMN IF NOT EXISTS include_apps_code BOOLEAN NOT NULL DEFAULT TRUE;
