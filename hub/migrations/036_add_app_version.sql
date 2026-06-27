-- Migration 036: Version de l'app capturée à l'enregistrement.
-- Source de vérité : le fichier VERSION de l'app, transmis dans le payload
-- d'enregistrement. Remplace l'usage du dernier backup pour le badge dashboard.

ALTER TABLE applications ADD COLUMN IF NOT EXISTS version VARCHAR(50);

COMMENT ON COLUMN applications.version IS 'Version de l''app transmise dans le payload d''enregistrement (source: fichier VERSION de l''app).';
