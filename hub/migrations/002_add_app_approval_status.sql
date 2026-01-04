-- Migration: Add approval system for applications
-- Date: 2026-01-04
-- Description: Ajoute un système d'approbation admin pour les applications auto-enregistrées
--              afin d'empêcher les applications non autorisées de s'enregistrer

-- Ajout de la colonne approval_status
-- Les apps existantes sont automatiquement approuvées (approved)
-- Les nouvelles apps auto-enregistrées seront en 'pending'
ALTER TABLE applications ADD COLUMN approval_status VARCHAR(20) DEFAULT 'approved';

-- Ajout de la colonne approved_by_id (référence vers l'admin qui a approuvé)
ALTER TABLE applications ADD COLUMN approved_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

-- Ajout de la colonne approved_at (date d'approbation)
ALTER TABLE applications ADD COLUMN approved_at TIMESTAMP;

-- Ajout de la colonne rejection_reason (raison du rejet si applicable)
ALTER TABLE applications ADD COLUMN rejection_reason VARCHAR(500);

-- Index pour optimiser les requêtes sur le statut d'approbation
CREATE INDEX IF NOT EXISTS idx_applications_approval_status ON applications(approval_status);

-- Note: Les applications existantes gardent le statut 'approved' par défaut
-- Seules les nouvelles applications auto-enregistrées auront le statut 'pending'
