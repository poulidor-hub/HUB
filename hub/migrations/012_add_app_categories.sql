-- Migration 012: Ajout des catégories d'applications
-- Date: 2026-04-03
-- Description: Ajoute un champ category sur les applications pour permettre
--              le filtrage et le regroupement visuel sur le dashboard.

-- =============================================================================
-- Colonne category sur applications
-- =============================================================================
ALTER TABLE applications ADD COLUMN IF NOT EXISTS category VARCHAR(100);

CREATE INDEX IF NOT EXISTS idx_applications_category ON applications(category);

COMMENT ON COLUMN applications.category IS 'Catégorie de l''application (ex: RH, SI, Communication, Métier, Outils, API)';
