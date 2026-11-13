# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Configuration du pool de connexions DB du HUB.

Contexte (incidents 13/11/2026, auto-register + approve de 'exemple-app') :
les événements d'ajout d'app déclenchent des tempêtes de requêtes
(broadcast SSE → vagues de /api/apps/pending, /api/apps/, token/refresh,
unread-count) dont la demande concurrente de sessions dépassait le pool
5 + 10 = 15 connexions → QueuePool TimeoutError (500 en cascade pendant
~50 s, app_readtimeout M2M côté app qui s'enregistre).

Invariant : le pool dimensionné dans app.database doit absorber ces
tempêtes tout en restant sous max_connections PostgreSQL (100 côté
hub-db, hub-api étant son seul consommateur à cette échelle).
"""
from app.database import DB_MAX_OVERFLOW, DB_POOL_SIZE, engine


def test_pool_size_couvre_les_tempetes_ajout_app():
    """pool_size >= 20 : les vagues ~30 requêtes simultanées ne doivent
    plus mettre 15+ requêtes en file d'attente de 30 s."""
    assert DB_POOL_SIZE >= 20, (
        f"DB_POOL_SIZE={DB_POOL_SIZE} < 20 : le pool ne couvre pas les "
        "tempêtes de requêtes des événements d'ajout d'app "
        "(incidents 13/11/2026)"
    )


def test_max_overflow_couvre_les_pics():
    """max_overflow >= 20 : le plafond dynamique doit absorber les pics
    au-delà du pool de base."""
    assert DB_MAX_OVERFLOW >= 20, (
        f"DB_MAX_OVERFLOW={DB_MAX_OVERFLOW} < 20 : pics non couverts"
    )


def test_plafond_global_sous_max_connections_postgres():
    """pool_size + max_overflow doit rester nettement sous max_connections
    PostgreSQL (100) — marge pour les connexions psql/admin/supervision."""
    total = DB_POOL_SIZE + DB_MAX_OVERFLOW
    assert total <= 60, (
        f"Plafond pool ({total}) trop proche de max_connections PG (100) : "
        "risque d'épuisement côté serveur DB"
    )


def test_engine_utilise_le_pool_dimensionne():
    """L'engine réel doit refléter les constantes (pas de valeurs par
    défaut SQLAlchemy qui remettraient le pool à 5 + 10)."""
    pool = engine.sync_engine.pool
    # SQLite :memory: (tests) → StaticPool, pas de sémantique de taille ;
    # l'assertion ne porte que sur les backends poolisés (Postgres prod).
    if type(pool).__name__ == "StaticPool":
        return
    assert pool.size() == DB_POOL_SIZE, (
        f"engine pool.size()={pool.size()} != DB_POOL_SIZE={DB_POOL_SIZE}"
    )
    assert pool._max_overflow == DB_MAX_OVERFLOW, (
        f"engine _max_overflow={pool._max_overflow} != "
        f"DB_MAX_OVERFLOW={DB_MAX_OVERFLOW}"
    )
