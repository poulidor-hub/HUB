-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 009: Ajout du champ api_endpoints pour stocker la spec OpenAPI des apps
-- Date: 2026-02-18
-- Description: Permet aux applications de pousser leur spécification OpenAPI au Hub
--              lors de l'auto-registration ou via POST /api/apps/me/openapi.
--              Le Discovery Explorer utilise cette spec stockée (prioritaire sur le fetch live).

-- Ajouter la colonne api_endpoints (JSON) sur la table applications
ALTER TABLE applications ADD COLUMN IF NOT EXISTS api_endpoints JSONB DEFAULT NULL;

-- Ajouter la date de dernière mise à jour de la spec
ALTER TABLE applications ADD COLUMN IF NOT EXISTS api_endpoints_updated_at TIMESTAMP WITH TIME ZONE DEFAULT NULL;

COMMENT ON COLUMN applications.api_endpoints IS 'Spécification OpenAPI (JSON) poussée par l''application. Utilisée par le Discovery Explorer.';
COMMENT ON COLUMN applications.api_endpoints_updated_at IS 'Date de dernière mise à jour de la spécification OpenAPI.';
