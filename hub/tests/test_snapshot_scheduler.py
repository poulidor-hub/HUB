# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests de régression pour le scheduler de snapshots planifiés.

Le bug : _snapshot_cron_scheduler calculait `croniter(expr, now).get_next()`,
qui renvoie TOUJOURS une datetime strictement future. La comparaison
`next_run <= now` n'était donc jamais vraie → aucun snapshot planifié
ne se déclenchait jamais (vérifié empiriquement avec un cron "* * * * *"
et 150s d'observation : zéro déclenchement).

Le fix : extraire la décision dans la fonction pure `snapshot_cron_due()`,
basée sur la dernière occurrence PASSÉE du cron (`get_prev`) comparée à la
dernière occurrence déjà traitée — le même raisonnement que le scheduler
de backups d'apps (periodic_backup_scheduler avec next_backup_at).
"""
from datetime import datetime

from app.routes.snapshots import snapshot_cron_due


NOW = datetime(2026, 8, 15, 19, 20, 30)


class TestSnapshotCronDue:
    def test_first_check_initializes_without_triggering(self):
        """Premier check : on initialise last_handled à l'occurrence passée
        sans déclencher (pas de rattrapage d'une échéance antérieure au démarrage)."""
        due, last = snapshot_cron_due("* * * * *", NOW, None)
        assert due is False
        assert last == datetime(2026, 8, 15, 19, 20, 0)

    def test_triggers_when_new_occurrence_elapsed(self):
        """Une occurrence du cron est survenue depuis le dernier check → déclencher."""
        due, last = snapshot_cron_due(
            "* * * * *", NOW, datetime(2026, 8, 15, 19, 19, 0)
        )
        assert due is True
        assert last == datetime(2026, 8, 15, 19, 20, 0)

    def test_no_retrigger_for_already_handled_occurrence(self):
        """L'occurrence courante a déjà été traitée → ne pas re-déclencher."""
        due, last = snapshot_cron_due(
            "* * * * *", NOW, datetime(2026, 8, 15, 19, 20, 0)
        )
        assert due is False
        assert last == datetime(2026, 8, 15, 19, 20, 0)

    def test_daily_schedule_triggers_at_boundary(self):
        """Cron quotidien 03:00 — déclenche à 03:00:30 si le 03:00 du jour
        n'a pas encore été traité."""
        now = datetime(2026, 8, 15, 3, 0, 30)
        due, last = snapshot_cron_due(
            "0 3 * * *", now, datetime(2026, 8, 14, 3, 0, 0)
        )
        assert due is True
        assert last == datetime(2026, 8, 15, 3, 0, 0)

    def test_daily_schedule_not_due_before_boundary(self):
        """Avant l'heure prévue → pas de déclenchement."""
        now = datetime(2026, 8, 15, 2, 59, 0)
        due, _ = snapshot_cron_due(
            "0 3 * * *", now, datetime(2026, 8, 14, 3, 0, 0)
        )
        assert due is False

    def test_invalid_cron_does_not_raise(self):
        """Un cron invalide ne doit pas lever (le scheduler loguera l'erreur)."""
        due, last = snapshot_cron_due("pas-un-cron", NOW, None)
        assert due is False
        assert last is None

    def test_empty_cron_never_triggers(self):
        """Cron vide (scheduling désactivé) → jamais de déclenchement."""
        due, last = snapshot_cron_due("", NOW, datetime(2026, 8, 14, 3, 0, 0))
        assert due is False
        assert last is None

    def test_datetime_naive_output(self):
        """Les valeurs retournées doivent être naive UTC (cohérent avec utcnow())."""
        due, last = snapshot_cron_due("* * * * *", NOW, None)
        assert last is not None
        assert last.tzinfo is None
