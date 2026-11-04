-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 021: Cycle de vie des messages + tracking d'engagement
-- Date: 2026-04-21
-- Description:
--   Refonte du Notification Center.
--   1. Ajout d'un statut explicite (active/archived) pour soft-delete admin.
--   2. Ajout de compteurs agrégés cachés sur user_messages (perf admin).
--   3. Ajout de delivered_at/clicked_at sur les receipts pour tracking d'engagement.
--   Toutes les colonnes sont optionnelles avec valeurs par défaut → rétro-compat M2M.

-- =============================================================================
-- 1. Cycle de vie sur user_messages
-- =============================================================================
ALTER TABLE user_messages
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active';
ALTER TABLE user_messages
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP NULL;

CREATE INDEX IF NOT EXISTS idx_user_messages_status_created
    ON user_messages(status, created_at DESC);

COMMENT ON COLUMN user_messages.status IS 'Cycle de vie : active | archived (soft-delete admin). expired est calculé.';
COMMENT ON COLUMN user_messages.archived_at IS 'Horodatage de l''archivage (soft-delete admin).';

-- =============================================================================
-- 2. Compteurs agrégés cachés sur user_messages
-- =============================================================================
ALTER TABLE user_messages
    ADD COLUMN IF NOT EXISTS delivered_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE user_messages
    ADD COLUMN IF NOT EXISTS read_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE user_messages
    ADD COLUMN IF NOT EXISTS click_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE user_messages
    ADD COLUMN IF NOT EXISTS total_targets INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN user_messages.delivered_count IS 'Compteur agrégé : nb de receipts avec delivered_at non NULL.';
COMMENT ON COLUMN user_messages.read_count IS 'Compteur agrégé : nb de receipts (= nb de lectures).';
COMMENT ON COLUMN user_messages.click_count IS 'Compteur agrégé : nb de receipts avec clicked_at non NULL.';
COMMENT ON COLUMN user_messages.total_targets IS 'Snapshot à l''envoi : nb de destinataires potentiels (1 pour user, count(users) pour broadcast).';

-- =============================================================================
-- 3. Tracking d'engagement sur message_read_receipts
-- =============================================================================
ALTER TABLE message_read_receipts
    ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMP NULL;
ALTER TABLE message_read_receipts
    ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMP NULL;

COMMENT ON COLUMN message_read_receipts.delivered_at IS 'Horodatage de la livraison (push SSE ou GET /me).';
COMMENT ON COLUMN message_read_receipts.clicked_at IS 'Horodatage du clic sur action_url.';

-- =============================================================================
-- 4. Backfill des compteurs et de total_targets pour l'existant
-- =============================================================================

-- 4a. read_count et delivered_count = nombre de receipts existants
--     (delivered_at backfill = read_at car on n'a pas mieux pour l'historique)
UPDATE message_read_receipts
SET delivered_at = read_at
WHERE delivered_at IS NULL;

UPDATE user_messages um
SET read_count = sub.cnt,
    delivered_count = sub.cnt
FROM (
    SELECT message_id, COUNT(*) AS cnt
    FROM message_read_receipts
    GROUP BY message_id
) sub
WHERE um.id = sub.message_id
  AND um.read_count = 0;

-- 4b. total_targets : 1 pour les messages user, COUNT(users) pour les broadcasts
UPDATE user_messages
SET total_targets = 1
WHERE target_type = 'user' AND total_targets = 0;

UPDATE user_messages
SET total_targets = (SELECT COUNT(*) FROM users)
WHERE target_type = 'broadcast' AND total_targets = 0;
