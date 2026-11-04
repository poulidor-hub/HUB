-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration: 050_app_groups
-- Description: Groupes d'applications (regroupement purement visuel du dashboard)

CREATE TABLE IF NOT EXISTS app_groups (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description VARCHAR(500),
    icon VARCHAR(50) DEFAULT 'bi-collection',
    color VARCHAR(7) DEFAULT '#0d6efd',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES app_groups(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_applications_group_id ON applications(group_id);
