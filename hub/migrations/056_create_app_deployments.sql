-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 056: Historique des déploiements automatiques des apps
-- Créé par POST /api/apps/{slug}/deploy, piloté par app/deploy_service.py.
-- Lien par app_slug (pas de FK, cohérent avec port_allocations).

CREATE TABLE IF NOT EXISTS app_deployments (
    id           SERIAL PRIMARY KEY,
    app_slug     VARCHAR(50) NOT NULL,
    status       VARCHAR(16) NOT NULL DEFAULT 'pending',
    current_step VARCHAR(50),
    logs         TEXT,
    triggered_by VARCHAR(100),
    started_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at  TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_app_deployments_app_slug ON app_deployments (app_slug);
CREATE INDEX IF NOT EXISTS ix_app_deployments_status   ON app_deployments (status);
