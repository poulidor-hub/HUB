-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 024: Suivi de progression et de durée des backups
-- Date: 2026-04-30
-- Description: Ajoute les colonnes de cycle de vie (status, started_at,
--              completed_at, duration_seconds), de progression (progress_step,
--              progress_percent) et le message d''erreur détaillé sur app_backups.
--              Permet d''exécuter les backups en tâche de fond et d''exposer la
--              progression via SSE, ainsi que la durée dans la liste de rétention.

-- Rendre storage_path nullable : la ligne est créée AVANT le téléchargement
-- du ZIP. Elle est remplie une fois le fichier stocké sur disque.
ALTER TABLE app_backups
ALTER COLUMN storage_path DROP NOT NULL;

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'success';

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS progress_step VARCHAR(40) DEFAULT 'done';

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS progress_percent SMALLINT DEFAULT 100;

ALTER TABLE app_backups
ADD COLUMN IF NOT EXISTS error_message TEXT;

-- Backfill des lignes existantes : marquer comme terminé instantanément.
UPDATE app_backups
SET started_at = COALESCE(started_at, created_at),
    completed_at = COALESCE(completed_at, created_at),
    duration_seconds = COALESCE(duration_seconds, 0),
    progress_step = COALESCE(progress_step, 'done'),
    progress_percent = COALESCE(progress_percent, 100)
WHERE started_at IS NULL OR completed_at IS NULL;

-- Index pour retrouver rapidement les backups en cours par app.
CREATE INDEX IF NOT EXISTS idx_app_backups_running
ON app_backups(app_slug)
WHERE status IN ('pending', 'running');

COMMENT ON COLUMN app_backups.status IS
'Statut du backup: pending|running|success|failed|timeout';
COMMENT ON COLUMN app_backups.started_at IS 'Début effectif du job de backup';
COMMENT ON COLUMN app_backups.completed_at IS 'Fin du job (succès ou échec)';
COMMENT ON COLUMN app_backups.duration_seconds IS
'Durée totale du backup en secondes (renseigné à la fin du job)';
COMMENT ON COLUMN app_backups.progress_step IS
'Étape courante: queued|create|downloading|storing|done';
COMMENT ON COLUMN app_backups.progress_percent IS
'Pourcentage approximatif (0-100) basé sur les jalons côté Hub';
COMMENT ON COLUMN app_backups.error_message IS
'Message d''erreur détaillé en cas d''échec (code HTTP, extrait body, URL)';
