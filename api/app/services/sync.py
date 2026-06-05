"""One-way sync: app SQLite → Google Sheet + Drive. Never reads data back.

Sheet gets an ID column (app transaction id) making reconcile idempotent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

from ..db import get_db, get_setting, set_setting
from . import google_client as gc

logger = logging.getLogger(__name__)

SHEET_NAME = "Transactions"
SHEET_HEADERS = ["ID", "Date", "Type", "Category", "Description", "Merchant",
                 "Amount", "Taxes", "Total", "Counted", "Image Link", "Source"]


def sync_enabled() -> bool:
    return gc.is_connected()


def _ensure_spreadsheet(conn: sqlite3.Connection) -> str:
    spreadsheet_id = get_setting(conn, "spreadsheet_id")
    sheets = gc.sheets_service()
    if not spreadsheet_id:
        created = sheets.spreadsheets().create(
            body={"properties": {"title": "Expense Manager"},
                  "sheets": [{"properties": {"title": SHEET_NAME}}]},
            fields="spreadsheetId").execute()
        spreadsheet_id = created["spreadsheetId"]
        set_setting(conn, "spreadsheet_id", spreadsheet_id)
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A1",
            valueInputOption="RAW", body={"values": [SHEET_HEADERS]}).execute()
    return spreadsheet_id


def _txn_row(txn: dict, image_link: str) -> list:
    return [txn["id"], txn["date"], txn["type"], txn["category"],
            txn["description"], txn["merchant"], txn["amount"],
            json.dumps(txn["tax_breakdown"]), txn["total"], txn["counted"],
            image_link, txn["source"]]


def _sheet_ids(sheets, spreadsheet_id: str) -> dict[int, int]:
    """Map app txn id → sheet row number (2-based)."""
    values = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A2:A").execute().get("values", [])
    mapping = {}
    for index, row in enumerate(values):
        if row and str(row[0]).isdigit():
            mapping[int(row[0])] = index + 2
    return mapping


def reconcile() -> dict:
    """Push all pending/missing transactions. Safe to run repeatedly."""
    if not sync_enabled():
        return {"synced": 0, "skipped": "google_not_connected"}
    from .transactions import list_transactions

    with get_db() as conn:
        spreadsheet_id = _ensure_spreadsheet(conn)
        sheets = gc.sheets_service()
        existing = _sheet_ids(sheets, spreadsheet_id)
        txns = list_transactions(conn, limit=100000)
        pushed = 0
        for txn in txns:
            needs = txn["sync_status"] == "pending" or txn["id"] not in existing
            if not needs:
                continue
            image_link = _maybe_upload_receipt(conn, txn)
            row = _txn_row(txn, image_link)
            if txn["id"] in existing:
                sheets.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"{SHEET_NAME}!A{existing[txn['id']]}",
                    valueInputOption="USER_ENTERED", body={"values": [row]}).execute()
            else:
                sheets.spreadsheets().values().append(
                    spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A:L",
                    valueInputOption="USER_ENTERED", body={"values": [row]}).execute()
            conn.execute("UPDATE transactions SET sync_status='synced' WHERE id=?",
                         (txn["id"],))
            pushed += 1
        # Deletions: ids in sheet but not in app
        app_ids = {t["id"] for t in txns}
        for missing_id, row_number in sorted(existing.items(), reverse=True):
            if missing_id not in app_ids:
                sheets.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"{SHEET_NAME}!A{row_number}:L{row_number}",
                    valueInputOption="RAW",
                    body={"values": [["(deleted)"] + [""] * 11]}).execute()
        return {"synced": pushed}


def _maybe_upload_receipt(conn, txn: dict) -> str:
    link = get_setting(conn, f"receipt_link_{txn['id']}")
    if link:
        return link
    if not txn["image_path"] or not Path(txn["image_path"]).exists():
        return ""
    data = Path(txn["image_path"]).read_bytes()
    suffix = Path(txn["image_path"]).suffix.lstrip(".") or "jpg"
    link = gc.upload_receipt_image(Path(txn["image_path"]).name, data, f"image/{suffix}")
    set_setting(conn, f"receipt_link_{txn['id']}", link)
    return link


def schedule_push(txn_id: int) -> None:
    """Background push of one transaction (called after writes)."""
    if not sync_enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(asyncio.to_thread(_safe_reconcile))


def _safe_reconcile() -> None:
    try:
        reconcile()
    except Exception:  # noqa: BLE001
        logger.exception("Sync push failed; will retry on next reconcile")


def status() -> dict:
    with get_db() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE sync_status='pending'"
        ).fetchone()["c"]
        spreadsheet_id = get_setting(conn, "spreadsheet_id")
    return {"enabled": sync_enabled(), "pending": pending,
            "sheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
                         if spreadsheet_id else None}
