"""Transaction CRUD, filters, bulk ops, CSV — all money math lives here."""

from __future__ import annotations

import csv
import io
import json
import sqlite3

from ..errors import AppError
from . import audit
from . import categories as cat_svc
from . import tax as tax_svc

COLUMNS = ["id", "date", "type", "category_id", "description", "merchant",
           "amount", "tax_breakdown", "total", "counted", "image_path",
           "source", "external_ref", "sync_status", "loan", "created_at", "updated_at"]


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
        "SELECT name FROM categories WHERE id=?", (txn["category_id"],)
    ).fetchone()
    txn["category"] = category["name"] if category else "?"
    return txn


def _compute(conn, category: dict, total: float) -> dict:
    components = tax_svc.active_components(conn)
    calc = tax_svc.back_calculate(total, components, bool(category["taxable"]))
    counted = round(total * category["percent"] / 100, 2)
    return {"amount": calc["amount"], "breakdown": calc["breakdown"], "counted": counted}


def create_transaction(conn: sqlite3.Connection, data: dict) -> dict:
    category = cat_svc.find_category_by_name(conn, data["category"])
    if category is None:
        raise AppError("category_not_found", f"Unknown category: {data['category']}", 404)
    if data["type"] not in ("income", "expense"):
        raise AppError("invalid_type", "type must be income or expense")
    total = round(float(data["total"]), 2)
    parts = _compute(conn, category, total)
    cursor = conn.execute(
        """INSERT INTO transactions(date, type, category_id, description, merchant,
           amount, tax_breakdown, total, counted, image_path, source, external_ref,
           sync_status, loan)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (data["date"], data["type"], category["id"], data.get("description", ""),
         data.get("merchant", ""), parts["amount"], json.dumps(parts["breakdown"]),
         total, parts["counted"], data.get("image_path"), data.get("source", "ui"),
         data.get("external_ref"), "pending", int(bool(data.get("loan", False)))),
    )
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (cursor.lastrowid,)).fetchone()
    result = _row_to_dict(conn, row)
    audit.record(conn, "transaction_created", channel=result["source"],
                 ref=str(result["id"]),
                 detail=f"{result['date']} {result['merchant']} ${result['total']}")
    _request_sync()
    return result


def list_transactions(conn, *, start: str | None = None, end: str | None = None,
                      type_: str | None = None, category: str | None = None,
                      q: str | None = None, limit: int = 500, offset: int = 0) -> list[dict]:
    sql = """SELECT t.* FROM transactions t
             JOIN categories c ON c.id = t.category_id WHERE 1=1"""
    params: list = []
    if start:
        sql += " AND t.date >= ?"; params.append(start)
    if end:
        sql += " AND t.date <= ?"; params.append(end)
    if type_:
        sql += " AND t.type = ?"; params.append(type_)
    if category:
        sql += " AND lower(c.name) = lower(?)"; params.append(category)
    if q:
        sql += " AND (t.merchant LIKE ? OR t.description LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY t.date DESC, t.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_row_to_dict(conn, r) for r in conn.execute(sql, params)]


def get_transaction(conn, txn_id: int) -> dict:
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
    if not row:
        raise AppError("transaction_not_found", "Transaction not found", 404)
    return _row_to_dict(conn, row)


def update_transaction(conn, txn_id: int, changes: dict) -> dict:
    current = get_transaction(conn, txn_id)
    merged = current | changes
    if "category" in changes:
        category = cat_svc.find_category_by_name(conn, changes["category"])
        if category is None:
            raise AppError("category_not_found", "Unknown category", 404)
        merged["category_id"] = category["id"]
    else:
        category = cat_svc.get_category(conn, merged["category_id"])
    parts = _compute(conn, category, round(float(merged["total"]), 2))
    conn.execute(
        """UPDATE transactions SET date=?, type=?, category_id=?, description=?,
           merchant=?, amount=?, tax_breakdown=?, total=?, counted=?, loan=?,
           sync_status='pending', updated_at=datetime('now') WHERE id=?""",
        (merged["date"], merged["type"], merged["category_id"], merged["description"],
         merged["merchant"], parts["amount"], json.dumps(parts["breakdown"]),
         round(float(merged["total"]), 2), parts["counted"],
         int(bool(merged.get("loan", False))), txn_id),
    )
    result = get_transaction(conn, txn_id)
    audit.record(conn, "transaction_updated", channel=result["source"],
                 ref=str(txn_id), detail=f"total ${result['total']}")
    _request_sync()
    return result


def delete_transaction(conn, txn_id: int) -> None:
    conn.execute("DELETE FROM transactions WHERE id=?", (txn_id,))
    audit.record(conn, "transaction_deleted", ref=str(txn_id))
    _request_sync()


def bulk_action(conn, ids: list[int], action: str, category: str | None = None) -> int:
    if action == "delete":
        conn.executemany("DELETE FROM transactions WHERE id=?", [(i,) for i in ids])
        _request_sync()
        return len(ids)
    if action == "recategorize":
        target = cat_svc.find_category_by_name(conn, category or "")
        if target is None:
            raise AppError("category_not_found", "Unknown category", 404)
        for txn_id in ids:
            update_transaction(conn, txn_id, {"category": target["name"]})
        return len(ids)
    raise AppError("invalid_action", f"Unknown bulk action: {action}")


def export_csv(conn) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "date", "type", "category", "description", "merchant",
                     "amount", "taxes", "total", "counted", "source"])
    for txn in list_transactions(conn, limit=100000):
        writer.writerow([txn["id"], txn["date"], txn["type"], txn["category"],
                         txn["description"], txn["merchant"], txn["amount"],
                         json.dumps(txn["tax_breakdown"]), txn["total"],
                         txn["counted"], txn["source"]])
    return buffer.getvalue()


def dashboard_data(conn, period: str | None) -> dict:
    from datetime import date
    from .periods import resolve_period, _shift_month, _month_bounds

    start, end = resolve_period(period)
    txns = list_transactions(conn, start=start, end=end, limit=100000)

    income = round(sum(t["counted"] for t in txns if t["type"] == "income"), 2)
    expenses = round(sum(t["counted"] for t in txns if t["type"] == "expense"), 2)

    by_category: dict[str, float] = {}
    for t in txns:
        if t["type"] == "expense":
            by_category[t["category"]] = round(by_category.get(t["category"], 0) + t["counted"], 2)

    # Trend: 6 months ending at the period's end month
    end_year, end_month = int(end[:4]), int(end[5:7])
    trend = []
    for delta in range(-5, 1):
        year, month = _shift_month(end_year, end_month, delta)
        month_start, month_end = _month_bounds(year, month)
        month_txns = list_transactions(conn, start=month_start, end=month_end, limit=100000)
        trend.append({
            "month": f"{year:04d}-{month:02d}",
            "income": round(sum(t["counted"] for t in month_txns if t["type"] == "income"), 2),
            "expenses": round(sum(t["counted"] for t in month_txns if t["type"] == "expense"), 2),
        })

    budgets = []
    for category in conn.execute(
        "SELECT name, budget_monthly FROM categories "
        "WHERE budget_monthly IS NOT NULL AND type='expense' ORDER BY name"
    ):
        spent = by_category.get(category["name"], 0.0)
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
