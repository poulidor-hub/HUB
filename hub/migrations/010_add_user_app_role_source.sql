-- Migration 010: Ajouter la colonne source à user_app_roles
-- Permet de distinguer les rôles provenant d'un profil (source='profile')
-- de ceux ajoutés manuellement (source='manual')

ALTER TABLE user_app_roles ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'manual';

-- Les rôles existants des utilisateurs qui ont un profil sont marqués comme 'profile'
-- (les rôles existants sans profil restent 'manual')
UPDATE user_app_roles SET source = 'profile'
WHERE user_id IN (SELECT id FROM users WHERE profile_id IS NOT NULL);
