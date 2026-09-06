"""
Régression — « can't subtract offset-naive and offset-aware datetimes » (asyncpg).

Un client (front admin ou app M2M) peut envoyer `expires_at` / `scheduled_at` en
ISO 8601 avec timezone (ex: "2026-09-07T22:35:00Z"). Pydantic v2 produit alors un
datetime AWARE (tzinfo=TzInfo(0)), que asyncpg refuse d'encoder pour une colonne
TIMESTAMP WITHOUT TIME ZONE — la convention du parc étant des datetimes naive UTC
en DB (cf. app.models.utcnow).

Fix attendu : normalisation naive UTC à la source, dans les schémas d'entrée
(MessageCreate, MessageM2MCreate). La conversion doit être un vrai décalage UTC
(astimezone) et non un simple strip du tzinfo.

Note : ce bug n'est pas reproductible sur la suite SQLite in-memory (asyncpg
n'intervient pas), d'où un test au niveau schéma Pydantic.
"""
from datetime import datetime, timezone

from app.schemas import MessageCreate, MessageM2MCreate


class TestMessageCreateNaiveUtc:
    def test_expires_at_z_suffix_becomes_naive_utc(self):
        payload = MessageCreate(title="Incident", expires_at="2026-09-07T22:35:00Z")
        assert payload.expires_at == datetime(2026, 9, 7, 22, 35)
        assert payload.expires_at.tzinfo is None

    def test_scheduled_at_offset_converted_to_utc_then_naive(self):
        # 10h00 à +02:00 == 08h00 UTC : on veut le décalage correct, pas un strip
        payload = MessageCreate(title="Différé", scheduled_at="2026-09-08T10:00:00+02:00")
        assert payload.scheduled_at == datetime(2026, 9, 8, 8, 0)
        assert payload.scheduled_at.tzinfo is None

    def test_naive_input_unchanged(self):
        payload = MessageCreate(title="x", expires_at=datetime(2026, 9, 7, 22, 35))
        assert payload.expires_at == datetime(2026, 9, 7, 22, 35)

    def test_none_unchanged(self):
        payload = MessageCreate(title="x")
        assert payload.expires_at is None
        assert payload.scheduled_at is None

    def test_aware_datetime_object_becomes_naive_utc(self):
        aware = datetime(2026, 9, 7, 22, 35, tzinfo=timezone.utc)
        payload = MessageCreate(title="x", expires_at=aware)
        assert payload.expires_at == datetime(2026, 9, 7, 22, 35)
        assert payload.expires_at.tzinfo is None


class TestMessageM2MCreateNaiveUtc:
    def test_expires_at_z_suffix_becomes_naive_utc(self):
        payload = MessageM2MCreate(
            title="Notif app",
            target_usernames=["alice"],
            expires_at="2026-09-07T22:35:00Z",
        )
        assert payload.expires_at == datetime(2026, 9, 7, 22, 35)
        assert payload.expires_at.tzinfo is None

    def test_none_unchanged(self):
        payload = MessageM2MCreate(title="x", target_usernames=["alice"])
        assert payload.expires_at is None
