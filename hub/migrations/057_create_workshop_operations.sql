-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 057: Historique des opérations atelier (cycle UI vue santé)
-- Piloté par POST/DELETE /api/apps/{slug}/workshop*, exécuté par app/workshop_service.py.
-- Lien par app_slug (pas de FK, cohérent avec app_deployments).

CREATE TABLE IF NOT EXISTS workshop_operations (
    id           SERIAL PRIMARY KEY,
    app_slug     VARCHAR(50) NOT NULL,
    operation    VARCHAR(16) NOT NULL,
    status       VARCHAR(16) NOT NULL DEFAULT 'pending',
    current_step VARCHAR(50),
    logs         TEXT,
    triggered_by VARCHAR(100),
    started_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at  TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_workshop_operations_app_slug ON workshop_operations (app_slug);
CREATE INDEX IF NOT EXISTS ix_workshop_operations_status   ON workshop_operations (status);
