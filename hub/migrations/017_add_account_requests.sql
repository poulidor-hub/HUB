-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration: Add account request system
-- Date: 2026-04-16
-- Description: Ajoute un système de demande de compte utilisateur.
--              Les utilisateurs sans compte peuvent soumettre une demande
--              qui sera validée ou refusée par un administrateur.

-- Table des demandes de compte
CREATE TABLE IF NOT EXISTS account_requests (
    id SERIAL PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    username VARCHAR(50) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    rejection_reason VARCHAR(500),
    reviewed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMP,
    polling_token VARCHAR(64) NOT NULL,
    auto_login_token VARCHAR(64),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Index pour les requêtes fréquentes
CREATE INDEX IF NOT EXISTS idx_account_requests_status ON account_requests(status);
CREATE INDEX IF NOT EXISTS idx_account_requests_username ON account_requests(username);
CREATE INDEX IF NOT EXISTS idx_account_requests_polling_token ON account_requests(polling_token);
