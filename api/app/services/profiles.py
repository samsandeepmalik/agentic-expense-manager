"""Profiles: full data partition (personal / incorporation / other)."""

from __future__ import annotations

import json
import sqlite3

from ..db import DEFAULT_CATEGORIES, TAX_PRESETS, get_setting, set_setting
from ..errors import AppError
from ..settings_keys import ACTIVE_PROFILE


def list_profiles(conn: sqlite3.Connection) -> list[dict]:
    active = active_id(conn)
    return [dict(r) | {"active": r["id"] == active}
            for r in conn.execute("SELECT * FROM profiles ORDER BY id")]


def active_id(conn: sqlite3.Connection) -> int:
    value = get_setting(conn, ACTIVE_PROFILE)
    return int(value) if value else 1


def get_profile(conn, profile_id: int) -> dict:
    row = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        raise AppError("profile_not_found", "Profile not found", 404)
    return dict(row)


def set_active(conn, profile_id: int) -> dict:
    profile = get_profile(conn, profile_id)
    set_setting(conn, ACTIVE_PROFILE, profile_id)
    return profile


def update_profile(conn: sqlite3.Connection, profile_id: int, prompt_loan: bool) -> dict:
    get_profile(conn, profile_id)  # raises profile_not_found if absent
    conn.execute(
        "UPDATE profiles SET prompt_loan=? WHERE id=?",
        (1 if prompt_loan else 0, profile_id),
    )
    return get_profile(conn, profile_id)


def create_profile(conn, name: str, kind: str = "personal") -> dict:
    if kind not in ("personal", "incorporation", "other"):
        raise AppError("invalid_kind", "kind must be personal, incorporation or other")
    if not name.strip():
        raise AppError("invalid_name", "Profile needs a name")
    try:
        cursor = conn.execute("INSERT INTO profiles(name, kind) VALUES (?,?)",
                              (name.strip(), kind))
    except sqlite3.IntegrityError:
        raise AppError("profile_exists", f"Profile '{name}' already exists", 409)
    profile_id = cursor.lastrowid
    conn.executemany(
        "INSERT INTO categories(name, type, taxable, profile_id) VALUES (?,?,?,?)",
        [(n, t, x, profile_id) for n, t, x in DEFAULT_CATEGORIES])
    conn.executemany(
        "INSERT INTO tax_profiles(name, components, is_active, profile_id) "
        "VALUES (?,?,?,?)",
        [(n, json.dumps(c), a, profile_id) for n, c, a in TAX_PRESETS])
    return get_profile(conn, profile_id)


def delete_profile(conn, profile_id: int) -> None:
    if profile_id == active_id(conn):
        raise AppError("profile_active", "Switch away from this profile first", 409)
    used = conn.execute("SELECT COUNT(*) c FROM transactions WHERE profile_id=?",
                        (profile_id,)).fetchone()["c"]
    if used:
        raise AppError("profile_in_use",
                       f"Profile has {used} transactions; export or delete them first", 409)
    conn.execute("DELETE FROM tax_profiles WHERE profile_id=?", (profile_id,))
    conn.execute("DELETE FROM categories WHERE profile_id=?", (profile_id,))
    conn.execute("DELETE FROM recurring_rules WHERE profile_id=?", (profile_id,))
    conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
