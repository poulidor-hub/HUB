-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- 043_add_is_workshop.sql
ALTER TABLE applications ADD COLUMN IF NOT EXISTS is_workshop BOOLEAN NOT NULL DEFAULT FALSE;
