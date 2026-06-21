"""Transaction CRUD, filters, bulk ops, CSV — all money math lives here."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3

from ..errors import AppError
from . import audit
from . import categories as cat_svc
from . import dedup
from . import profiles as prof_svc
from . import tax as tax_svc


def _validate_receipt_link(url: str | None) -> None:
    """Reject receipt_link values that aren't http/https URLs."""
    if url is None:
        return
    lower = url.strip().lower()
    if lower and not lower.startswith(("https://", "http://")):
        raise AppError(
            "invalid_receipt_link",
            "receipt_link must be an http:// or https:// URL",
            400,
        )


def _request_sync() -> None:
    """Mark data dirty for the background Google sync worker (lazy import —
    sync imports this module)."""
    from . import sync
    sync.request_sync()


def _row_to_dict(conn, row) -> dict:
    txn = dict(row)
    txn["tax_breakdown"] = json.loads(txn["tax_breakdown"])
    txn["loan"] = bool(txn.get("loan", 0))
    category = conn.execute(
        "SELECT c.name AS name, c.percent AS percent, p.name AS parent_name "
        "FROM categories c LEFT JOIN categories p "
        "  ON p.id = c.parent_id AND c.parent_id != 0 "
        "WHERE c.id = ?", (txn["category_id"],)
    ).fetchone()
    txn["category"] = category["name"] if category else "?"
    txn["category_parent"] = category["parent_name"] if category else None
    txn["category_percent"] = category["percent"] if category else 100
    return txn


def _compute(conn, category: dict, total: float, profile_id: int | None = None) -> dict:
    components = tax_svc.active_components(conn, profile_id)
    calc = tax_svc.back_calculate(total, components, bool(category["taxable"]))
    counted = round(total * category["percent"] / 100, 2)
    return {"amount": calc["amount"], "breakdown": calc["breakdown"], "counted": counted}


def _resolve_category(conn, data: dict, pid: int) -> dict:
    """Resolve the category for a write. Prefer an explicit category_id (precise,
    immune to name collisions between a top-level and a same-named sub-category);
    fall back to name resolution which raises on ambiguity."""
    if data.get("category_id"):
        category = cat_svc.get_category(conn, int(data["category_id"]))
        if category["profile_id"] != pid:
            raise AppError("category_not_found", "Unknown category", 404)
        return category
    name = data.get("category")
    if not name:
        raise AppError("category_not_found", "No category provided", 404)
    category = cat_svc.find_category_by_name(conn, name, profile_id=pid)
    if category is None:
        raise AppError("category_not_found", f"Unknown category: {name}", 404)
    return category


