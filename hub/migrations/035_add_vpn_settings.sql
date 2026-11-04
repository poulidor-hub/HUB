-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

INSERT INTO app_settings (key, value) VALUES ('vpn_enabled', 'false')
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value) VALUES ('vpn_config_hash', '')
ON CONFLICT (key) DO NOTHING;
