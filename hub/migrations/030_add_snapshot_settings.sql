-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 030: Parametres de snapshot (app_settings)
-- Date: 2026-06-23

INSERT INTO app_settings (key, value) VALUES
    ('snapshot_cron', ''),
    ('snapshot_retention_max', '7'),
    ('snapshot_retention_min_age_hours', '24'),
    ('s3_endpoint', ''),
    ('s3_bucket', 'hub-snapshots'),
    ('s3_region', 'eu-west-1'),
    ('s3_access_key', ''),
    ('s3_secret_key', ''),
    ('s3_prefix', 'prod/')
ON CONFLICT (key) DO NOTHING;
