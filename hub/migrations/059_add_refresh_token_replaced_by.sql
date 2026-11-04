-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 059: Période de grâce sur la rotation des refresh tokens.
-- Stocke (temporairement) la valeur du token remplaçant sur la ligne de
-- l'ancien token révoqué : si l'ancien est re-présenté dans la fenêtre de
-- grâce (60 s, course inter-onglets), le Hub re-délivre le remplaçant au
-- lieu de renvoyer 401. Nullifié ensuite par cleanup_expired_tokens.

ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS replaced_by_token VARCHAR(255);
