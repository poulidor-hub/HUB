-- Migration 058: Accès atelier par utilisateur (visibilité des apps -ws)
-- L'activation référence TOUJOURS l'app de base (jamais la -ws) :
-- elle survit à la suppression/recréation de l'atelier.

CREATE TABLE IF NOT EXISTS user_workshop_access (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    app_id     INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, app_id)
);

CREATE INDEX IF NOT EXISTS ix_user_workshop_access_user ON user_workshop_access (user_id);
CREATE INDEX IF NOT EXISTS ix_user_workshop_access_app  ON user_workshop_access (app_id);
