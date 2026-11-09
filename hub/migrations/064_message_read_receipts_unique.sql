-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 064: Unicité (message_id, user_id) des read receipts
-- Date: 2026-11-09
-- Description: Dédoublonne message_read_receipts puis pose un index unique.
--              Incident 09/11/2026 : l'UNIQUE de la migration 014 est un no-op
--              sur toutes les installations (init_db/create_all depuis un modèle
--              sans contrainte crée la table AVANT apply_migrations, donc le
--              CREATE TABLE IF NOT EXISTS de 014 ne s'applique jamais). Des
--              courses (read + read-all concurrents) ont créé de vrais doublons
--              → « Multiple rows were found when one or none was required » sur
--              POST /api/messages/{id}/read en boucle (pic d'erreurs Hub).
--              Même pattern que la migration 055 (user_app_roles).

-- 1. Dédoublonnage : garder la ligne la plus ancienne (premier état connu)
DELETE FROM message_read_receipts
WHERE id IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY message_id, user_id ORDER BY id
        ) AS rn
        FROM message_read_receipts
    ) dup WHERE dup.rn > 1
);

-- 2. Index unique (même nom que l'index du modèle → no-op sur installs neuves)
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_read_receipts_message_user
    ON message_read_receipts(message_id, user_id);
