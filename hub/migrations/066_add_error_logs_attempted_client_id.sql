-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 066 : traçabilité du client_id tenté sur échec d'auth M2M
-- Date: 2026-11-14
-- Description: Colonne `attempted_client_id` sur error_logs — incident 09/11/2026 :
--              un SA désactivé a été martelé en boucle (186×403 sur
--              POST /api/auth/token) sans identification possible depuis
--              error_logs (service_account_id NULL, body non stocké).
--              On trace l'identifiant PUBLIC tenté (jamais le client_secret).

ALTER TABLE error_logs ADD COLUMN IF NOT EXISTS attempted_client_id VARCHAR(255) NULL;
