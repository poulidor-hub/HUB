-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 011: Multi-profils — permettre plusieurs profils par utilisateur
-- Date: 2026-04-03
-- Description: Remplace la relation users.profile_id (1:N) par une table de jonction
--              user_profiles (N:N). Quand plusieurs profils donnent des droits différents
--              sur une même application, le rôle le plus élevé est retenu.
--              Hiérarchie : admin > manager > user > viewer

-- =============================================================================
-- Table user_profiles : association N:N users <-> profiles
-- =============================================================================
CREATE TABLE IF NOT EXISTS user_profiles (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    assigned_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE(user_id, profile_id)
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_profile_id ON user_profiles(profile_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_profiles_unique_user_profile ON user_profiles(user_id, profile_id);

COMMENT ON TABLE user_profiles IS 'Association N:N entre utilisateurs et profils (multi-profils)';
COMMENT ON COLUMN user_profiles.assigned_by IS 'Admin ayant assigné ce profil';

-- =============================================================================
-- Migrer les données existantes depuis users.profile_id
-- =============================================================================
INSERT INTO user_profiles (user_id, profile_id)
SELECT id, profile_id FROM users WHERE profile_id IS NOT NULL
ON CONFLICT (user_id, profile_id) DO NOTHING;

-- =============================================================================
-- Adapter la colonne source de user_app_roles pour tracer l'origine précise
-- Passer de "profile" à "profile:<profile_id>" pour les rôles existants
-- =============================================================================
UPDATE user_app_roles
SET source = 'profile:' || u.profile_id::TEXT
FROM users u
WHERE user_app_roles.user_id = u.id
  AND user_app_roles.source = 'profile'
  AND u.profile_id IS NOT NULL;

-- Augmenter la taille de la colonne source pour accueillir "profile:<id>"
ALTER TABLE user_app_roles ALTER COLUMN source TYPE VARCHAR(50);

-- =============================================================================
-- Supprimer l'ancienne colonne profile_id de users
-- =============================================================================
ALTER TABLE users DROP COLUMN IF EXISTS profile_id;
