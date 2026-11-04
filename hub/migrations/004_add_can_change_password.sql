-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 004: Ajouter le champ can_change_password pour les comptes partagés
-- Ce champ permet d'empêcher certains utilisateurs de modifier leur mot de passe
-- Utile pour les comptes "accueil" ou autres comptes partagés

ALTER TABLE users ADD COLUMN IF NOT EXISTS can_change_password BOOLEAN DEFAULT TRUE;

-- Mettre à jour les utilisateurs existants pour qu'ils puissent changer leur mdp
UPDATE users SET can_change_password = TRUE WHERE can_change_password IS NULL;
