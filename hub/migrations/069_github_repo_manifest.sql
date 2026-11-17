-- 069 : certificat hub-app.yaml (spec 2026-11-16)
-- manifest_valid : TRUE = certifié, FALSE = invalidé (manifest_reason),
-- NULL = inconnu (jamais vérifié / GitHub raw injoignable).
ALTER TABLE github_repo_cache ADD COLUMN IF NOT EXISTS manifest_valid BOOLEAN;
ALTER TABLE github_repo_cache ADD COLUMN IF NOT EXISTS manifest_checked_at TIMESTAMPTZ;
ALTER TABLE github_repo_cache ADD COLUMN IF NOT EXISTS manifest_reason VARCHAR(64);
ALTER TABLE github_repo_cache ADD COLUMN IF NOT EXISTS manifest_name VARCHAR(255);
ALTER TABLE github_repo_cache ADD COLUMN IF NOT EXISTS manifest_slug VARCHAR(255);
