-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 014: Ajout du système de messagerie utilisateur
-- Date: 2026-04-04
-- Description: Permet aux admins et aux applications (via M2M) d'envoyer des messages
--              ciblés ou globaux aux utilisateurs, avec accusé de lecture et expiration.

-- =============================================================================
-- Table user_messages : messages envoyés aux utilisateurs
-- =============================================================================
CREATE TABLE IF NOT EXISTS user_messages (
    id SERIAL PRIMARY KEY,
    
    -- Expéditeur
    sender_type VARCHAR(20) NOT NULL,           -- 'admin', 'system', 'app'
    sender_id INTEGER,                          -- user_id admin OU service_account_id
    sender_app_slug VARCHAR(50),                -- slug de l'app source (M2M)
    
    -- Destinataire
    target_type VARCHAR(20) NOT NULL,           -- 'user', 'broadcast'
    target_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    
    -- Contenu
    priority VARCHAR(20) NOT NULL DEFAULT 'info',  -- 'info', 'warning', 'urgent'
    title VARCHAR(200) NOT NULL,
    body TEXT,
    action_url VARCHAR(500),                    -- lien cliquable
    action_label VARCHAR(100),                  -- texte du bouton d'action
    
    -- Expiration
    expires_at TIMESTAMP WITH TIME ZONE,        -- NULL = pas d'expiration
    
    -- Horodatage
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_messages_target_user_id ON user_messages(target_user_id);
CREATE INDEX IF NOT EXISTS idx_user_messages_sender_app_slug ON user_messages(sender_app_slug);
CREATE INDEX IF NOT EXISTS idx_user_messages_created_at ON user_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_user_messages_priority ON user_messages(priority);
CREATE INDEX IF NOT EXISTS idx_user_messages_target_type ON user_messages(target_type);

COMMENT ON TABLE user_messages IS 'Messages envoyés aux utilisateurs par les admins, le système ou les applications (M2M)';

-- =============================================================================
-- Table message_read_receipts : accusés de lecture
-- =============================================================================
CREATE TABLE IF NOT EXISTS message_read_receipts (
    id SERIAL PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES user_messages(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    read_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(message_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_message_read_receipts_message_id ON message_read_receipts(message_id);
CREATE INDEX IF NOT EXISTS idx_message_read_receipts_user_id ON message_read_receipts(user_id);

COMMENT ON TABLE message_read_receipts IS 'Accusés de lecture des messages par les utilisateurs';
