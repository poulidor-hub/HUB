-- Migration 003: Enrichissement des logs d'audit et création de error_logs
-- Ajoute des champs de traçabilité aux audit_logs et crée une table séparée pour les erreurs

-- =============================================================================
-- ENRICHISSEMENT TABLE audit_logs
-- =============================================================================

-- Ajout des nouveaux champs de traçabilité
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_id VARCHAR(36);
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS service_account_id INTEGER REFERENCES service_accounts(id) ON DELETE SET NULL;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS session_id INTEGER REFERENCES user_sessions(id) ON DELETE SET NULL;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS source_app_slug VARCHAR(50);
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_method VARCHAR(10);
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_path VARCHAR(255);
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS response_status INTEGER;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS duration_ms INTEGER;

-- Index pour les recherches fréquentes
CREATE INDEX IF NOT EXISTS idx_audit_logs_request_id ON audit_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_source_app_slug ON audit_logs(source_app_slug);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_service_account_id ON audit_logs(service_account_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_session_id ON audit_logs(session_id);

-- =============================================================================
-- NOUVELLE TABLE error_logs
-- =============================================================================

CREATE TABLE IF NOT EXISTS error_logs (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(36),
    error_code INTEGER NOT NULL,
    error_type VARCHAR(50),
    error_message VARCHAR(500),
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    service_account_id INTEGER REFERENCES service_accounts(id) ON DELETE SET NULL,
    source_app_slug VARCHAR(50),
    request_method VARCHAR(10),
    request_path VARCHAR(255),
    request_body TEXT,
    ip_address VARCHAR(45),
    user_agent VARCHAR(255),
    stack_trace TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index pour les recherches fréquentes sur error_logs
CREATE INDEX IF NOT EXISTS idx_error_logs_request_id ON error_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_error_logs_error_code ON error_logs(error_code);
CREATE INDEX IF NOT EXISTS idx_error_logs_source_app_slug ON error_logs(source_app_slug);
CREATE INDEX IF NOT EXISTS idx_error_logs_request_path ON error_logs(request_path);
CREATE INDEX IF NOT EXISTS idx_error_logs_created_at ON error_logs(created_at);

-- Commentaires sur les tables
COMMENT ON COLUMN audit_logs.request_id IS 'UUID unique pour traçabilité cross-service';
COMMENT ON COLUMN audit_logs.source_app_slug IS 'Application source de la requête (header X-Slug)';
COMMENT ON COLUMN audit_logs.duration_ms IS 'Temps de traitement de la requête en millisecondes';
COMMENT ON TABLE error_logs IS 'Logs des erreurs HTTP (4xx, 5xx) pour monitoring séparé';
