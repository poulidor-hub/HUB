-- Migration: 052_create_app_usage_segments
-- Description: Tracking du temps passé par application (segments d'usage) pour les visualisations d'audit.

CREATE TABLE IF NOT EXISTS app_usage_segments (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES user_sessions(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    app_slug VARCHAR(50) NOT NULL,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMP NULL,
    end_reason VARCHAR(20) NULL
);

CREATE INDEX IF NOT EXISTS ix_app_usage_segments_session_id ON app_usage_segments(session_id);
CREATE INDEX IF NOT EXISTS ix_app_usage_segments_user_id ON app_usage_segments(user_id);
CREATE INDEX IF NOT EXISTS ix_app_usage_segments_app_slug ON app_usage_segments(app_slug);
CREATE INDEX IF NOT EXISTS ix_app_usage_user_started ON app_usage_segments(user_id, started_at);
CREATE INDEX IF NOT EXISTS ix_app_usage_app_started ON app_usage_segments(app_slug, started_at);
