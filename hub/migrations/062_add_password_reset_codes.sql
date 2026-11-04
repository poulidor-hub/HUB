-- Migration 062 (chantier D) : reset self-service par code admin.
-- Table password_reset_codes : code court 'XXXX-XXXX' généré par un admin,
-- stocké hashé (bcrypt, jamais en clair), usage unique, TTL configurable
-- (password_reset_code_ttl_minutes, défaut 15 min). Un seul code actif par
-- utilisateur : toute génération consomme (consumed_at) les actifs précédents,
-- et la vérification réussie consomme immédiatement.

CREATE TABLE IF NOT EXISTS password_reset_codes (
    id                 SERIAL PRIMARY KEY,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash          VARCHAR(255) NOT NULL,
    expires_at         TIMESTAMP NOT NULL,
    consumed_at        TIMESTAMP,
    created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_password_reset_codes_user_id
    ON password_reset_codes (user_id);
