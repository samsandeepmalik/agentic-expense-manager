"""Append-only audit log: which channel did what to which record."""

from __future__ import annotations

import sqlite3


def record(conn: sqlite3.Connection, event: str, *, channel: str = "",
           ref: str = "", detail: str = "", profile_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log(channel, event, ref, detail, profile_id) "
        "VALUES (?,?,?,?,?)",
        (channel, event, ref, detail[:1000], profile_id),
    )


def recent(conn: sqlite3.Connection, limit: int = 100,
           profile_id: int | None = None) -> list[dict]:
    if profile_id is None:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    else:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE profile_id=? OR profile_id IS NULL "
            "ORDER BY id DESC LIMIT ?", (profile_id, limit))
    return [dict(r) for r in rows]
