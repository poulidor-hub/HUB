-- Migration 008: Ajout du système de profils (templates de rôles)
-- Date: 2026-02-14
-- Description: Introduit un concept de "profil" (ex: Cadre, Agent, Admin SI)
--              qui regroupe un ensemble d'applications + rôles assignable en un clic.
--              Un utilisateur peut avoir 0 ou 1 profil assigné.
--              Les user_app_roles restent la source de vérité pour les accès effectifs.

-- =============================================================================
-- Table profiles : templates de rôles réutilisables
-- =============================================================================
CREATE TABLE IF NOT EXISTS profiles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    color VARCHAR(7) DEFAULT '#6c757d',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

COMMENT ON TABLE profiles IS 'Profils-types (templates de rôles) assignables aux utilisateurs';
COMMENT ON COLUMN profiles.name IS 'Nom du profil (ex: Cadre, Agent, Admin SI)';
COMMENT ON COLUMN profiles.description IS 'Description du profil et de ses droits';
COMMENT ON COLUMN profiles.color IS 'Couleur hex pour le badge (ex: #0d6efd)';

-- =============================================================================
-- Table profile_app_roles : apps + rôles inclus dans chaque profil
-- =============================================================================
CREATE TABLE IF NOT EXISTS profile_app_roles (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    app_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    UNIQUE(profile_id, app_id)
);

COMMENT ON TABLE profile_app_roles IS 'Applications et rôles inclus dans chaque profil';
COMMENT ON COLUMN profile_app_roles.role IS 'Rôle sur l''application (admin, manager, user, viewer)';

CREATE INDEX IF NOT EXISTS idx_profile_app_roles_profile_id ON profile_app_roles(profile_id);
CREATE INDEX IF NOT EXISTS idx_profile_app_roles_app_id ON profile_app_roles(app_id);

-- =============================================================================
-- Colonne profile_id sur users : profil assigné (0 ou 1)
-- =============================================================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_users_profile_id ON users(profile_id);

COMMENT ON COLUMN users.profile_id IS 'Profil assigné (template de rôles). NULL = pas de profil.';
