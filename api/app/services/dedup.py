"""Duplicate detection — the single match rule for the whole app.

A transaction is a likely duplicate of an existing one in the same profile when
EITHER its receipt_link exactly matches (strong signal — a re-shared receipt),
OR it has the same total + merchant (case-insensitive) + date (exact day).
Used by the import review grid (flag_duplicates) and the create-transaction
warn-gate (find_duplicate). Pure reads — no writes."""

from __future__ import annotations

import sqlite3

from . import profiles as prof_svc


def find_duplicate(conn: sqlite3.Connection, data: dict,
                   profile_id: int | None = None) -> dict | None:
    """Return {"txn": <existing row dict>, "reason": "receipt"|"fields"} for the
    first existing transaction that looks like a duplicate of `data`, else None."""
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)

    link = (data.get("receipt_link") or "").strip()
    if link:
        hit = conn.execute(
            "SELECT * FROM transactions WHERE profile_id=? AND receipt_link=? LIMIT 1",
            (pid, link),
        ).fetchone()
        if hit is not None:
            return {"txn": dict(hit), "reason": "receipt"}

    total = round(float(data["total"]), 2)
    merchant = (data.get("merchant") or "").strip()
    hit = conn.execute(
        "SELECT * FROM transactions WHERE profile_id=? AND total=? "
        "AND lower(merchant)=lower(?) AND date=? LIMIT 1",
        (pid, total, merchant, data["date"]),
    ).fetchone()
    if hit is not None:
        return {"txn": dict(hit), "reason": "fields"}
    return None


def flag_duplicates(conn: sqlite3.Connection, rows: list[dict],
                    profile_id: int | None = None) -> list[bool]:
    """Per-row duplicate flags for the import review grid, on the same rule."""
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    return [find_duplicate(conn, row, pid) is not None for row in rows]
