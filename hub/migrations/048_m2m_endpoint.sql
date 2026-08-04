-- 048_m2m_endpoint.sql
-- Ajoute un champ m2m_endpoint pour configurer le chemin d'import M2M

ALTER TABLE tag_categories ADD COLUMN IF NOT EXISTS m2m_endpoint VARCHAR(255) DEFAULT '/api/tags/export';
