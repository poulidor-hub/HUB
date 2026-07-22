-- Migration 041 : Refresh tokens OIDC persistants et révocables (mirroir du mécanisme apps hub).
CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    id            SERIAL PRIMARY KEY,
    token_hash    VARCHAR(100) UNIQUE NOT NULL,
    client_id     VARCHAR(100) NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope         VARCHAR(200) NOT NULL,
    expires_at    TIMESTAMP,
    revoked_at    TIMESTAMP,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_hash ON oauth_refresh_tokens(token_hash) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_user ON oauth_refresh_tokens(user_id);
