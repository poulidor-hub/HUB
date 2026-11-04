-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- 049_user_tag_seen.sql
-- Suivi des catégories de tags déjà présentées à un utilisateur (popup d'onboarding)

CREATE TABLE IF NOT EXISTS user_tag_seen (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES tag_categories(id) ON DELETE CASCADE,
    seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_user_tag_seen_user ON user_tag_seen(user_id);
