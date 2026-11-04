-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 025: Selection des admins notifies pour les demandes de compte

INSERT INTO app_settings (key, value) VALUES ('account_request_notification_admin_ids', '[]')
ON CONFLICT (key) DO NOTHING;
