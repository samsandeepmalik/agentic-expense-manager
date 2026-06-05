"""Duplicate detection for imports: same total, date within ±1 day."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta


def flag_duplicates(conn: sqlite3.Connection, rows: list[dict]) -> list[bool]:
    flags = []
    for row in rows:
        day = date.fromisoformat(row["date"])
        low = (day - timedelta(days=1)).isoformat()
        high = (day + timedelta(days=1)).isoformat()
        hit = conn.execute(
            "SELECT 1 FROM transactions WHERE total=? AND date BETWEEN ? AND ? LIMIT 1",
            (round(float(row["total"]), 2), low, high),
        ).fetchone()
        flags.append(hit is not None)
    return flags
