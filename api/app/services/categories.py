"""Categories (+ percent, taxable, budget) and tax profiles."""

from __future__ import annotations

import json
import sqlite3

from ..errors import AppError


def list_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM categories ORDER BY type, name").fetchall()
    return [dict(r) | {"taxable": bool(r["taxable"])} for r in rows]


def upsert_category(conn, name: str, type_: str, percent: float,
                    taxable: bool, budget_monthly: float | None) -> dict:
    if type_ not in ("income", "expense"):
        raise AppError("invalid_type", "Category type must be income or expense")
    percent = max(0.0, min(float(percent), 100.0))
    conn.execute(
        """INSERT INTO categories(name, type, percent, taxable, budget_monthly)
           VALUES (?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET type=excluded.type,
             percent=excluded.percent, taxable=excluded.taxable,
             budget_monthly=excluded.budget_monthly""",
        (name.strip(), type_, percent, int(taxable), budget_monthly),
    )
    row = conn.execute("SELECT * FROM categories WHERE name=?", (name.strip(),)).fetchone()
    return dict(row) | {"taxable": bool(row["taxable"])}


def delete_category(conn, category_id: int) -> None:
    used = conn.execute(
        "SELECT COUNT(*) c FROM transactions WHERE category_id=?", (category_id,)
    ).fetchone()["c"]
    if used:
        raise AppError("category_in_use",
                       f"Category has {used} transactions; recategorize them first", 409)
    conn.execute("DELETE FROM categories WHERE id=?", (category_id,))


def get_category(conn, category_id: int) -> dict:
    row = conn.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
    if not row:
        raise AppError("category_not_found", "Category not found", 404)
    return dict(row) | {"taxable": bool(row["taxable"])}


def find_category_by_name(conn, name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM categories WHERE lower(name)=lower(?)", (name.strip(),)
    ).fetchone()
    return (dict(row) | {"taxable": bool(row["taxable"])}) if row else None


def list_tax_profiles(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM tax_profiles ORDER BY id").fetchall()
    return [dict(r) | {"components": json.loads(r["components"]),
                       "is_active": bool(r["is_active"])} for r in rows]


def save_tax_profile(conn, name: str, components: list[dict],
                     activate: bool) -> dict:
    for component in components:
        if not component.get("name") or not isinstance(component.get("rate"), (int, float)):
            raise AppError("invalid_component", "Each component needs name and rate")
    conn.execute(
        """INSERT INTO tax_profiles(name, components) VALUES (?,?)
           ON CONFLICT(name) DO UPDATE SET components=excluded.components""",
        (name.strip(), json.dumps(components)),
    )
    if activate:
        conn.execute("UPDATE tax_profiles SET is_active=0")
        conn.execute("UPDATE tax_profiles SET is_active=1 WHERE name=?", (name.strip(),))
    return [p for p in list_tax_profiles(conn) if p["name"] == name.strip()][0]
