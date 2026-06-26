-- Migration 032: Templates de messages + envoi differe
-- Date: 2026-06-26
-- Description:
--   1. Ajout de scheduled_at sur user_messages pour l'envoi differe.
--   2. Creation de la table message_templates pour les templates reutilisables.

-- =============================================================================
-- 1. scheduled_at sur user_messages
-- =============================================================================
ALTER TABLE user_messages
    ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP NULL;

COMMENT ON COLUMN user_messages.scheduled_at IS 'Horodatage d''envoi differe. NULL = envoi immediat (comportement existant).';

-- =============================================================================
-- 2. Table message_templates
-- =============================================================================
CREATE TABLE IF NOT EXISTS message_templates (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    title_template VARCHAR(500),
    body_template TEXT,
    default_priority VARCHAR(20) NOT NULL DEFAULT 'info',
    default_target_type VARCHAR(20) NOT NULL DEFAULT 'broadcast',
    default_expiry_hours INTEGER,
    default_action_url VARCHAR(500),
    default_action_label VARCHAR(100),
    created_by VARCHAR(150),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE message_templates IS 'Templates de messages reutilisables (geres par les admins).';
COMMENT ON COLUMN message_templates.name IS 'Nom unique du template (ex: Maintenance planifiee).';
COMMENT ON COLUMN message_templates.default_expiry_hours IS 'Duree d''expiration par defaut en heures. NULL = pas de defaut.';
