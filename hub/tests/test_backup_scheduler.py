# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests de régression pour le scheduler de backups planifiés (C4).

Le bug : periodic_backup_scheduler comparait app.next_backup_at (lu aware
depuis une colonne TIMESTAMPTZ par asyncpg en prod PostgreSQL) avec
utcnow() (naive) → TypeError à chaque minute → aucun backup planifié.

Note : les tests du Hub tournent en SQLite (datetimes naive), donc le bug
ne se reproduit pas naturellement ici. On simule explicitement une datetime
aware pour reproduire la condition de production.
"""
from datetime import datetime, timezone

from app.models import utcnow
from app.routes.app_backups import _naive_utc


def test_naive_utc_normalizes_aware_datetime():
    """asyncpg retourne des datetimes aware pour TIMESTAMPTZ ; _naive_utc normalise."""
    aware = datetime(2026, 7, 7, 10, 0, 0, tzinfo=timezone.utc)
    result = _naive_utc(aware)
    assert result is not None
    assert result.tzinfo is None, "_naive_utc doit retourner une datetime naive"
    assert result == datetime(2026, 7, 7, 10, 0, 0)


def test_naive_utc_passes_through_already_naive():
    """Une datetime déjà naive doit être retournée inchangée."""
    naive = datetime(2026, 7, 7, 10, 0, 0)
    assert _naive_utc(naive) == naive


def test_naive_utc_handles_none():
    """None doit retourner None (pas de crash sur next_backup_at nullable)."""
    assert _naive_utc(None) is None


def test_backup_due_comparison_does_not_raise_typeerror():
    """Régression C4 : comparer next_backup_at aware avec now naive levait TypeError.

    Simule la condition exacte du scheduler : une valeur aware (comme asyncpg
    la retournerait depuis TIMESTAMPTZ) comparée à utcnow() naive.
    Sans fix : aware <= naive → TypeError.
    Avec fix : _naive_utc(aware) <= naive → OK.
    """
    # Simule next_backup_at lu depuis TIMESTAMPTZ par asyncpg (aware)
    aware_next_backup_at = datetime(2026, 7, 7, 10, 0, 0, tzinfo=timezone.utc)
    now = utcnow()  # naive, comme dans le scheduler

    # Sans fix, la ligne suivante lèverait TypeError :
    #   aware_next_backup_at <= now
    # → TypeError: can't compare offset-naive and offset-aware datetimes

    # Avec fix, on normalise d'abord :
    normalized = _naive_utc(aware_next_backup_at)
    assert normalized is not None
    # Cette comparaison ne doit PAS lever :
    is_due = normalized <= now
    assert isinstance(is_due, bool)
