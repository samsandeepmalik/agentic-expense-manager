"""Categories (+ percent, taxable, budget) and tax profiles."""

from __future__ import annotations

import json
import sqlite3

from ..errors import AppError
from . import profiles as prof_svc


def list_categories(conn: sqlite3.Connection,
                    profile_id: int | None = None) -> list[dict]:
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    rows = conn.execute(
        "SELECT * FROM categories WHERE profile_id=? ORDER BY type, parent_id, name",
        (pid,)).fetchall()
    return [dict(r) | {"taxable": bool(r["taxable"])} for r in rows]


def upsert_category(conn, name: str, type_: str, percent: float,
                    taxable: bool, budget_monthly: float | None,
                    parent_id: int = 0, profile_id: int | None = None) -> dict:
    if type_ not in ("income", "expense"):
        raise AppError("invalid_type", "Category type must be income or expense")
    percent = max(0.0, min(float(percent), 100.0))
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    conn.execute(
        """INSERT INTO categories(name, type, percent, taxable, budget_monthly,
             parent_id, profile_id)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(name, profile_id, parent_id) DO UPDATE SET type=excluded.type,
             percent=excluded.percent, taxable=excluded.taxable,
             budget_monthly=excluded.budget_monthly""",
        (name.strip(), type_, percent, int(taxable), budget_monthly,
         int(parent_id), pid),
    )
    row = conn.execute(
        "SELECT * FROM categories WHERE name=? AND profile_id=? AND parent_id=?",
        (name.strip(), pid, int(parent_id))).fetchone()
    return dict(row) | {"taxable": bool(row["taxable"])}


def update_category(conn, category_id: int, *, parent_id: int,
                    profile_id: int | None = None) -> dict:
    """Re-parent a category by id (0 = promote to top-level). Enforces a single
    level of nesting: the target parent must itself be top-level, and a category
    that has children cannot become a child."""
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    row = conn.execute(
        "SELECT * FROM categories WHERE id=? AND profile_id=?",
        (category_id, pid)).fetchone()
    if not row:
        raise AppError("category_not_found", "Category not found", 404)
    parent_id = int(parent_id)
    if parent_id != 0:
        if parent_id == category_id:
            raise AppError("invalid_parent", "A category cannot be its own parent", 409)
        parent = conn.execute(
            "SELECT parent_id FROM categories WHERE id=? AND profile_id=?",
            (parent_id, pid)).fetchone()
        if not parent:
            raise AppError("invalid_parent", "Parent category not found", 404)
        if parent["parent_id"] != 0:
            raise AppError("invalid_parent", "Sub-categories are only one level deep", 409)
        kids = conn.execute(
            "SELECT COUNT(*) c FROM categories WHERE parent_id=? AND profile_id=?",
            (category_id, pid)).fetchone()["c"]
        if kids:
            raise AppError("has_children",
                           "Promote this category's sub-categories first", 409)
    try:
        conn.execute("UPDATE categories SET parent_id=? WHERE id=? AND profile_id=?",
                     (parent_id, category_id, pid))
    except sqlite3.IntegrityError:
        raise AppError("category_exists",
                       "A category with that name already exists under that parent", 409)
    return get_category(conn, category_id)


def delete_category(conn, category_id: int, profile_id: int | None = None) -> None:
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    owned = conn.execute(
        "SELECT 1 FROM categories WHERE id=? AND profile_id=?", (category_id, pid)
    ).fetchone()
    if not owned:
        raise AppError("category_not_found", "Category not found", 404)
    used = conn.execute(
        "SELECT COUNT(*) c FROM transactions WHERE category_id=? AND profile_id=?",
        (category_id, pid)).fetchone()["c"]
    if used:
        raise AppError("category_in_use",
                       f"Category has {used} transactions; recategorize them first", 409)
    conn.execute("DELETE FROM categories WHERE id=? AND profile_id=?",
                 (category_id, pid))


def get_category(conn, category_id: int) -> dict:
    row = conn.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
    if not row:
        raise AppError("category_not_found", "Category not found", 404)
    return dict(row) | {"taxable": bool(row["taxable"])}


def find_category_by_name(conn, name: str, profile_id: int | None = None,
                          parent_id: int | None = None) -> dict | None:
    """Resolve a category by name within a profile.

    A name can legitimately exist twice — once top-level and once as a
    sub-category (UNIQUE is on name+profile+parent_id). Pass `parent_id` to
    disambiguate. Without it, an ambiguous name raises rather than silently
    resolving to an arbitrary row (which previously caused wrong taxes/counted).
    """
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    sql = "SELECT * FROM categories WHERE lower(name)=lower(?) AND profile_id=?"
    params: list = [name.strip(), pid]
    if parent_id is not None:
        sql += " AND parent_id=?"
        params.append(int(parent_id))
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise AppError(
            "ambiguous_category",
            f"'{name}' exists both as a top-level category and a sub-category; "
            "select it precisely (by id) so the right tax/percent is used.", 409)
    row = rows[0]
    return dict(row) | {"taxable": bool(row["taxable"])}


def child_ids(conn, category_id: int, profile_id: int | None = None) -> list[int]:
    """Direct children of a category (one level of nesting)."""
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    return [r["id"] for r in conn.execute(
        "SELECT id FROM categories WHERE parent_id=? AND profile_id=?",
        (category_id, pid))]


def list_tax_profiles(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM tax_profiles WHERE profile_id=? ORDER BY id",
                        (prof_svc.active_id(conn),)).fetchall()
    return [dict(r) | {"components": json.loads(r["components"]),
                       "is_active": bool(r["is_active"])} for r in rows]


def save_tax_profile(conn, name: str, components: list[dict],
                     activate: bool) -> dict:
    for component in components:
        if not component.get("name") or not isinstance(component.get("rate"), (int, float)):
            raise AppError("invalid_component", "Each component needs name and rate")
    pid = prof_svc.active_id(conn)
    conn.execute(
        """INSERT INTO tax_profiles(name, components, profile_id) VALUES (?,?,?)
           ON CONFLICT(name, profile_id) DO UPDATE SET components=excluded.components""",
        (name.strip(), json.dumps(components), pid),
    )
    if activate:
        conn.execute("UPDATE tax_profiles SET is_active=0 WHERE profile_id=?", (pid,))
        conn.execute("UPDATE tax_profiles SET is_active=1 WHERE name=? AND profile_id=?",
                     (name.strip(), pid))
    return [p for p in list_tax_profiles(conn) if p["name"] == name.strip()][0]
