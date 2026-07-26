-- 043_add_is_workshop.sql
ALTER TABLE applications ADD COLUMN IF NOT EXISTS is_workshop BOOLEAN NOT NULL DEFAULT FALSE;
