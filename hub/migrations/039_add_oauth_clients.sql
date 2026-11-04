-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 039 : Clients OIDC pour le bridge OIDC provider.
-- Permet aux apps externes (Vikunja, Nextcloud, Grafana...) de consommer le SSO HUB via OIDC.
CREATE TABLE IF NOT EXISTS oauth_clients (
    id                    SERIAL PRIMARY KEY,
    client_id             VARCHAR(100) UNIQUE NOT NULL,
    client_secret_hash    VARCHAR(255) NOT NULL,
    name                  VARCHAR(100) NOT NULL,
    redirect_uris         JSON NOT NULL DEFAULT '[]',
    scopes                JSON NOT NULL DEFAULT '["openid","profile","email"]',
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    auto_approve          BOOLEAN NOT NULL DEFAULT TRUE,
    backchannel_logout_uri VARCHAR(500),
    app_id                INTEGER REFERENCES applications(id) ON DELETE SET NULL,
    created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_clients_client_id ON oauth_clients(client_id);
