"""Tests des endpoints /api/audit/usage/* (admin uniquement)."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import AppUsageSegment, UserSession, utcnow
from app.usage_tracking import record_app_switch
from tests.test_usage_segments import _make_session


@pytest.mark.asyncio
async def test_usage_user_day(client, async_session, admin_token, test_user):
    s = await _make_session(async_session, test_user)
    await record_app_switch(async_session, s, "test-app")
    seg = (await async_session.execute(select(AppUsageSegment))).scalar_one()
    seg.ended_at = seg.started_at + timedelta(minutes=10)
    seg.end_reason = "switch"
    await async_session.commit()

    today = utcnow().strftime("%Y-%m-%d")
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
    await record_app_switch(async_session, s, "test-app")
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
async def test_usage_apps_filter_by_app(client, async_session, admin_token, test_user):
    s = await _make_session(async_session, test_user)
    await record_app_switch(async_session, s, "test-app")
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
    await record_app_switch(async_session, s, "test-app")
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
