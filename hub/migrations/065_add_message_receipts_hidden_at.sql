-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 065 : suppression côté destinataire (console notifications unifiée)
-- Date: 2026-11-09
-- Description: Colonne `hidden_at` sur message_read_receipts — un destinataire
--              peut masquer un message de sa vue sans impact côté admin
--              (le message reste actif pour les autres cibles).

ALTER TABLE message_read_receipts ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMP NULL;
