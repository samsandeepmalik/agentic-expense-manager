"""Persistent chat sessions and messages (survive restarts)."""

from __future__ import annotations

import json
import sqlite3
import uuid

from ..errors import AppError


def create_session(conn, channel: str = "ui") -> dict:
    session_id = f"{channel}:{uuid.uuid4().hex[:12]}"
    conn.execute("INSERT INTO chat_sessions(id, channel) VALUES (?,?)",
                 (session_id, channel))
    return get_session(conn, session_id)


def get_session(conn, session_id: str) -> dict:
    row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise AppError("session_not_found", "Chat session not found", 404)
    return dict(row)


def ensure_session(conn, session_id: str, channel: str) -> dict:
    row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
    if row:
        return dict(row)
    conn.execute("INSERT INTO chat_sessions(id, channel) VALUES (?,?)",
                 (session_id, channel))
    return get_session(conn, session_id)


def list_sessions(conn, channel: str | None = "ui") -> list[dict]:
    if channel is None:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM chat_sessions ORDER BY updated_at DESC")]
    return [dict(r) for r in conn.execute(
        "SELECT * FROM chat_sessions WHERE channel=? ORDER BY updated_at DESC",
        (channel,))]


def delete_session(conn, session_id: str) -> None:
    conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))


def add_message(conn, session_id: str, role: str, content: dict) -> None:
    conn.execute(
        "INSERT INTO chat_messages(session_id, role, content) VALUES (?,?,?)",
        (session_id, role, json.dumps(content)),
    )
    if role == "user":
        first = conn.execute(
            "SELECT COUNT(*) c FROM chat_messages WHERE session_id=? AND role='user'",
            (session_id,)).fetchone()["c"]
        if first == 1:
            title = (content.get("text") or "Receipt")[:60]
            conn.execute("UPDATE chat_sessions SET title=? WHERE id=?",
                         (title, session_id))
    conn.execute("UPDATE chat_sessions SET updated_at=datetime('now') WHERE id=?",
                 (session_id,))


def list_messages(conn, session_id: str) -> list[dict]:
    return [dict(r) | {"content": json.loads(r["content"])} for r in conn.execute(
        "SELECT * FROM chat_messages WHERE session_id=? ORDER BY id", (session_id,))]
