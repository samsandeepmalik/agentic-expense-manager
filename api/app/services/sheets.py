"""Transactions and categories on top of Google Sheets.

The spreadsheet is the source of truth:
- Transactions tab: one row per income/expense entry
- Categories tab: Name | Type (income|expense) | Percent (counting formula)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import google_client as gc

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def list_categories() -> list[dict[str, Any]]:
    spreadsheet_id = gc.ensure_spreadsheet()
    sheets = gc.sheets_service()
    rows = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{gc.CATEGORIES_SHEET}!A2:C")
        .execute()
        .get("values", [])
    )
    categories = []
    for row in rows:
        if not row or not row[0].strip():
            continue
        categories.append(
            {
                "name": row[0].strip(),
                "type": (row[1].strip().lower() if len(row) > 1 else "expense"),
                "percent": _to_float(row[2], 100.0) if len(row) > 2 else 100.0,
            }
        )
    return categories


def upsert_category(name: str, type_: str, percent: float) -> dict[str, Any]:
    spreadsheet_id = gc.ensure_spreadsheet()
    sheets = gc.sheets_service()
    existing = list_categories()
    type_ = type_.lower()
    if type_ not in ("income", "expense"):
        raise ValueError("Category type must be 'income' or 'expense'")
    percent = max(0.0, min(float(percent), 100.0))

    for index, category in enumerate(existing):
        if category["name"].lower() == name.strip().lower():
            row_number = index + 2  # 1-based + header row
            sheets.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{gc.CATEGORIES_SHEET}!A{row_number}:C{row_number}",
                valueInputOption="RAW",
                body={"values": [[name.strip(), type_, percent]]},
            ).execute()
            return {"name": name.strip(), "type": type_, "percent": percent}

    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{gc.CATEGORIES_SHEET}!A:C",
        valueInputOption="RAW",
        body={"values": [[name.strip(), type_, percent]]},
    ).execute()
    return {"name": name.strip(), "type": type_, "percent": percent}


def delete_category(name: str) -> bool:
    spreadsheet_id = gc.ensure_spreadsheet()
    sheets = gc.sheets_service()
    existing = list_categories()
    for index, category in enumerate(existing):
        if category["name"].lower() == name.strip().lower():
            sheet_id = _sheet_id(sheets, spreadsheet_id, gc.CATEGORIES_SHEET)
            row_number = index + 1  # 0-based data row -> +1 header
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "requests": [
                        {
                            "deleteDimension": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "dimension": "ROWS",
                                    "startIndex": row_number,
                                    "endIndex": row_number + 1,
                                }
                            }
                        }
                    ]
                },
            ).execute()
            return True
    return False


def category_percent(name: str) -> float:
    for category in list_categories():
        if category["name"].lower() == name.strip().lower():
            return category["percent"]
    return 100.0


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


def record_transaction(
    *,
    date: str,
    type_: str,
    category: str,
    description: str = "",
    merchant: str = "",
    amount: float,
    gst: float = 0.0,
    qst: float = 0.0,
    total: float | None = None,
    image_link: str = "",
    source: str = "ui",
) -> dict[str, Any]:
    """Append a transaction row. Applies the category percent formula to
    compute the counted amount."""
    spreadsheet_id = gc.ensure_spreadsheet()
    sheets = gc.sheets_service()

    type_ = type_.lower()
    if type_ not in ("income", "expense"):
        raise ValueError("Transaction type must be 'income' or 'expense'")

    if total is None:
        total = round(amount + gst + qst, 2)
    percent = category_percent(category)
    counted = round(total * percent / 100.0, 2)
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    row = [
        date, type_, category, description, merchant,
        amount, gst, qst, total, counted,
        image_link, source, recorded_at,
    ]
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{gc.TRANSACTIONS_SHEET}!A:M",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()

    return {
        "date": date,
        "type": type_,
        "category": category,
        "description": description,
        "merchant": merchant,
        "amount": amount,
        "gst": gst,
        "qst": qst,
        "total": total,
        "counted": counted,
        "percent_applied": percent,
        "image_link": image_link,
        "source": source,
        "recorded_at": recorded_at,
        "sheet_url": gc.spreadsheet_url(),
    }


def list_transactions(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    type_: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    spreadsheet_id = gc.ensure_spreadsheet()
    sheets = gc.sheets_service()
    rows = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{gc.TRANSACTIONS_SHEET}!A2:M")
        .execute()
        .get("values", [])
    )

    transactions = []
    for row in rows:
        if not row or not (row[0] if len(row) > 0 else "").strip():
            continue
        row = row + [""] * (13 - len(row))
        transaction = {
            "date": row[0],
            "type": row[1].lower(),
            "category": row[2],
            "description": row[3],
            "merchant": row[4],
            "amount": _to_float(row[5]),
            "gst": _to_float(row[6]),
            "qst": _to_float(row[7]),
            "total": _to_float(row[8]),
            "counted": _to_float(row[9]),
            "image_link": row[10],
            "source": row[11],
            "recorded_at": row[12],
        }
        if start_date and transaction["date"] < start_date:
            continue
        if end_date and transaction["date"] > end_date:
            continue
        if type_ and transaction["type"] != type_.lower():
            continue
        if category and transaction["category"].lower() != category.lower():
            continue
        transactions.append(transaction)
    return transactions


def summarize(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Aggregate totals, by-category breakdown and monthly trend."""
    transactions = list_transactions(start_date=start_date, end_date=end_date)

    income = sum(t["counted"] for t in transactions if t["type"] == "income")
    expenses = sum(t["counted"] for t in transactions if t["type"] == "expense")

    by_category: dict[str, float] = {}
    for t in transactions:
        if t["type"] == "expense":
            by_category[t["category"]] = round(
                by_category.get(t["category"], 0.0) + t["counted"], 2
            )

    monthly: dict[str, dict[str, float]] = {}
    for t in transactions:
        month = (t["date"] or "")[:7]  # YYYY-MM
        if not month:
            continue
        bucket = monthly.setdefault(month, {"income": 0.0, "expenses": 0.0})
        key = "income" if t["type"] == "income" else "expenses"
        bucket[key] = round(bucket[key] + t["counted"], 2)

    trend = [
        {"month": month, **values} for month, values in sorted(monthly.items())
    ]

    return {
        "income": round(income, 2),
        "expenses": round(expenses, 2),
        "net": round(income - expenses, 2),
        "by_category": by_category,
        "trend": trend,
        "count": len(transactions),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace("$", "").replace(",", "").strip() or default)
    except (TypeError, ValueError):
        return default


def _sheet_id(sheets, spreadsheet_id: str, title: str) -> int:
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == title:
            return sheet["properties"]["sheetId"]
    raise LookupError(f"Sheet '{title}' not found")
