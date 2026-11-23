# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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
    n_buckets = -(-(end - start) // step)  # ceil : dernier bucket partiel inclus
    if n_buckets <= 0:
        return []
    # Marquage par segment (O(n_buckets + segments)) : chaque segment
    # marque les buckets qu'il couvre, au lieu de re-scanner toutes les
    # entries pour chaque bucket. Le bucket i couvre [start + i*step,
    # start + (i+1)*step) ; un segment couvre le bucket si
    # s <= start + i*step < en.
    buckets: list[dict[int, str]] = [dict() for _ in range(n_buckets)]
    for e in entries:
        s = max(e["started_at"], start)
        # segment ouvert -> clamp défensif à la fin de fenêtre
        en = e["ended_at"] or end
        if en > end:
            en = end
        if s >= en:
            continue  # segment hors fenêtre
        q, r = divmod(s - start, step)
        i0 = q + (1 if r else 0)  # 1er bucket avec t >= s
        i1 = -(-(en - start) // step)  # 1er bucket avec t >= en (exclu)
        username = user_names.get(e["user_id"], str(e["user_id"]))
        # un user avec plusieurs segments couvrant t ne compte qu'une fois
        for i in range(max(i0, 0), min(i1, n_buckets)):
            buckets[i][e["user_id"]] = username
    return [
        {"t": (start + i * step).isoformat(), "count": len(users_map),
         "users": list(users_map.values())}
        for i, users_map in enumerate(buckets)
    ]


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
    mean = sum(counts) / len(counts)
    return {
        "max": peak["count"],
        "max_at": peak["t"],
        "max_users": peak["users"],
        "avg": round(mean, 2),
        "min": min(counts),
        "p95": round(_percentile(sorted(counts), 0.95), 2),
        "stddev": round(math.sqrt(sum((c - mean) ** 2
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
    now: datetime,
) -> list[dict]:
    """
    Grille jour × heure : nombre de sessions actives par bucket horaire (UTC).
    sessions: liste de (login_at, logout_at|None) ; `now` est l'instant réel
    qui borne le calcul (la route passe utcnow()).

    Une session ouverte (logout_at None) a une fin virtuelle `now` — la
    route fournit idéalement min(now, last_activity_at + timeout) comme fin
    effective (cohérence cleanup_stale_sessions / get_user_usage_day) et la
    fonction re-clampe défensivement toute fin à `now` (défense en
    profondeur) : aucune cellule postérieure à l'heure courante ne peut
    être remplie.

    O(sessions + heures couvertes) : chaque session marque directement
    l'intervalle de buckets qu'elle recouvre, au lieu de re-scanner toute
    la grille (jours × 24 buckets) pour chaque session. Bornes strictes
    identiques à la référence naïve (cf. test d'équivalence) : un bucket
    [b, b+1h) est couvert si login_at < b+1h et fin_virtuelle > b.
    """
    HOUR_US = 3_600_000_000
    WINDOW_US = days * 86_400_000_000

    def to_us(dt: datetime) -> int:
        delta = dt - start
        return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds

    now_us = to_us(now)
    grid = [[0] * 24 for _ in range(days)]
    for login_at, logout_at in sessions:
        s = to_us(login_at)
        e = to_us(logout_at) if logout_at is not None else now_us
        if e > now_us:
            e = now_us  # défense en profondeur : rien après maintenant
        if s < 0:
            s = 0
        if e > WINDOW_US:
            e = WINDOW_US  # les buckets au-delà de la fenêtre n'existent pas
        if s >= e:
            continue  # hors fenêtre ou durée nulle
        first = s // HOUR_US            # 1er bucket avec login_at < fin_bucket
        last = -(-e // HOUR_US) - 1     # dernier bucket avec fin > début_bucket
        for h in range(first, last + 1):
            d, hh = divmod(h, 24)
            grid[d][hh] += 1
    out = []
    for d in range(days):
        out.append({
            "date": (start + timedelta(days=d)).strftime("%Y-%m-%d"),
            "hours": grid[d],
        })
    return out
