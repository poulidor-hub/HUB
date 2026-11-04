-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 055: Unicité des lignes user_app_roles + préparation des overrides
-- Date: 2026-08-17
-- Description: Dédoublonne (user_id, app_id) puis pose une contrainte unique.
--              Corrige les doublons créés par le filtre obsolète source='profile'
--              (la valeur réelle depuis la migration 011 est 'profile:<id>') :
--              un appel API direct pouvait créer une ligne manuelle en parallèle
--              d'une ligne profil. On garde la ligne profil (source LIKE 'profile:%')
--              lorsqu'elle existe, conforme à ce que l'UI affiche.

-- 1. Supprimer les lignes manuelles dupliquées quand une ligne profil existe
DELETE FROM user_app_roles
WHERE id IN (
    SELECT m.id
    FROM user_app_roles m
    JOIN user_app_roles p
      ON p.user_id = m.user_id
     AND p.app_id = m.app_id
     AND p.source LIKE 'profile:%'
    WHERE m.source = 'manual'
);

-- 2. En cas de doublons restants sur (user_id, app_id), garder la ligne d'id minimal
DELETE FROM user_app_roles
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (PARTITION BY user_id, app_id ORDER BY id) AS rn
        FROM user_app_roles
    ) t
    WHERE t.rn > 1
);

-- 3. Contrainte unique
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_app_roles_user_app
    ON user_app_roles(user_id, app_id);
