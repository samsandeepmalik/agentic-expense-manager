"""Append-only audit log: which channel did what to which record."""

from __future__ import annotations

import sqlite3


def record(conn: sqlite3.Connection, event: str, *, channel: str = "",
           ref: str = "", detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log(channel, event, ref, detail) VALUES (?,?,?,?)",
        (channel, event, ref, detail[:1000]),
    )


def recent(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]
