-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration: Add TOTP authenticator columns to users table
-- Date: 2026-06-27
-- Description: Permet l'enrôlement d'un authentificateur TOTP pour la
-- récupération de mot de passe en self-service.

ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_confirmed_at TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_last_counter BIGINT;
