-- Migration 060: Reads M2M admin (phase 3) — flag can_admin_read des service accounts.
ALTER TABLE service_accounts ADD COLUMN IF NOT EXISTS can_admin_read BOOLEAN NOT NULL DEFAULT FALSE;
