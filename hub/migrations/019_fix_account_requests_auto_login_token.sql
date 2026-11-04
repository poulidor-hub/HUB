-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration: Fix account_requests — add auto_login_token if missing
-- Date: 2026-04-16
-- Description: Ajoute la colonne auto_login_token à account_requests si elle
--              n'existe pas déjà. Corrige le cas où la table avait été créée
--              par SQLAlchemy create_all() avant que la migration 017 ne soit
--              ajoutée (CREATE TABLE IF NOT EXISTS était alors un no-op).

ALTER TABLE account_requests ADD COLUMN IF NOT EXISTS auto_login_token VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_account_requests_auto_login_token ON account_requests(auto_login_token);
