-- Migration 029: Systeme de snapshots complets, suppression des backup clusters
-- Date: 2026-06-23

-- 1. Creer la table hub_snapshots
CREATE TABLE IF NOT EXISTS hub_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_id VARCHAR(36) UNIQUE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    progress_percent SMALLINT DEFAULT 0,
    progress_step VARCHAR(40) DEFAULT 'queued',
    s3_bucket VARCHAR(255),
    s3_key VARCHAR(500),
    total_size_bytes BIGINT DEFAULT 0,
    checksum_sha256 VARCHAR(64),
    manifest JSONB DEFAULT '{}',
    include_pg_dump BOOLEAN DEFAULT TRUE,
    include_files BOOLEAN DEFAULT TRUE,
    include_app_backups BOOLEAN DEFAULT TRUE,
    include_docker_config BOOLEAN DEFAULT TRUE,
    pg_dump_size_bytes BIGINT DEFAULT 0,
    apps_count INTEGER DEFAULT 0,
    apps_succeeded INTEGER DEFAULT 0,
    apps_failed INTEGER DEFAULT 0,
    error_message TEXT,
    retention_policy JSONB DEFAULT '{}',
    created_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_hub_snapshots_status ON hub_snapshots(status);
CREATE INDEX IF NOT EXISTS idx_hub_snapshots_created_at ON hub_snapshots(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hub_snapshots_snapshot_id ON hub_snapshots(snapshot_id);

-- 2. Supprimer les backup clusters (remplaces par le snapshot complet)
DROP TABLE IF EXISTS backup_cluster_apps CASCADE;
DROP TABLE IF EXISTS backup_clusters CASCADE;
