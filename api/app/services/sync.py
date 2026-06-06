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
from ..settings_keys import LAST_SYNC_ERROR, SPREADSHEET_ID
from . import google_client as gc

logger = logging.getLogger(__name__)

SHEET_NAME = "Transactions"
SHEET_HEADERS = ["ID", "Date", "Type", "Category", "Description", "Merchant",
                 "Amount", "Taxes", "Total", "Counted", "Image Link", "Source", "Loan"]


def sync_enabled() -> bool:
    return gc.is_connected()


def _ensure_spreadsheet(conn: sqlite3.Connection) -> str:
    spreadsheet_id = get_setting(conn, SPREADSHEET_ID)
    sheets = gc.sheets_service()
    if not spreadsheet_id:
        created = sheets.spreadsheets().create(
            body={"properties": {"title": "Expense Manager"},
                  "sheets": [{"properties": {"title": SHEET_NAME}}]},
            fields="spreadsheetId").execute()
        spreadsheet_id = created["spreadsheetId"]
        set_setting(conn, SPREADSHEET_ID, spreadsheet_id)
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A1",
            valueInputOption="RAW", body={"values": [SHEET_HEADERS]}).execute()
    return spreadsheet_id


def _txn_row(txn: dict, image_link: str) -> list:
    return [txn["id"], txn["date"], txn["type"], txn["category"],
            txn["description"], txn["merchant"], txn["amount"],
            json.dumps(txn["tax_breakdown"]), txn["total"], txn["counted"],
            image_link, txn["source"], "yes" if txn.get("loan") else ""]


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
                    spreadsheetId=spreadsheet_id, range=f"{SHEET_NAME}!A:M",
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
                    range=f"{SHEET_NAME}!A{row_number}:M{row_number}",
                    valueInputOption="RAW",
                    body={"values": [["(deleted)"] + [""] * 12]}).execute()
        return {"synced": pushed}


def _maybe_upload_receipt(conn, txn: dict) -> str:
    if txn.get("receipt_link"):
        return txn["receipt_link"]
    if not txn["image_path"] or not Path(txn["image_path"]).exists():
        return ""
    data = Path(txn["image_path"]).read_bytes()
    suffix = Path(txn["image_path"]).suffix.lstrip(".") or "jpg"
    link = gc.upload_receipt_image(Path(txn["image_path"]).name, data, f"image/{suffix}")
    conn.execute("UPDATE transactions SET receipt_link=? WHERE id=?",
                 (link, txn["id"]))
    return link


_loop: asyncio.AbstractEventLoop | None = None
_dirty: asyncio.Event | None = None


def request_sync() -> None:
    """Thread-safe dirty flag. Callable from any thread (agent tools run in
    worker threads); the worker coalesces bursts into one reconcile."""
    if _loop is None or _dirty is None or _loop.is_closed():
        return  # worker not running (tests, scripts) — hourly reconcile covers it
    try:
        _loop.call_soon_threadsafe(_dirty.set)
    except RuntimeError:
        pass  # loop shutting down


async def sync_worker(debounce: float = 2.0) -> None:
    """Long-lived task: wait for dirty flag, debounce, reconcile once."""
    global _loop, _dirty
    _loop = asyncio.get_running_loop()
    _dirty = asyncio.Event()
    try:
        while True:
            await _dirty.wait()
            await asyncio.sleep(debounce)
            _dirty.clear()
            if sync_enabled():
                await asyncio.to_thread(_safe_reconcile)
    finally:
        _loop = None
        _dirty = None


def _safe_reconcile() -> None:
    from .audit import record
    try:
        result = reconcile()
        with get_db() as conn:
            set_setting(conn, LAST_SYNC_ERROR, None)
            if result.get("synced"):
                record(conn, "sync_pushed", channel="sync",
                       detail=f"{result['synced']} rows")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sync push failed; will retry on next reconcile")
        with get_db() as conn:
            set_setting(conn, LAST_SYNC_ERROR, str(exc))
            record(conn, "sync_failed", channel="sync", detail=str(exc))


def status() -> dict:
    with get_db() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE sync_status='pending'"
        ).fetchone()["c"]
        spreadsheet_id = get_setting(conn, SPREADSHEET_ID)
        last_error = get_setting(conn, LAST_SYNC_ERROR)
    return {"enabled": sync_enabled(), "pending": pending,
            "last_error": last_error,
            "sheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
                         if spreadsheet_id else None}
