-- 045_add_application_is_unmanaged.sql
-- Apps "non gérées" (launcher-only) : visibles/lancables mais exclues du health-check, backup et sync Docker.
ALTER TABLE applications ADD COLUMN IF NOT EXISTS is_unmanaged BOOLEAN NOT NULL DEFAULT FALSE;