def create_transaction(conn: sqlite3.Connection, data: dict, *,
                       audit_row: bool = True, check_duplicate: bool = False) -> dict:
    pid = data.get("profile_id") or prof_svc.active_id(conn)
    if not str(data.get("date") or "").strip():
        raise AppError("invalid_date", "Transaction date is required", 400)
    if check_duplicate and not data.get("confirm_duplicate"):
        dup = dedup.find_duplicate(conn, data, pid)
        if dup is not None:
            m = dup["txn"]
            raise AppError(
                "duplicate_suspected",
                f"Looks like a duplicate of #{m['id']} "
                f"({m['date']} {m['merchant']} ${m['total']}).",
                409,
                details={"reason": dup["reason"], "txn": {
                    "id": m["id"], "date": m["date"], "merchant": m["merchant"],
                    "total": m["total"]}},
            )
    category = _resolve_category(conn, data, pid)
    _validate_receipt_link(data.get("receipt_link"))
    if data["type"] not in ("income", "expense"):
        raise AppError("invalid_type", "type must be income or expense")
    total = round(float(data["total"]), 2)
    parts = _compute(conn, category, total, profile_id=pid)
    cursor = conn.execute(
        """INSERT INTO transactions(date, type, category_id, description, notes,
           merchant, amount, tax_breakdown, total, counted, image_path, source,
           external_ref, sync_status, loan, receipt_link, profile_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (data["date"], data["type"], category["id"], data.get("description", ""),
         data.get("notes", ""),
         data.get("merchant", ""), parts["amount"], json.dumps(parts["breakdown"]),
         total, parts["counted"], data.get("image_path"), data.get("source", "ui"),
         data.get("external_ref"), "pending", int(bool(data.get("loan", False))),
         data.get("receipt_link"), pid),
    )
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (cursor.lastrowid,)).fetchone()
    result = _row_to_dict(conn, row)
    if audit_row:
        audit.record(conn, "transaction_created", channel=result["source"],
                     ref=str(result["id"]),
                     detail=f"{result['date']} {result['merchant']} ${result['total']}",
                     profile_id=pid)
    _request_sync()
    return result


def preview_transaction(conn: sqlite3.Connection, data: dict) -> dict:
    """Compute the tax/counted breakdown for a prospective transaction without
    persisting it. Lets the UI show a live preview while keeping ALL money math
    server-side (same _compute path create uses)."""
    pid = data.get("profile_id") or prof_svc.active_id(conn)
    category = _resolve_category(conn, data, pid)
    total = round(float(data["total"]), 2)
    parts = _compute(conn, category, total, profile_id=pid)
    return {"amount": parts["amount"], "breakdown": parts["breakdown"],
            "counted": parts["counted"], "total": total}


def list_transactions(conn, *, start: str | None = None, end: str | None = None,
                      type_: str | None = None, category: str | None = None,
                      q: str | None = None, limit: int = 500, offset: int = 0,
                      profile_id: int | None = None) -> list[dict]:
    sql = """SELECT t.* FROM transactions t
             JOIN categories c ON c.id = t.category_id WHERE t.profile_id = ?"""
    params: list = [profile_id if profile_id is not None
                    else prof_svc.active_id(conn)]
    if start:
        sql += " AND t.date >= ?"; params.append(start)
    if end:
        sql += " AND t.date <= ?"; params.append(end)
    if type_:
        sql += " AND t.type = ?"; params.append(type_)
    if category:
        # Match the category itself OR any of its sub-categories, so filtering
        # by a parent name ("Transport") includes "Transport › Petrol" txns —
        # consistent with the dashboard roll-up.
        sql += (" AND (lower(c.name) = lower(?) OR c.parent_id IN"
                " (SELECT id FROM categories WHERE lower(name)=lower(?)"
                "  AND profile_id = t.profile_id))")
        params += [category, category]
    if q:
        sql += " AND (t.merchant LIKE ? OR t.description LIKE ? OR t.notes LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql += " ORDER BY t.date DESC, t.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_row_to_dict(conn, r) for r in conn.execute(sql, params)]


def get_transaction(conn, txn_id: int, profile_id: int | None = None) -> dict:
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    row = conn.execute(
        "SELECT * FROM transactions WHERE id=? AND profile_id=?", (txn_id, pid)
    ).fetchone()
    if not row:
        raise AppError("transaction_not_found", "Transaction not found", 404)
    return _row_to_dict(conn, row)


def update_transaction(conn, txn_id: int, changes: dict,
                       profile_id: int | None = None) -> dict:
    current = get_transaction(conn, txn_id, profile_id)
    if "date" in changes and not str(changes.get("date", "")).strip():
        raise AppError("invalid_date", "Transaction date is required", 400)
    merged = current | changes
    if "receipt_link" in changes:
        _validate_receipt_link(changes.get("receipt_link"))
    pid = current["profile_id"]
    if "category_id" in changes or "category" in changes:
        category = _resolve_category(conn, changes, pid)
        merged["category_id"] = category["id"]
    else:
        category = cat_svc.get_category(conn, merged["category_id"])
    parts = _compute(conn, category, round(float(merged["total"]), 2), profile_id=pid)
    conn.execute(
        """UPDATE transactions SET date=?, type=?, category_id=?, description=?,
           notes=?, merchant=?, amount=?, tax_breakdown=?, total=?, counted=?, loan=?,
           receipt_link=?, sync_status='pending', updated_at=datetime('now') WHERE id=?""",
        (merged["date"], merged["type"], merged["category_id"], merged["description"],
         merged.get("notes", ""),
         merged["merchant"], parts["amount"], json.dumps(parts["breakdown"]),
         round(float(merged["total"]), 2), parts["counted"],
         int(bool(merged.get("loan", False))),
         merged.get("receipt_link"), txn_id),
    )
    result = get_transaction(conn, txn_id, pid)
    audit.record(conn, "transaction_updated", channel=result["source"],
                 ref=str(txn_id), detail=f"total ${result['total']}", profile_id=pid)
    _request_sync()
    return result


def delete_transaction(conn, txn_id: int, profile_id: int | None = None) -> None:
    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    cursor = conn.execute(
        "DELETE FROM transactions WHERE id=? AND profile_id=?", (txn_id, pid))
    if cursor.rowcount == 0:
        raise AppError("transaction_not_found", "Transaction not found", 404)
    audit.record(conn, "transaction_deleted", ref=str(txn_id), profile_id=pid)
    _request_sync()


def reupload_receipt(conn, txn_id: int, profile_id: int | None = None) -> dict:
    """Clear the Drive receipt link so the next sync re-uploads the local file."""
    txn = get_transaction(conn, txn_id, profile_id)
    if not txn.get("image_path") or not os.path.exists(txn["image_path"]):
        raise AppError("no_local_receipt", "No local receipt to re-upload", 422)
    conn.execute(
        "UPDATE transactions SET receipt_link=NULL, sync_status='pending', "
        "updated_at=datetime('now') WHERE id=?", (txn_id,))
    audit.record(conn, "receipt_reupload", channel="ui", ref=str(txn_id),
                 profile_id=txn["profile_id"])
    _request_sync()
    return get_transaction(conn, txn_id, profile_id)


def bulk_action(conn, ids: list[int], action: str, category: str | None = None,
                category_id: int | None = None) -> int:
    if action == "delete":
        pid = prof_svc.active_id(conn)
        deleted = 0
        for i in ids:
            cur = conn.execute(
                "DELETE FROM transactions WHERE id=? AND profile_id=?", (i, pid))
            if cur.rowcount:
                audit.record(conn, "transaction_deleted", ref=str(i), profile_id=pid)
                deleted += 1
        _request_sync()
        return deleted
    if action == "recategorize":
        pid = prof_svc.active_id(conn)
        # Prefer an explicit category_id (precise, collision-proof); fall back to
        # a name only for legacy callers.
        if category_id:
            target = cat_svc.get_category(conn, int(category_id))
            if target["profile_id"] != pid:
                raise AppError("category_not_found", "Unknown category", 404)
        else:
            target = cat_svc.find_category_by_name(conn, category or "", profile_id=pid)
            if target is None:
                raise AppError("category_not_found", "Unknown category", 404)
        owned = [r["id"] for r in conn.execute(
            "SELECT id FROM transactions WHERE profile_id=? AND id IN (%s)"
            % ",".join("?" * len(ids)),
            [pid, *ids])] if ids else []
        for txn_id in owned:
            update_transaction(conn, txn_id, {"category_id": target["id"]})
        return len(owned)
    raise AppError("invalid_action", f"Unknown bulk action: {action}")


def export_csv(conn) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "date", "type", "category", "description", "notes",
                     "merchant", "amount", "taxes", "total", "counted", "source",
                     "loan"])
    for txn in list_transactions(conn, limit=100000):
        writer.writerow([txn["id"], txn["date"], txn["type"], txn["category"],
                         txn["description"], txn.get("notes", ""), txn["merchant"],
                         txn["amount"], json.dumps(txn["tax_breakdown"]), txn["total"],
                         txn["counted"], txn["source"], txn["loan"]])
    return buffer.getvalue()


def dashboard_data(conn, period: str | None,
                   profile_id: int | None = None) -> dict:
    from datetime import date
    from .periods import resolve_period, _shift_month, _month_bounds

    pid = profile_id if profile_id is not None else prof_svc.active_id(conn)
    start, end = resolve_period(period)
    txns = list_transactions(conn, start=start, end=end, limit=100000, profile_id=pid)

    income = round(sum(t["counted"] for t in txns if t["type"] == "income"), 2)
    expenses = round(sum(t["counted"] for t in txns if t["type"] == "expense"), 2)

    # Spend per leaf category id (for budget roll-up), and per top-level name
    # (for the pie — sub-categories roll up into their parent so the chart shows
    # the parent total, not fragmented child slices).
    # Top-level EXPENSE category names — only roll a sub up to its parent when the
    # parent is itself an expense category (an expense child of an income parent
    # must not leak onto the expense pie under the income parent's name).
    expense_tops = {
        r["name"] for r in conn.execute(
            "SELECT name FROM categories WHERE parent_id=0 AND type='expense'"
            " AND profile_id=?", (pid,))
    }
    spend_by_cat_id: dict[int, float] = {}
    by_category: dict[str, float] = {}
    for t in txns:
        if t["type"] != "expense":
            continue
        spend_by_cat_id[t["category_id"]] = round(
            spend_by_cat_id.get(t["category_id"], 0) + t["counted"], 2)
        parent = t.get("category_parent")
        top = parent if parent in expense_tops else t["category"]
        by_category[top] = round(by_category.get(top, 0) + t["counted"], 2)

    # Trend: 6 months ending at the period's end month. One grouped query over
    # the whole 6-month window (sum counted per month+type) instead of six
    # per-month table scans.
    end_year, end_month = int(end[:4]), int(end[5:7])
    first_year, first_month = _shift_month(end_year, end_month, -5)
    trend_start, _ = _month_bounds(first_year, first_month)
    _, trend_end = _month_bounds(end_year, end_month)
    monthly = {(r["ym"], r["t"]): r["total"] for r in conn.execute(
        "SELECT substr(date,1,7) AS ym, type AS t, SUM(counted) AS total "
        "FROM transactions WHERE profile_id=? AND date BETWEEN ? AND ? "
        "GROUP BY ym, t", (pid, trend_start, trend_end))}
    trend = []
    for delta in range(-5, 1):
        year, month = _shift_month(end_year, end_month, delta)
        ym = f"{year:04d}-{month:02d}"
        trend.append({
            "month": ym,
            "income": round(monthly.get((ym, "income"), 0.0), 2),
            "expenses": round(monthly.get((ym, "expense"), 0.0), 2),
        })

    # Categories that carry their own budget — a child with its own budget is
    # tracked on its own line and must NOT also be rolled into its parent's
    # budget (that would double-count the same spend).
    budgeted_ids = {
        r["id"] for r in conn.execute(
            "SELECT id FROM categories WHERE budget_monthly IS NOT NULL"
            " AND type='expense' AND profile_id=?", (pid,))
    }
    budgets = []
    for category in conn.execute(
        "SELECT id, name, budget_monthly FROM categories "
        "WHERE budget_monthly IS NOT NULL AND type='expense' AND profile_id=? "
        "ORDER BY name",
        (pid,),
    ):
        # Spend against a budget = the category's own spend PLUS its
        # sub-categories' spend (a "Food" budget counts "Food › Snacks") —
        # except children that own a budget (counted on their own line).
        ids = [category["id"]] + [
            cid for cid in cat_svc.child_ids(conn, category["id"])
            if cid not in budgeted_ids]
        spent = round(sum(spend_by_cat_id.get(i, 0.0) for i in ids), 2)
        budgets.append({"name": category["name"], "budget": category["budget_monthly"],
                        "spent": spent,
                        "pct": round(100 * spent / category["budget_monthly"], 1)
                               if category["budget_monthly"] else 0})

    return {
        "period": {"start": start, "end": end},
        "metrics": {"income": income, "expenses": expenses,
                    "net": round(income - expenses, 2), "count": len(txns)},
        "by_category": by_category,
        "trend": trend,
        "budgets": budgets,
        "recent": txns[:20],
    }
