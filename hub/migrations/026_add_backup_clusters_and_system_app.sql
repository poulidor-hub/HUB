-- Migration 026: Clusters de sauvegarde et application systeme Hub
-- Date: 2026-05-08
-- Description: Ajoute des clusters persistants pour grouper les applications
--              dans la console de sauvegardes et marque le Hub comme une
--              application systeme sauvegardable via le scheduler existant.

ALTER TABLE applications
ADD COLUMN IF NOT EXISTS is_system_app BOOLEAN DEFAULT FALSE NOT NULL;

CREATE TABLE IF NOT EXISTS backup_clusters (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    slug VARCHAR(50) NOT NULL UNIQUE,
    description TEXT,
    color VARCHAR(7) DEFAULT '#0d6efd',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_backup_clusters_slug
ON backup_clusters(slug);

CREATE TABLE IF NOT EXISTS backup_cluster_apps (
    cluster_id INTEGER NOT NULL REFERENCES backup_clusters(id) ON DELETE CASCADE,
    app_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_backup_cluster_apps_cluster_app UNIQUE (cluster_id, app_id)
);

CREATE INDEX IF NOT EXISTS idx_backup_cluster_apps_cluster_id
ON backup_cluster_apps(cluster_id);

CREATE INDEX IF NOT EXISTS idx_backup_cluster_apps_app_id
ON backup_cluster_apps(app_id);

INSERT INTO applications (
    name,
    slug,
    description,
    icon,
    color,
    category,
    api_url,
    frontend_url,
    callback_url,
    is_active,
    requires_auth,
    is_shared_service,
    is_system_app,
    approval_status,
    backup_mode,
    backup_retention,
    backup_quiesce,
    created_at,
    updated_at
)
VALUES (
    'Hub',
    'hub',
    'Application systeme representant la configuration du Hub pour les sauvegardes planifiees.',
    'bi-grid-3x3-gap-fill',
    '#495057',
    'Systeme',
    'http://hub-api:8000',
    '/',
    NULL,
    TRUE,
    TRUE,
    FALSE,
    TRUE,
    'approved',
    'manual',
    5,
    FALSE,
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
)
ON CONFLICT (slug) DO UPDATE SET
    is_system_app = TRUE,
    category = COALESCE(applications.category, 'Systeme'),
    icon = COALESCE(applications.icon, 'bi-grid-3x3-gap-fill'),
    color = COALESCE(applications.color, '#495057'),
    backup_mode = COALESCE(applications.backup_mode, 'manual'),
    backup_retention = COALESCE(applications.backup_retention, 5),
    updated_at = CURRENT_TIMESTAMP;
