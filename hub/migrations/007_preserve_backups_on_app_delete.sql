-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 007: Conserver les sauvegardes quand une application est supprimée
-- Date: 2026-01-07
-- Description: Modifie la contrainte de clé étrangère app_id dans app_backups
--              pour utiliser SET NULL au lieu de CASCADE.
--              Cela permet de conserver les sauvegardes même si l'application est supprimée,
--              afin qu'une application puisse récupérer ses sauvegardes si elle est
--              réinstallée sur un autre serveur ou recréée avec le même slug.

-- =============================================================================
-- Modification de la contrainte FK sur app_backups.app_id
-- =============================================================================

-- 1. Supprimer la contrainte existante (CASCADE)
ALTER TABLE app_backups 
DROP CONSTRAINT IF EXISTS app_backups_app_id_fkey;

-- 2. Permettre NULL sur app_id (si ce n'était pas déjà le cas)
ALTER TABLE app_backups 
ALTER COLUMN app_id DROP NOT NULL;

-- 3. Recréer la contrainte avec SET NULL
ALTER TABLE app_backups 
ADD CONSTRAINT app_backups_app_id_fkey 
FOREIGN KEY (app_id) 
REFERENCES applications(id) 
ON DELETE SET NULL;

-- =============================================================================
-- Note: Les sauvegardes sont maintenant identifiées principalement par app_slug
-- qui reste toujours renseigné. Si une application est supprimée puis recréée
-- avec le même slug, elle pourra retrouver ses anciennes sauvegardes.
-- =============================================================================
