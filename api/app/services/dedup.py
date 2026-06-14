"""Duplicate detection for imports: same total, date within ±1 day, same profile."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import profiles as prof_svc


def flag_duplicates(conn: sqlite3.Connection, rows: list[dict],
                    profile_id: int | None = None) -> list[bool]:
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    flags = []
    for row in rows:
        day = date.fromisoformat(row["date"])
        low = (day - timedelta(days=1)).isoformat()
        high = (day + timedelta(days=1)).isoformat()
        hit = conn.execute(
            "SELECT 1 FROM transactions "
            "WHERE total=? AND date BETWEEN ? AND ? AND profile_id=? LIMIT 1",
            (round(float(row["total"]), 2), low, high, pid),
        ).fetchone()
        flags.append(hit is not None)
    return flags
