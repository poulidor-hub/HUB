-- Migration 042: Ajout du champ git_repo_url pour le dashboard de santé
-- Permet le check de mise à jour et l'affichage du changelog via GitHub API.

ALTER TABLE applications ADD COLUMN git_repo_url VARCHAR(512);
