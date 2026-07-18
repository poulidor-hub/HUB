-- Migration 037: Table des dépendances inter-apps (M2M).
-- Chaque app déclare ses deps via depends.yaml, envoyé au hub au register().
-- last_sync_at est mis à jour par POST /api/apps/sync-report (best-effort).

CREATE TABLE IF NOT EXISTS app_dependencies (
    id SERIAL PRIMARY KEY,
    source_slug VARCHAR(100) NOT NULL,
    target_slug VARCHAR(100) NOT NULL,
    note TEXT,
    declared_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_sync_at TIMESTAMP,
    CONSTRAINT uq_app_dep_pair UNIQUE (source_slug, target_slug)
);

CREATE INDEX IF NOT EXISTS ix_app_dependencies_source_slug ON app_dependencies (source_slug);
CREATE INDEX IF NOT EXISTS ix_app_dependencies_target_slug ON app_dependencies (target_slug);
