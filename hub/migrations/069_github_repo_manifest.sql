-- 069 : certificat hub-app.yaml (spec 2026-11-16)
-- manifest_valid : TRUE = certifié, FALSE = invalidé (manifest_reason),
-- NULL = inconnu (jamais vérifié / GitHub raw injoignable).
-- manifest_checked_at : TIMESTAMP naïf UTC, aligné sur les colonnes sœurs de
-- 044 (updated_at_github, fetched_at) et sur le modèle DateTime + utcnow().
ALTER TABLE github_repo_cache ADD COLUMN IF NOT EXISTS manifest_valid BOOLEAN;
ALTER TABLE github_repo_cache ADD COLUMN IF NOT EXISTS manifest_checked_at TIMESTAMP;
ALTER TABLE github_repo_cache ADD COLUMN IF NOT EXISTS manifest_reason VARCHAR(64);
ALTER TABLE github_repo_cache ADD COLUMN IF NOT EXISTS manifest_name VARCHAR(255);
ALTER TABLE github_repo_cache ADD COLUMN IF NOT EXISTS manifest_slug VARCHAR(255);
