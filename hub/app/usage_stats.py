"""
Agrégation des segments d'usage — fonctions pures Python.

Calculés côté application (et non en SQL PostgreSQL) pour rester
portables et testables sur SQLite. Volumétrie faible : quelques
dizaines de milliers de segments max sur 90 jours.
Toutes les dates sont en UTC.
"""
import math
from datetime import datetime, timedelta
from typing import Optional

BASE_BUCKET_MINUTES = 5  # granularité de base des simultanés + records


def bucket_size_for_days(days: int) -> int:
    """Taille de bucket d'affichage selon la période (borne ~360 points)."""
    if days <= 1:
        return 5
    if days <= 7:
        return 30
    if days <= 30:
        return 120
    return 360


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Percentile linéaire (équivalent percentile_cont de PostgreSQL)."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def compute_simultaneous_series(
    entries: list[dict],
    start: datetime,
    end: datetime,
    step_minutes: int,
    user_names: dict[int, str],
) -> list[dict]:
    """
    Utilisateurs simultanés par bucket temporel.
    Un user compte dans un bucket si un de ses segments couvre l'instant t.
    entries: [{"app_slug", "user_id", "started_at", "ended_at"}]
    """
    step = timedelta(minutes=step_minutes)
    series = []
    t = start
    while t < end:
        # user_id -> username : un user avec plusieurs segments couvrant t
        # ne compte qu'une fois
        active: dict[int, str] = {}
        for e in entries:
            s = max(e["started_at"], start)
            # segment ouvert -> clamp défensif à la fin de fenêtre
            en = e["ended_at"] or end
            if en > end:
                en = end
            if s <= t < en:
                active[e["user_id"]] = user_names.get(e["user_id"], str(e["user_id"]))
        series.append({"t": t.isoformat(), "count": len(active),
                       "users": list(active.values())})
        t += step
    return series


def downsample_max(series: list[dict], factor: int) -> list[dict]:
    """Agrège `factor` points consécutifs en gardant le max (t du 1er point)."""
    if factor <= 1:
        return series
    out = []
    for i in range(0, len(series), factor):
        chunk = series[i:i + factor]
        best = max(chunk, key=lambda s: s["count"])
        out.append({"t": chunk[0]["t"], "count": best["count"],
                    "users": best["users"]})
    return out


def compute_records(series: list[dict]) -> dict:
    """Records sur la série des simultanés : max/date/users, avg, min, p95, σ."""
    counts = [s["count"] for s in series]
    if not counts:
        return {"max": 0, "max_at": None, "max_users": [],
                "avg": 0, "min": 0, "p95": 0, "stddev": 0}
    peak = max(series, key=lambda s: s["count"])
    return {
        "max": peak["count"],
        "max_at": peak["t"],
        "max_users": peak["users"],
        "avg": round(sum(counts) / len(counts), 2),
        "min": min(counts),
        "p95": round(_percentile(sorted(counts), 0.95), 2),
        "stddev": round(math.sqrt(sum((c - sum(counts) / len(counts)) ** 2
                                      for c in counts) / len(counts)), 2),
    }


def compute_daily_uniques(entries: list[dict], start: datetime, days: int) -> list[dict]:
    """Utilisateurs uniques par jour (UTC) pour UNE app."""
    out = []
    for d in range(days):
        day_start = start + timedelta(days=d)
        day_end = day_start + timedelta(days=1)
        uniq = {
            e["user_id"] for e in entries
            if e["started_at"] < day_end and (e["ended_at"] or day_end) >= day_start
        }
        out.append({"date": day_start.strftime("%Y-%m-%d"), "count": len(uniq)})
    return out


def compute_activity_heatmap(
    sessions: list[tuple[datetime, Optional[datetime]]],
    start: datetime,
    days: int,
) -> list[dict]:
    """
    Grille jour × heure : nombre de sessions actives par bucket horaire (UTC).
    sessions: liste de (login_at, logout_at|None).
    """
    out = []
    for d in range(days):
        day_start = start + timedelta(days=d)
        hours = [0] * 24
        for login_at, logout_at in sessions:
            end = logout_at or datetime.max
            for h in range(24):
                b_start = day_start + timedelta(hours=h)
                b_end = b_start + timedelta(hours=1)
                if login_at < b_end and end > b_start:
                    hours[h] += 1
        out.append({"date": day_start.strftime("%Y-%m-%d"), "hours": hours})
    return out
