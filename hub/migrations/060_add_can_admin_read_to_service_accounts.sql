-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 060: Reads M2M admin (phase 3) — flag can_admin_read des service accounts.
ALTER TABLE service_accounts ADD COLUMN IF NOT EXISTS can_admin_read BOOLEAN NOT NULL DEFAULT FALSE;
