# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests des endpoints /api/audit/usage/* (admin uniquement)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import AppUsageSegment, UserSession, utcnow
from app.usage_tracking import touch_app_presence
from tests.test_usage_segments import _make_session

# Instant figé loin de toute frontière de jour UTC (convention naive-UTC).
# Les segments de test de 10 min ne doivent jamais traverser minuit : le clamp
# `ended = min(raw_end, day_end)` de l'endpoint les tronquerait (flake 23:50→00:00
# UTC observé en CI — durée 330 s au lieu de 600 s).
FROZEN_NOW = datetime(2026, 6, 15, 12, 0, 0)


def _freeze_utcnow(monkeypatch):
    """Fige utcnow dans les 3 modules qui le bindent (models, tracking, route)."""
    for mod in ("app.models", "app.usage_tracking", "app.routes.audit"):
        monkeypatch.setattr(f"{mod}.utcnow", lambda: FROZEN_NOW)


@pytest.mark.asyncio
async def test_usage_user_day(client, async_session, admin_token, test_user, monkeypatch):
    _freeze_utcnow(monkeypatch)
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    seg = (await async_session.execute(select(AppUsageSegment))).scalar_one()
    seg.ended_at = seg.started_at + timedelta(minutes=10)
    seg.end_reason = "switch"
    await async_session.commit()

    today = FROZEN_NOW.strftime("%Y-%m-%d")
    resp = await client.get(
        f"/api/audit/usage/user/{test_user.id}?date={today}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == test_user.id
    assert body["username"] == test_user.username
    assert len(body["segments"]) == 1
    assert body["segments"][0]["app_slug"] == "test-app"
    assert 590 <= body["segments"][0]["duration_seconds"] <= 610
    assert body["totals"][0]["total_seconds"] == body["segments"][0]["duration_seconds"]
    assert body["day_total_seconds"] == body["segments"][0]["duration_seconds"]


@pytest.mark.asyncio
async def test_usage_user_404_and_400(client, admin_token):
    resp = await client.get(
        "/api/audit/usage/user/999999?date=2026-08-15",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404

    resp = await client.get(
        "/api/audit/usage/user/1?date=not-a-date",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_usage_requires_admin(client, user_token, test_user):
    resp = await client.get(
        f"/api/audit/usage/user/{test_user.id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_usage_apps_structure(client, async_session, admin_token, test_user):
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    seg = (await async_session.execute(select(AppUsageSegment))).scalar_one()
    seg.ended_at = seg.started_at + timedelta(minutes=10)
    await async_session.commit()

    resp = await client.get(
        "/api/audit/usage/apps?days=1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["period_days"] == 1
    apps = {a["slug"]: a for a in body["apps"]}
    assert "test-app" in apps
    app = apps["test-app"]
    assert len(app["simultaneous"]) > 0
    assert all({"t", "count"} <= set(p) for p in app["simultaneous"])
    assert len(app["daily_uniques"]) == 1
    assert app["daily_uniques"][0]["count"] == 1
    rec = app["records"]
    for key in ("max", "max_at", "max_users", "avg", "min", "p95", "stddev"):
        assert key in rec
    assert rec["max"] >= 1


@pytest.mark.asyncio
async def test_usage_heatmap_structure(client, async_session, admin_token, test_user):
    s = await _make_session(async_session, test_user)
    s.logout_at = s.login_at + timedelta(hours=1)
    await async_session.commit()

    resp = await client.get(
        "/api/audit/usage/heatmap?days=7",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["period_days"] == 7
    assert len(body["days"]) == 7
    for day in body["days"]:
        assert len(day["hours"]) == 24
    total = sum(sum(d["hours"]) for d in body["days"])
    assert total >= 1


@pytest.mark.asyncio
async def test_usage_heatmap_open_session_bounded_to_now(
    client, async_session, admin_token, test_user, monkeypatch
):
    """Régression heatmap : une session ouverte ne doit remplir aucune
    cellule du jour courant postérieure à maintenant (12h00 figé).
    Bug observé : les cellules 13h→23h apparaissaient remplies à midi."""
    _freeze_utcnow(monkeypatch)
    s = await _make_session(async_session, test_user)
    s.login_at = FROZEN_NOW - timedelta(minutes=10)  # connectée à 11h50
    await async_session.commit()

    resp = await client.get(
        "/api/audit/usage/heatmap?days=1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    hours = resp.json()["days"][-1]["hours"]
    assert hours[11] == 1                            # active sur 11h→12h
    assert hours[12:] == [0] * 12, f"cellules futures remplies: {hours}"


@pytest.mark.asyncio
async def test_usage_apps_filter_by_app(client, async_session, admin_token, test_user):
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    seg = (await async_session.execute(select(AppUsageSegment))).scalar_one()
    seg.ended_at = seg.started_at + timedelta(minutes=10)
    await async_session.commit()

    resp = await client.get(
        "/api/audit/usage/apps?days=1&app=test-app",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    slugs = [a["slug"] for a in resp.json()["apps"]]
    assert slugs == ["test-app"]

    resp2 = await client.get(
        "/api/audit/usage/apps?days=1&app=inconnue",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["apps"] == []


@pytest.mark.asyncio
async def test_usage_user_open_segment_visible(client, async_session, admin_token, test_user, settings):
    """Un segment ouvert (app courante) doit apparaître, borné par le timeout de session."""
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    await async_session.commit()

    today = utcnow().strftime("%Y-%m-%d")
    resp = await client.get(
        f"/api/audit/usage/user/{test_user.id}?date={today}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    segs = [x for x in body["segments"] if x["app_slug"] == "test-app"]
    assert len(segs) == 1
    assert segs[0]["duration_seconds"] > 0
    assert segs[0]["duration_seconds"] <= settings.session_timeout_minutes * 60 + 5


@pytest.mark.asyncio
async def test_usage_user_excludes_workshop_segments(client, async_session, admin_token, test_user, monkeypatch):
    """Les segments atelier (slug finissant par -ws) sont exclus de l'uptime
    utilisateur : cette vue ne mesure que l'usage production."""
    _freeze_utcnow(monkeypatch)
    s = await _make_session(async_session, test_user)
    await touch_app_presence(async_session, s, "test-app")
    await touch_app_presence(async_session, s, "test-app-ws")
    segs = (await async_session.execute(select(AppUsageSegment))).scalars().all()
    assert len(segs) == 2
    for seg in segs:
        seg.ended_at = seg.started_at + timedelta(minutes=10)
        seg.end_reason = "switch"
    await async_session.commit()

    today = FROZEN_NOW.strftime("%Y-%m-%d")
    resp = await client.get(
        f"/api/audit/usage/user/{test_user.id}?date={today}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    slugs = [seg["app_slug"] for seg in body["segments"]]
    assert "test-app-ws" not in slugs
    assert "test-app" in slugs
    assert all(t["app_slug"] != "test-app-ws" for t in body["totals"])
    assert body["day_total_seconds"] == 600  # 10 min de prod uniquement
