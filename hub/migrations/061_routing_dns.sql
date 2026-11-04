-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 061 (chantier B) : routage par noms DNS.
-- dns_name sur applications (nom de routage <dns_name>.<hub_dns_zone>, défaut:
-- slug via backfill au boot) + table routed_service (services tiers routés,
-- ex. PhiliaNG — auparavant un vhost nginx caché).

ALTER TABLE applications ADD COLUMN IF NOT EXISTS dns_name VARCHAR(63);
CREATE UNIQUE INDEX IF NOT EXISTS uq_applications_dns_name
    ON applications (dns_name) WHERE dns_name IS NOT NULL;

CREATE TABLE IF NOT EXISTS routed_service (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    dns_name   VARCHAR(255) NOT NULL UNIQUE,
    target_url VARCHAR(512) NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
