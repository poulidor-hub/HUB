-- 063 : index login_at sur user_sessions
-- Audit 08/11/2026 : purge user_sessions ajoutée (rétention 180 j) mais
-- login_at n'était pas indexé — le DELETE serait un seq scan.
CREATE INDEX IF NOT EXISTS ix_user_sessions_login_at ON user_sessions (login_at);
