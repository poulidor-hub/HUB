-- 068 : secteur principal (refonte préférences, spec 2026-11-14)
-- is_primary : au plus une ligne par (user_id, category_id) via index partiel unique.
-- primary_enabled : autorise le choix d'une primaire sur une catégorie (admin).
-- Idempotent : fresh install matérialise déjà ces colonnes via create_all.

ALTER TABLE user_tags ADD COLUMN IF NOT EXISTS is_primary BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE tag_categories ADD COLUMN IF NOT EXISTS primary_enabled BOOLEAN NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_tags_primary ON user_tags (user_id, category_id) WHERE is_primary;
