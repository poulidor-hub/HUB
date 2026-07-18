-- Migration 038 : mode d'enforcement M2M par application (compat/enforced)
ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS m2m_enforcement_mode VARCHAR(16) NOT NULL DEFAULT 'compat';
ALTER TABLE applications
    ADD CONSTRAINT ck_applications_m2m_enforcement_mode
    CHECK (m2m_enforcement_mode IN ('compat', 'enforced'));
