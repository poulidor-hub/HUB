-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration: Add must_change_password column to users table
-- Date: 2026-01-04
-- Description: Permet de forcer les utilisateurs à changer leur mot de passe à la prochaine connexion

-- Ajout de la colonne must_change_password
-- Cette colonne sera FALSE par défaut pour les utilisateurs existants
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT 0;

-- SQLite ne supporte pas les commentaires de colonnes mais l'intention est :
-- must_change_password: Si TRUE, l'utilisateur doit changer son mot de passe à la prochaine connexion
