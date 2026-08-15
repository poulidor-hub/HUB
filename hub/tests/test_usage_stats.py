"""Tests des agrégats d'usage (fonctions pures)."""
import time
from datetime import datetime, timedelta

from app.usage_stats import (
    compute_activity_heatmap,
    compute_daily_uniques,
    compute_records,
    compute_simultaneous_series,
    bucket_size_for_days,
    downsample_max,
)

T0 = datetime(2026, 8, 15, 10, 0, 0)
NAMES = {1: "alice", 2: "bob"}


def _seg(user_id, app, start_min, end_min):
    return {
        "app_slug": app,
        "user_id": user_id,
        "started_at": T0 + timedelta(minutes=start_min),
        "ended_at": T0 + timedelta(minutes=end_min),
    }


def test_bucket_size_for_days():
    assert bucket_size_for_days(1) == 5
    assert bucket_size_for_days(7) == 30
    assert bucket_size_for_days(30) == 120
    assert bucket_size_for_days(90) == 360


def test_simultaneous_series_overlap():
    # alice sur l'app 10:00→10:20, bob 10:10→10:15
    entries = [_seg(1, "app", 0, 20), _seg(2, "app", 10, 15)]
    series = compute_simultaneous_series(
        entries, T0, T0 + timedelta(hours=1), 5, NAMES
    )
    assert len(series) == 12  # 60 min / 5 min
    by_t = {s["t"]: s for s in series}
    t_1000 = T0.isoformat()
    t_1010 = (T0 + timedelta(minutes=10)).isoformat()
    t_1030 = (T0 + timedelta(minutes=30)).isoformat()
    assert by_t[t_1000]["count"] == 1
    assert by_t[t_1010]["count"] == 2
    assert set(by_t[t_1010]["users"]) == {"alice", "bob"}
    assert by_t[t_1030]["count"] == 0


def test_simultaneous_series_dedupes_users():
    # Même user, deux segments couvrant t=10:10 -> compté 1 fois
    entries = [_seg(1, "app", 0, 20), _seg(1, "other", 10, 15)]
    series = compute_simultaneous_series(
        entries, T0, T0 + timedelta(minutes=15), 5, NAMES
    )
    t_1010 = (T0 + timedelta(minutes=10)).isoformat()
    by_t = {s["t"]: s for s in series}
    assert by_t[t_1010]["count"] == 1
    assert by_t[t_1010]["users"] == ["alice"]


def test_simultaneous_series_open_ended_clamped_to_end():
    # segment ouvert (ended_at None) -> actif jusqu'à la fin de fenêtre
    entries = [{"app_slug": "app", "user_id": 1,
                "started_at": T0, "ended_at": None}]
    series = compute_simultaneous_series(
        entries, T0, T0 + timedelta(hours=1), 30, NAMES
    )
    assert all(s["count"] == 1 for s in series)


def test_compute_records():
    series = [{"t": f"t{i}", "count": c, "users": []}
              for i, c in enumerate([0, 2, 3, 1, 3, 0])]
    r = compute_records(series)
    assert r["max"] == 3
    assert r["max_at"] == "t2"
    assert r["max_users"] == []
    assert r["min"] == 0
    assert r["avg"] == 1.5
    assert r["stddev"] > 0
    assert 0 <= r["p95"] <= 3


def test_compute_records_empty():
    r = compute_records([])
    assert r == {"max": 0, "max_at": None, "max_users": [],
                 "avg": 0, "min": 0, "p95": 0, "stddev": 0}


def test_downsample_max():
    series = [{"t": f"t{i}", "count": i, "users": []} for i in range(6)]
    out = downsample_max(series, 3)
    assert [s["count"] for s in out] == [2, 5]
    assert out[0]["t"] == "t0"


def test_daily_uniques():
    d1 = T0
    d2 = T0 + timedelta(days=1)
    entries = [
        {"app_slug": "app", "user_id": 1, "started_at": d1, "ended_at": d1},
        {"app_slug": "app", "user_id": 1, "started_at": d1 + timedelta(hours=2), "ended_at": d1},
        {"app_slug": "app", "user_id": 2, "started_at": d2, "ended_at": d2},
    ]
    out = compute_daily_uniques(entries, d1, 2)
    assert out[0]["date"] == "2026-08-15" and out[0]["count"] == 1
    assert out[1]["date"] == "2026-08-16" and out[1]["count"] == 1


def test_perf_90j_window():
    """Garde-fou : agrégats sur une fenêtre 90 j (25 920 buckets) < 5 s."""
    from datetime import timedelta
    entries = [
        {"app_slug": "app", "user_id": u, "started_at": T0 + timedelta(hours=h),
         "ended_at": T0 + timedelta(hours=h + 2)}
        for u in (1, 2, 3) for h in range(0, 24 * 90, 48)
    ]
    t0 = time.monotonic()
    series = compute_simultaneous_series(
        entries, T0, T0 + timedelta(days=90), 5, NAMES
    )
    compute_records(series)
    compute_daily_uniques(entries, T0, 90)
    elapsed = time.monotonic() - t0
    assert len(series) == 24 * 90 * 12  # 25 920 buckets de 5 min
    assert elapsed < 5.0, f"agrégats 90j trop lents: {elapsed:.1f}s"


def test_activity_heatmap():
    # Une session 10:00→12:30 le 15/08 : couvre les heures 10, 11, 12
    sessions = [(T0, T0 + timedelta(hours=2, minutes=30))]
    out = compute_activity_heatmap(sessions, datetime(2026, 8, 15), 1)
    assert len(out) == 1
    hours = out[0]["hours"]
    assert len(hours) == 24
    assert hours[10] == 1 and hours[11] == 1 and hours[12] == 1
    assert hours[9] == 0 and hours[13] == 0
