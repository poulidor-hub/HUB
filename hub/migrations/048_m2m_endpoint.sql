-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- 048_m2m_endpoint.sql
-- Ajoute un champ m2m_endpoint pour configurer le chemin d'import M2M

ALTER TABLE tag_categories ADD COLUMN IF NOT EXISTS m2m_endpoint VARCHAR(255) DEFAULT '/api/tags/export';
