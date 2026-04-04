-- Migration 013: Ajout des favoris utilisateur
-- Date: 2026-04-03
-- Description: Permet aux utilisateurs d'épingler leurs applications favorites
--              en haut du dashboard. Persisté côté serveur (multi-device).

-- =============================================================================
-- Table user_favorites : applications favorites par utilisateur
-- =============================================================================
CREATE TABLE IF NOT EXISTS user_favorites (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    app_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, app_id)
);

CREATE INDEX IF NOT EXISTS idx_user_favorites_user_id ON user_favorites(user_id);

COMMENT ON TABLE user_favorites IS 'Applications favorites épinglées par chaque utilisateur';
