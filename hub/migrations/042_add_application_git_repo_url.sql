-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 042: Ajout du champ git_repo_url pour le dashboard de santé
-- Permet le check de mise à jour et l'affichage du changelog via GitHub API.

ALTER TABLE applications ADD COLUMN IF NOT EXISTS git_repo_url VARCHAR(512);
