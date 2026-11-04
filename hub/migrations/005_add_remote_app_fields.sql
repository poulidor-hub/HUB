-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 005: Ajout des champs pour les applications distantes
-- Date: 2026-01-06
-- Description: Permet aux applications hébergées sur des serveurs distants (réseau local)
--              de s'enregistrer sur le HUB avec leur propre host/IP

-- Ajouter le champ is_remote pour identifier les apps sur des serveurs distants
ALTER TABLE applications ADD COLUMN IF NOT EXISTS is_remote BOOLEAN DEFAULT FALSE;

-- Ajouter le champ registered_from_ip pour tracer l'IP source lors de l'auto-enregistrement
ALTER TABLE applications ADD COLUMN IF NOT EXISTS registered_from_ip VARCHAR(45);

-- Commentaires sur les colonnes
COMMENT ON COLUMN applications.is_remote IS 'True si l''application est hébergée sur un serveur distant (pas le même que le HUB)';
COMMENT ON COLUMN applications.registered_from_ip IS 'Adresse IP source lors de l''auto-enregistrement de l''application';

-- Index pour faciliter le filtrage des apps distantes
CREATE INDEX IF NOT EXISTS idx_applications_is_remote ON applications(is_remote);
