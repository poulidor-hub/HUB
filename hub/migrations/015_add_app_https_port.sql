-- Migration 015: Ajouter le port HTTPS du frontend aux applications
-- Permet au Hub de construire des URLs HTTPS directes, évitant
-- la redirection HTTP→HTTPS qui provoque un flash visuel désagréable
-- et expose le JWT en clair sur HTTP.

ALTER TABLE applications ADD COLUMN IF NOT EXISTS frontend_https_port INTEGER;

-- Commentaire explicatif
COMMENT ON COLUMN applications.frontend_https_port IS 'Port HTTPS du frontend (si HTTPS activé par l''app). Permet au Hub d''ouvrir directement en HTTPS.';
