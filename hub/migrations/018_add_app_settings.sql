-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 018: Table app_settings (clé-valeur) pour configuration applicative
-- Permet de stocker des paramètres modifiables à chaud par les admins

CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);

-- Valeur par défaut : les demandes de compte sont activées
INSERT INTO app_settings (key, value) VALUES ('account_requests_enabled', 'true')
ON CONFLICT (key) DO NOTHING;
