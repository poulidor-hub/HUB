-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 027: Table d'allocation dynamique des ports d'applications
-- Le Hub attribue les ports (API + Frontend) aux apps lors du pré-vol.
-- Clé d'idempotence : (app_slug, host).

CREATE TABLE IF NOT EXISTS port_allocations (
    id SERIAL PRIMARY KEY,
    app_slug VARCHAR(50) NOT NULL,
    host VARCHAR(255) NOT NULL DEFAULT '',
    api_port INTEGER NOT NULL,
    frontend_port INTEGER NOT NULL,
    frontend_https_port INTEGER NOT NULL,
    allocated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_port_allocations_slug_host UNIQUE (app_slug, host)
);

CREATE INDEX IF NOT EXISTS ix_port_allocations_app_slug ON port_allocations (app_slug);
