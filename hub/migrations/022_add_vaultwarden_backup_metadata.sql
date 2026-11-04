-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 022: Metadata de backups Vaultwarden complets
-- Date: 2026-04-29
-- Description: Ajoute les indicateurs et compteurs nécessaires pour stocker
--              les backups Vaultwarden complets comme archives opaques.

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS includes_vaultwarden_snapshot BOOLEAN DEFAULT FALSE;

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS includes_secret_bundle BOOLEAN DEFAULT FALSE;

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS restore_requires_offline BOOLEAN DEFAULT FALSE;

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS format_version VARCHAR(20);

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS total_tables INTEGER DEFAULT 0;

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS total_rows INTEGER DEFAULT 0;

COMMENT ON COLUMN app_backups.includes_vaultwarden_snapshot IS 'Indique que le ZIP contient un snapshot complet Vaultwarden';
COMMENT ON COLUMN app_backups.includes_secret_bundle IS 'Indique que le ZIP contient un bundle de secrets chiffré';
COMMENT ON COLUMN app_backups.restore_requires_offline IS 'Indique que la restauration complète doit être faite app cible hors ligne';
COMMENT ON COLUMN app_backups.format_version IS 'Version du format de backup envoyée par l application';
COMMENT ON COLUMN app_backups.total_tables IS 'Nombre de tables exportées par l application';
COMMENT ON COLUMN app_backups.total_rows IS 'Nombre total de lignes exportées par l application';
