-- Migration 006: Système de sauvegarde centralisée des applications
-- Date: 2026-01-07
-- Description: Ajoute la table app_backups pour stocker les métadonnées des sauvegardes
--              et les champs de configuration de scheduling sur les applications

-- =============================================================================
-- Table des sauvegardes d'applications
-- =============================================================================

CREATE TABLE IF NOT EXISTS app_backups (
    id SERIAL PRIMARY KEY,
    backup_id VARCHAR(36) UNIQUE NOT NULL,  -- UUID unique du backup
    
    -- Référence à l'application
    app_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
    app_slug VARCHAR(50) NOT NULL,
    app_version VARCHAR(50),  -- Version de l'app au moment du backup
    
    -- Métadonnées du backup
    total_items INTEGER DEFAULT 0,
    total_preferences INTEGER DEFAULT 0,
    total_files INTEGER DEFAULT 0,
    total_size_bytes BIGINT DEFAULT 0,
    checksum VARCHAR(64),  -- SHA-256 du fichier ZIP original
    
    -- Stockage
    storage_path VARCHAR(500) NOT NULL,  -- Chemin relatif du fichier
    encrypted_path VARCHAR(500),  -- Chemin du fichier chiffré (si exporté)
    is_encrypted BOOLEAN DEFAULT FALSE,
    
    -- Traçabilité
    created_by VARCHAR(100),  -- Username ou service_account qui a initié
    created_by_type VARCHAR(20) DEFAULT 'app',  -- 'app', 'user', 'scheduler'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    description TEXT,
    
    -- Metadata JSON (pour stocker des infos additionnelles)
    metadata JSONB DEFAULT '{}',
    
    -- Index
    CONSTRAINT idx_app_backups_backup_id UNIQUE (backup_id)
);

-- Index pour les requêtes fréquentes
CREATE INDEX IF NOT EXISTS idx_app_backups_app_slug ON app_backups(app_slug);
CREATE INDEX IF NOT EXISTS idx_app_backups_app_id ON app_backups(app_id);
CREATE INDEX IF NOT EXISTS idx_app_backups_created_at ON app_backups(created_at DESC);

-- =============================================================================
-- Champs de configuration du scheduling sur les applications
-- =============================================================================

-- Mode de sauvegarde: disabled, manual, scheduled
ALTER TABLE applications ADD COLUMN IF NOT EXISTS backup_mode VARCHAR(20) DEFAULT 'manual';

-- Expression cron pour le scheduling (ex: "0 2 * * *" = tous les jours à 2h)
ALTER TABLE applications ADD COLUMN IF NOT EXISTS backup_cron VARCHAR(100);

-- Rétention: nombre de backups à conserver (0 = illimité)
ALTER TABLE applications ADD COLUMN IF NOT EXISTS backup_retention INTEGER DEFAULT 5;

-- Dernier backup réussi
ALTER TABLE applications ADD COLUMN IF NOT EXISTS last_backup_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS last_backup_id VARCHAR(36);

-- Prochain backup planifié
ALTER TABLE applications ADD COLUMN IF NOT EXISTS next_backup_at TIMESTAMP WITH TIME ZONE;

-- Statut du dernier backup
ALTER TABLE applications ADD COLUMN IF NOT EXISTS last_backup_status VARCHAR(20);  -- success, failed, running

-- Commentaires sur les colonnes
COMMENT ON COLUMN applications.backup_mode IS 'Mode de sauvegarde: disabled, manual, scheduled';
COMMENT ON COLUMN applications.backup_cron IS 'Expression cron pour la planification (ex: 0 2 * * * = 2h du matin)';
COMMENT ON COLUMN applications.backup_retention IS 'Nombre de sauvegardes à conserver (0 = illimité)';
COMMENT ON COLUMN applications.last_backup_at IS 'Date/heure du dernier backup réussi';
COMMENT ON COLUMN applications.next_backup_at IS 'Date/heure du prochain backup planifié';

COMMENT ON TABLE app_backups IS 'Sauvegardes centralisées des applications du HUB';
COMMENT ON COLUMN app_backups.backup_id IS 'Identifiant UUID unique du backup';
COMMENT ON COLUMN app_backups.storage_path IS 'Chemin relatif du fichier ZIP dans /storage/backups/';
COMMENT ON COLUMN app_backups.encrypted_path IS 'Chemin du fichier chiffré exportable';
COMMENT ON COLUMN app_backups.checksum IS 'Hash SHA-256 du fichier pour vérification d''intégrité';
