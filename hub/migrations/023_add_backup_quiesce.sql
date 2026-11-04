-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 023: Option de quiesce Vaultwarden lors des backups planifies
-- Date: 2026-04-30
-- Description: Permet a une application enregistree au Hub de demander que le
--              conteneur Vaultwarden soit arrete pendant la creation du
--              snapshot (cohérence du volume) puis redemarre automatiquement.

ALTER TABLE applications
ADD COLUMN IF NOT EXISTS backup_quiesce BOOLEAN DEFAULT FALSE NOT NULL;

COMMENT ON COLUMN applications.backup_quiesce IS
'Si TRUE, le Hub envoie quiesce_vaultwarden=true lors d''un backup planifie '
'(arret/redemarrage automatique du conteneur Vaultwarden de l''app).';

-- Activer par defaut sur l''application vaultwarden si elle existe deja.
UPDATE applications
SET backup_quiesce = TRUE
WHERE slug = 'vaultwarden';
