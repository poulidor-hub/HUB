-- Index de performance sur la table d'association service_account <-> application.
--
-- Constat pg_stat_statements (18/11/2026, hub-db) : la requête dominante du Hub
-- (2755 ms cumulés, 86 appels/h, ~2000 lignes renvoyées par appel) filtre
-- service_account_app_access par service_account_id en IN-list — et la table
-- n'avait AUCUN index : Seq Scan complet à chaque appel (EXPLAIN prouvé).
--
-- ATTENTION runner : apply_migrations() découpe ce fichier sur les points-virgules
-- SANS respecter les lignes de commentaire — interdiction absolue de tout
-- point-virgule dans les commentaires SQL de ce répertoire (incident 3.97.1,
-- syntax error at "le").
--
-- Idempotent (CREATE INDEX IF NOT EXISTS) et compatible SQLite + PostgreSQL :
-- le runner s'exécute sur les deux dialectes. Les index sont aussi déclarés dans
-- models.py (Table service_account_app_access) pour que create_all les pose sur
-- toute installation vierge.

CREATE INDEX IF NOT EXISTS ix_service_account_app_access_service_account_id
    ON service_account_app_access (service_account_id);

CREATE INDEX IF NOT EXISTS ix_service_account_app_access_app_id
    ON service_account_app_access (app_id);

-- ix_applications_id dupliquait exactement la clé primaire applications_pkey
-- (héritage d'un Column(index=True) sur une PK). Le planner bascule sur la PK
-- (même btree). Le index=True correspondant est retiré du modèle en 3.97.1
-- pour que create_all ne recrée pas l'index sur les installations vierges.
DROP INDEX IF EXISTS ix_applications_id;
