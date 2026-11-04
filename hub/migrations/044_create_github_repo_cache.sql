-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

-- Migration 044: Cache persistant des repos GitHub accessibles au PAT
-- Peuplé par POST /api/apps/github/repos/refresh. Lu par GET /api/apps/github/repos.
-- Clé d'upsert : repo_id (GitHub id stable).

CREATE TABLE IF NOT EXISTS github_repo_cache (
    id                SERIAL PRIMARY KEY,
    repo_id           BIGINT UNIQUE NOT NULL,
    name              VARCHAR(255) NOT NULL,
    full_name         VARCHAR(512) NOT NULL,
    owner             VARCHAR(255) NOT NULL,
    html_url          VARCHAR(1024) NOT NULL,
    clone_url         VARCHAR(1024) NOT NULL,
    description       TEXT,
    default_branch    VARCHAR(255),
    fork              BOOLEAN NOT NULL DEFAULT FALSE,
    archived          BOOLEAN NOT NULL DEFAULT FALSE,
    private           BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at_github TIMESTAMP,
    fetched_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_github_repo_cache_owner     ON github_repo_cache (owner);
CREATE INDEX IF NOT EXISTS ix_github_repo_cache_full_name ON github_repo_cache (full_name);
