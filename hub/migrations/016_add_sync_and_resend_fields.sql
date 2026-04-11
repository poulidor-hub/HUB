-- Migration 016: Ajout des champs pour la synchronisation NotifCenter et le renvoi de messages
-- Date: 2026-04-11
-- Description:
--   1. target_username : permet au NotifCenter de filtrer par username (vs user_id)
--   2. is_resend / original_message_id : traçabilité des messages renvoyés
--   Ces colonnes sont nullables/optionnelles → aucun impact sur les endpoints existants.

-- =============================================================================
-- 1. target_username sur user_messages (fix critique pour sync NotifCenter)
-- =============================================================================
ALTER TABLE user_messages ADD COLUMN IF NOT EXISTS target_username VARCHAR(150);

-- Backfill : résoudre les usernames pour les messages existants
UPDATE user_messages
SET target_username = u.username
FROM users u
WHERE u.id = user_messages.target_user_id
  AND user_messages.target_username IS NULL;

CREATE INDEX IF NOT EXISTS idx_user_messages_target_username ON user_messages(target_username);

-- =============================================================================
-- 2. Champs de renvoi (resend) sur user_messages
-- =============================================================================
ALTER TABLE user_messages ADD COLUMN IF NOT EXISTS is_resend BOOLEAN DEFAULT FALSE;
ALTER TABLE user_messages ADD COLUMN IF NOT EXISTS original_message_id INTEGER REFERENCES user_messages(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_user_messages_original_message_id ON user_messages(original_message_id);

COMMENT ON COLUMN user_messages.target_username IS 'Username du destinataire (résolu depuis target_user_id, pour sync NotifCenter)';
COMMENT ON COLUMN user_messages.is_resend IS 'True si ce message est un renvoi d''un message antérieur';
COMMENT ON COLUMN user_messages.original_message_id IS 'ID du message original si is_resend=true';
