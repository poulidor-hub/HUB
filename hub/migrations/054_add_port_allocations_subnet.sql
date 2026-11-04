-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration: 054_add_port_allocations_subnet
-- Description: Ajoute la colonne subnet aux allocations de ports : le Hub
-- attribue désormais un sous-réseau Docker (/24) par app depuis le pool
-- APP_SUBNET_POOL (défaut 10.50.0.0/16), en plus des ports. Les lignes
-- existantes gardent NULL et seront remplies paresseusement au prochain
-- appel d'allocation de l'app.

ALTER TABLE port_allocations ADD COLUMN IF NOT EXISTS subnet VARCHAR(30) NULL;
