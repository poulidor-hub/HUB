-- 067 : purge OAuth + index hot-path
-- Perf heure de pointe : cleanup_expired_tokens ne purgeait ni les codes
-- d'autorisation OIDC ni les refresh tokens OAuth (croissance non bornée) ;
-- les DELETE de purge portent sur expires_at, non indexé sur ces deux
-- tables. refresh_tokens.user_id (utilisé par revoke_all_user_tokens et les
-- vues sessions) n'était pas indexé non plus. Le partiel sur user_sessions
-- accélère les requêtes "sessions actives par user".
CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id ON refresh_tokens (user_id);
CREATE INDEX IF NOT EXISTS ix_user_sessions_user_active ON user_sessions (user_id) WHERE logout_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_oauth_refresh_tokens_expires_at ON oauth_refresh_tokens (expires_at);
CREATE INDEX IF NOT EXISTS ix_oauth_authorization_codes_expires_at ON oauth_authorization_codes (expires_at);
