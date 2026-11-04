-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 040 : Codes d'autorisation OIDC one-shot (TTL court, utilisés pour /oidc/launch + /oidc/token).
CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
    id                    SERIAL PRIMARY KEY,
    code_hash             VARCHAR(100) UNIQUE NOT NULL,
    client_id             VARCHAR(100) NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    redirect_uri          VARCHAR(500) NOT NULL,
    scope                 VARCHAR(200) NOT NULL,
    code_challenge        VARCHAR(200),
    code_challenge_method VARCHAR(10),
    nonce                 VARCHAR(100),
    expires_at            TIMESTAMP NOT NULL,
    used_at               TIMESTAMP,
    created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_codes_hash ON oauth_authorization_codes(code_hash) WHERE used_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_oauth_codes_expires ON oauth_authorization_codes(expires_at);
