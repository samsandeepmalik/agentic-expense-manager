"""One-way sync: app SQLite → Google Sheet + Drive. Never reads data back.

Sheet gets an ID column (app transaction id) making reconcile idempotent.
New spreadsheets use year-based tabs (2024, 2025, …); legacy sheets that
already have a "Transactions" tab continue using that single-tab layout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from googleapiclient.errors import HttpError

from ..db import get_db, get_setting, set_setting
from ..settings_keys import (
    LAST_SYNC_AT, LAST_SYNC_COUNT, LAST_SYNC_ERROR, SHEET_COLUMN_CONFIG,
)
from . import google_client as gc

logger = logging.getLogger(__name__)

SHEET_NAME = "Transactions"  # legacy tab name; kept only for backward-compat detection
_YEAR_RE = re.compile(r"^\d{4}$")
_TOTALS_LABEL = "TOTALS"

# --- Column registry ------------------------------------------------------
# Ordered (key, default label, kind). The `tax` key is dynamic: it expands
# into one money column per active tax-component name. Header and row are
# always built from the SAME resolved column list, so there is no positional
# drift when the user reorders or drops columns.
COLUMN_REGISTRY: dict[str, dict] = {
    "id":          {"label": "ID",           "kind": "plain"},
    "date":        {"label": "Date",         "kind": "date"},
    "type":        {"label": "Type",         "kind": "plain"},
    "category":    {"label": "Category",     "kind": "plain"},
    "subcategory": {"label": "Sub-category", "kind": "plain"},
    "description": {"label": "Description",   "kind": "plain"},
    "merchant":    {"label": "Merchant",     "kind": "plain"},
    "amount":      {"label": "Amount",       "kind": "money"},
    "tax":         {"label": "__TAX__",      "kind": "tax"},
    "total":       {"label": "Total",        "kind": "money"},
    "counted_pct": {"label": "Counted %",    "kind": "plain"},
    "counted":     {"label": "Counted",      "kind": "money"},
    "receipt_name": {"label": "Receipt",      "kind": "plain"},
    "receipt_link": {"label": "Receipt Link", "kind": "plain"},
    "source":      {"label": "Source",       "kind": "plain"},
    "loan":        {"label": "Loan",         "kind": "plain"},
    "notes":       {"label": "Notes",        "kind": "plain"},
    "created":     {"label": "Created",      "kind": "plain"},
    "updated":     {"label": "Updated",      "kind": "plain"},
}

# Default sheet: everything except created/updated. This replaces the single
# "Image Link" column with a "Receipt" (name) + "Receipt Link" (url) pair and
# is otherwise the same set as the previous fixed layout.
DEFAULT_COLUMNS: list[str] = [
    k for k in COLUMN_REGISTRY if k not in ("created", "updated")
]


def get_column_config(profile_id: int) -> list[str]:
    """Stored column key order for a profile, or DEFAULT_COLUMNS. Filtered to
    known registry keys; "id" is always forced present and first so the
    incremental id→row map keeps working."""
    with get_db() as conn:
        stored = get_setting(conn, SHEET_COLUMN_CONFIG) or {}
    keys = stored.get(str(profile_id))
    if not keys:
        keys = list(DEFAULT_COLUMNS)
    keys = [k for k in keys if k in COLUMN_REGISTRY]
    keys = [k for k in keys if k != "id"]
    return ["id"] + keys


def set_column_config(profile_id: int, keys: list[str]) -> list[str]:
    """Persist a profile's column key order. Unknown keys are dropped and "id"
    is forced first. Returns the saved (normalised) key list."""
    keys = [k for k in keys if k in COLUMN_REGISTRY and k != "id"]
    saved = ["id"] + keys
    with get_db() as conn:
        stored = dict(get_setting(conn, SHEET_COLUMN_CONFIG) or {})
        stored[str(profile_id)] = saved
        set_setting(conn, SHEET_COLUMN_CONFIG, stored)
    return saved


def _resolve_columns(profile_id: int, tax_cols: list[str]) -> list[dict]:
    """Expand a profile's config into ordered resolved columns. The `tax`
    key expands into one money column per tax-component name (label = the
    tax name). Each entry: {key, label, kind, tax_name}."""
    resolved: list[dict] = []
    for key in get_column_config(profile_id):
        spec = COLUMN_REGISTRY.get(key)
        if not spec:
            continue
        if spec["kind"] == "tax":
            for name in tax_cols:
                resolved.append({"key": "tax", "label": name,
                                 "kind": "money", "tax_name": name})
        else:
            resolved.append({"key": key, "label": spec["label"],
                             "kind": spec["kind"], "tax_name": None})
    return resolved


def _build_headers(cols: list[dict]) -> list[str]:
    return [c["label"] for c in cols]


def _cell(key: str, txn: dict, ctx: dict, tax_name: str | None):
    if key == "id":
        return txn["id"]
    if key == "date":
        return txn["date"]
    if key == "type":
        return txn["type"]
    if key == "category":
        return txn.get("category_parent") or txn["category"]
    if key == "subcategory":
        return txn["category"] if txn.get("category_parent") else ""
    if key == "description":
        return txn.get("description", "")
    if key == "merchant":
        return txn.get("merchant", "")
    if key == "amount":
        return txn["amount"]
    if key == "tax":
        return txn["tax_breakdown"].get(tax_name, "")
    if key == "total":
        return txn["total"]
    if key == "counted_pct":
        pct = txn.get("category_percent", 100)
        return f"{pct:g}%"
    if key == "counted":
        return txn["counted"]
    if key == "receipt_name":
        return ctx.get("receipt_name", "")
    if key == "receipt_link":
        return ctx.get("receipt_link", "")
    if key == "source":
        return txn.get("source", "")
    if key == "loan":
        return "yes" if txn.get("loan") else ""
    if key == "notes":
        return txn.get("notes", "") or ""
    if key == "created":
        return txn.get("created_at", "")
    if key == "updated":
        return txn.get("updated_at", "")
    return ""


def _build_row(txn: dict, cols: list[dict], ctx: dict) -> list:
    return [_cell(c["key"], txn, ctx, c.get("tax_name")) for c in cols]


def _col_letter(n: int) -> str:
    """1-based column index -> spreadsheet letter (1->A, 27->AA)."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _tax_columns(conn, profile_id: int, txns: list) -> list[str]:
    """Tax component names: active tax-profile order first, then any extra
    names found in this profile's transactions (alphabetical). Never empty."""
    names: list[str] = []
    row = conn.execute(
        "SELECT components FROM tax_profiles WHERE is_active=1 AND profile_id=?",
        (profile_id,)).fetchone()
    if row:
        for comp in json.loads(row["components"]):
            if comp["name"] not in names:
                names.append(comp["name"])
    extra = set()
    for txn in txns:
        for key in txn["tax_breakdown"]:
            if key not in names:
                extra.add(key)
    names += sorted(extra)
    return names or ["Tax"]


def sync_enabled() -> bool:
    return gc.is_connected()


def _ensure_spreadsheet(conn: sqlite3.Connection, profile: dict) -> str:
    folder_id = profile.get("drive_folder_id") or gc.ensure_drive_folder(profile)

    if profile["spreadsheet_id"]:
        if gc.is_spreadsheet_alive(profile["spreadsheet_id"]):
            return profile["spreadsheet_id"]
        conn.execute(
            "UPDATE profiles SET spreadsheet_id=NULL, sheet_in_drive=0 WHERE id=?",
            (profile["id"],),
        )
        profile["spreadsheet_id"] = None
        profile["sheet_in_drive"] = 0

    title = f"Expense Manager — {profile['name']}"
    # No id stored — reuse an existing same-named sheet in the folder before
    # creating one, so a DB swap/restore/empty-id doesn't spawn a duplicate.
    existing = gc.find_spreadsheet(title, folder_id)
    if existing:
        conn.execute(
            "UPDATE profiles SET spreadsheet_id=?, sheet_in_drive=1 WHERE id=?",
            (existing, profile["id"]))
        return existing

    year = str(datetime.now().year)
    # Create sheet via Drive API so it lands in the profile folder immediately —
    # no post-creation move needed and no drive.file scope issues with old files.
    sheet_file = gc.drive_create_spreadsheet(title, folder_id)
    spreadsheet_id = sheet_file["id"]
    conn.execute(
        "UPDATE profiles SET spreadsheet_id=?, sheet_in_drive=1 WHERE id=?",
        (spreadsheet_id, profile["id"]),
    )
    cols = _resolve_columns(profile["id"], ["Tax"])
    sheets = gc.sheets_service()
    # Drive API creates a sheet with a default "Sheet1" tab (sheetId=0); rename it.
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": 0, "title": year},
                "fields": "title",
            }
        }]},
    ).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{year}'!A1",
        valueInputOption="RAW",
        body={"values": [_build_headers(cols)]},
    ).execute()
    _format_tab(sheets, spreadsheet_id, 0, cols)
    return spreadsheet_id


def _tab_meta(sheets, spreadsheet_id: str) -> dict[str, int]:
    """Return {tab_title: sheetId} for all tabs."""
    meta = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties"
    ).execute()
    return {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in meta.get("sheets", [])
    }


def _ensure_year_tab(sheets, spreadsheet_id: str, year: int,
                     cols: list[dict]) -> tuple[str, int]:
    """Ensure a tab named after the year exists; keep latest year at index 0.
    Returns (tab_title, sheetId)."""
    tab_title = str(year)
    tabs = _tab_meta(sheets, spreadsheet_id)

    if tab_title not in tabs:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [
                {"addSheet": {"properties": {"title": tab_title, "index": 0}}}
            ]},
        ).execute()
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_title}'!A1",
            valueInputOption="RAW",
            body={"values": [_build_headers(cols)]},
        ).execute()
        tabs = _tab_meta(sheets, spreadsheet_id)
        sheet_id = tabs[tab_title]
        _format_tab(sheets, spreadsheet_id, sheet_id, cols)
        return tab_title, sheet_id
    else:
        year_tabs = sorted([int(t) for t in tabs if _YEAR_RE.match(t)], reverse=True)
        if year_tabs and year_tabs[0] == year:
            meta = sheets.spreadsheets().get(
                spreadsheetId=spreadsheet_id, fields="sheets.properties"
            ).execute()
            sheets_list = meta.get("sheets", [])
            idx = next(
                (i for i, s in enumerate(sheets_list)
                 if s["properties"]["title"] == tab_title),
                None,
            )
            if idx is not None and idx != 0:
                sheets.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"requests": [{"updateSheetProperties": {
                        "properties": {"sheetId": tabs[tab_title], "index": 0},
                        "fields": "index",
                    }}]},
                ).execute()

    return tab_title, tabs[tab_title]


def _sheet_ids_for_tab(sheets, spreadsheet_id: str, tab: str) -> dict[int, int]:
    """Map app txn id → row number for a given tab. Row 1 = header,
    row 2 = frozen TOTALS, so DATA starts at row 3."""
    try:
        values = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{tab}'!A3:A"
        ).execute().get("values", [])
    except Exception:
        return {}
    return {
        int(row[0]): i + 3
        for i, row in enumerate(values)
        if row and str(row[0]).isdigit()
    }


def _totals_row_values(cols: list[dict]) -> list:
    """The frozen TOTALS row (row 2). First cell is the literal "TOTALS"
    (non-numeric → the id→row map skips it); every money column gets an
    OPEN-ENDED =SUM(<col>3:<col>) that auto-extends as data rows are appended
    below — so the totals never need recomputing on add/delete."""
    values: list = []
    for idx, col in enumerate(cols):
        if idx == 0:
            values.append(_TOTALS_LABEL)
        elif col["kind"] == "money":
            letter = _col_letter(idx + 1)
            values.append(f"=SUM({letter}3:{letter})")
        else:
            values.append("")
    return values


def _format_tab(sheets, spreadsheet_id: str, sheet_id: int,
                cols: list[dict]) -> None:
    """Freeze the header + TOTALS rows (top 2), bold the header, colour the
    TOTALS row, and apply currency/date number formats. Money/date columns are
    identified by the RESOLVED column kind (never a hardcoded name)."""
    requests: list[dict] = [
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"frozenRowCount": 2}},
            "fields": "gridProperties.frozenRowCount",
        }},
        {"repeatCell": {     # header row
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.92, "green": 0.92, "blue": 0.92},
            }},
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }},
        {"repeatCell": {     # frozen TOTALS row
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                      "startColumnIndex": 0, "endColumnIndex": len(cols)},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.85, "green": 0.92, "blue": 0.83},
            }},
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }},
    ]
    for idx, col in enumerate(cols):
        if col["kind"] == "money":     # currency on the TOTALS row + data
            requests.append({"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1,
                          "startColumnIndex": idx, "endColumnIndex": idx + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {
                    "type": "CURRENCY", "pattern": "$#,##0.00"}}},
                "fields": "userEnteredFormat.numberFormat",
            }})
        elif col["kind"] == "date":    # date on data rows only (row 3+)
            requests.append({"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 2,
                          "startColumnIndex": idx, "endColumnIndex": idx + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {
                    "type": "DATE", "pattern": "yyyy-mm-dd"}}},
                "fields": "userEnteredFormat.numberFormat",
            }})
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


_SUMMARY_TAB = "Summary"


def _update_summary_tab(sheets, spreadsheet_id: str,
                        year_tabs: list[str], cols: list[dict]) -> None:
    """Ensure a Summary tab with per-column SUM() across all year tabs. Each
    range is OPEN-ENDED from row 3 (data only) so the header (row 1) and the
    frozen TOTALS row (row 2) are never included. Idempotent: clears + rewrites."""
    year_tabs = sorted(t for t in year_tabs if _YEAR_RE.match(t))
    if not year_tabs:
        return
    tabs = _tab_meta(sheets, spreadsheet_id)
    if _SUMMARY_TAB not in tabs:
        # No index: Sheets appends the tab at the end. A hardcoded high index
        # (e.g. 999) is REJECTED by the live API ("new sheet index is too high")
        # — it does not clamp. Appending also keeps Summary clear of
        # _ensure_year_tab's index-0 churn.
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {
                "title": _SUMMARY_TAB}}}]},
        ).execute()

    rows = [["Metric", "Total"]]
    for idx, col in enumerate(cols):
        if col["kind"] != "money":
            continue
        letter = _col_letter(idx + 1)
        formula = "=" + "+".join(
            f"SUM('{yr}'!{letter}3:{letter})" for yr in year_tabs)
        rows.append([col["label"], formula])

    sheets.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{_SUMMARY_TAB}'!A:ZZ").execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{_SUMMARY_TAB}'!A1",
        valueInputOption="USER_ENTERED", body={"values": rows}).execute()


def _row_ctx(conn, txn: dict, profile: dict) -> dict:
    name, link = _maybe_upload_receipt(conn, txn, profile)
    return {"receipt_name": name, "receipt_link": link}


def _reconcile_tab(conn, sheets, spreadsheet_id: str, tab: str,
                   txns: list, profile: dict, cols: list[dict],
                   sheet_id: int) -> int:
    headers = _build_headers(cols)
    last_col = _col_letter(len(headers))

    current = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{tab}'!A1:ZZ2"
    ).execute().get("values", [])
    current_header = current[0] if len(current) >= 1 else []
    totals_present = (len(current) >= 2 and current[1]
                      and str(current[1][0]) == _TOTALS_LABEL)

    if current_header != headers or not totals_present:
        # First write, column-layout change, OR migrating from an older layout
        # without the frozen top TOTALS row: rewrite the whole tab as
        # [header, TOTALS, *data]. The TOTALS SUM is open-ended so it covers all
        # data rows (row 3 down) regardless of how many there are.
        sheets.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=f"'{tab}'!A:ZZ").execute()
        values = [headers, _totals_row_values(cols)]
        for txn in txns:
            values.append(_build_row(txn, cols, _row_ctx(conn, txn, profile)))
            conn.execute(
                "UPDATE transactions SET sync_status='synced' WHERE id=?",
                (txn["id"],))
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"'{tab}'!A1",
            valueInputOption="USER_ENTERED", body={"values": values}).execute()
        _format_tab(sheets, spreadsheet_id, sheet_id, cols)
        return len(txns)

    # Header + TOTALS unchanged: incremental update/append by app txn id. The
    # frozen TOTALS row at row 2 is left untouched — its open-ended SUM picks up
    # appended rows automatically, so there is no strip/recompute step.
    existing = _sheet_ids_for_tab(sheets, spreadsheet_id, tab)
    app_ids = {t["id"] for t in txns}
    pushed = 0
    for txn in txns:
        if txn["sync_status"] != "pending" and txn["id"] in existing:
            continue
        row = _build_row(txn, cols, _row_ctx(conn, txn, profile))
        if txn["id"] in existing:
            sheets.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{tab}'!A{existing[txn['id']]}",
                valueInputOption="USER_ENTERED", body={"values": [row]}).execute()
        else:
            sheets.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range=f"'{tab}'!A:{last_col}",
                valueInputOption="USER_ENTERED", body={"values": [row]}).execute()
        conn.execute(
            "UPDATE transactions SET sync_status='synced' WHERE id=?", (txn["id"],))
        pushed += 1

    # Delete rows whose app txn id is gone. Sort DESCENDING so earlier deletions
    # don't shift the row numbers of later ones.
    to_delete = sorted(
        (rn for txn_id, rn in existing.items() if txn_id not in app_ids),
        reverse=True,
    )
    for row_number in to_delete:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"deleteDimension": {"range": {
                "sheetId": sheet_id, "dimension": "ROWS",
                "startIndex": row_number - 1, "endIndex": row_number,
            }}}]},
        ).execute()

    # The frozen TOTALS row (row 2) needs no update — its open-ended SUM already
    # covers the new data extent after the appends/deletes above.
    return pushed


def _reconcile_profile(profile: dict) -> int:
    """Sync one profile to its Google Sheet. Returns rows pushed."""
    from .transactions import list_transactions

    folder_id = gc.ensure_drive_folder(profile)
    profile["drive_folder_id"] = folder_id

    with get_db() as conn:
        spreadsheet_id = _ensure_spreadsheet(conn, profile)
        sheets = gc.sheets_service()
        tabs = _tab_meta(sheets, spreadsheet_id)
        txns = list_transactions(conn, limit=100000, profile_id=profile["id"])
        tax_cols = _tax_columns(conn, profile["id"], txns)
        cols = _resolve_columns(profile["id"], tax_cols)

        if SHEET_NAME in tabs:
            return _reconcile_tab(
                conn, sheets, spreadsheet_id, SHEET_NAME, txns, profile,
                cols, tabs[SHEET_NAME],
            )
        else:
            txns_by_year: dict[int, list] = defaultdict(list)
            for txn in txns:
                date = txn.get("date") or ""
                if len(date) < 4:
                    logger.warning(
                        "txn id=%s has invalid date %r — skipped from sync",
                        txn["id"], date,
                    )
                    continue
                txns_by_year[int(date[:4])].append(txn)

            existing_year_tabs = {int(t) for t in tabs if _YEAR_RE.match(t)}
            year_tab_titles: list[str] = []
            pushed = 0
            for year in sorted(set(txns_by_year.keys()) | existing_year_tabs):
                tab, sheet_id = _ensure_year_tab(
                    sheets, spreadsheet_id, year, cols)
                year_tab_titles.append(tab)
                pushed += _reconcile_tab(
                    conn, sheets, spreadsheet_id, tab,
                    txns_by_year.get(year, []), profile, cols, sheet_id,
                )
            _update_summary_tab(sheets, spreadsheet_id, year_tab_titles, cols)
            return pushed


def reconcile() -> dict:
    """Push all pending/missing transactions, one spreadsheet per profile."""
    if not sync_enabled():
        return {"synced": 0, "skipped": "google_not_connected"}

    total_pushed = 0
    with get_db() as conn:
        profiles = [dict(r) for r in conn.execute("SELECT * FROM profiles")]

    for profile in profiles:
        try:
            pushed = _reconcile_profile(profile)
            total_pushed += pushed
            with get_db() as conn:
                set_setting(conn, f"sync_error_{profile['id']}", None)
        except Exception as exc:
            logger.error("Sync failed for profile %s: %s", profile["name"], exc)
            with get_db() as conn:
                set_setting(conn, f"sync_error_{profile['id']}", str(exc))

    return {"synced": total_pushed}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:30] or "receipt"


def _receipt_name(txn: dict) -> str:
    """A readable, deterministic receipt name for the sheet's Receipt column."""
    return f"{txn['date']}_{_slug(txn.get('merchant', ''))}"


def _maybe_upload_receipt(conn, txn: dict, profile: dict) -> tuple[str, str]:
    """Return (name, link). When a link already exists but no upload happens,
    the name is derived deterministically. Link persistence is unchanged."""
    if txn.get("receipt_link"):
        return _receipt_name(txn), txn["receipt_link"]
    if not txn["image_path"] or not Path(txn["image_path"]).exists():
        return "", ""
    path = Path(txn["image_path"])
    data = path.read_bytes()
    ext = path.suffix.lstrip(".") or "jpg"
    # Derive the real MIME (e.g. application/pdf) — never hardcode image/<ext>,
    # which produced an invalid "image/pdf" for PDF receipts.
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    filename = f"{txn['date']}_{txn['id']}_{_slug(txn.get('merchant', ''))}.{ext}"
    result = gc.upload_receipt_image(filename, data, mime,
                                     profile=profile, date=txn["date"])
    # upload_receipt_image now returns {"link","name"}; tolerate a bare string
    # for backward compatibility.
    if isinstance(result, dict):
        link = result["link"]
        name = result.get("name") or filename
    else:
        link = result
        name = filename
    conn.execute(
        "UPDATE transactions SET receipt_link=? WHERE id=?", (link, txn["id"])
    )
    return name, link


_loop: asyncio.AbstractEventLoop | None = None
_dirty: asyncio.Event | None = None


def request_sync() -> None:
    """Thread-safe dirty flag. Callable from any thread (agent tools run in
    worker threads); the worker coalesces bursts into one reconcile."""
    if _loop is None or _dirty is None or _loop.is_closed():
        return
    try:
        _loop.call_soon_threadsafe(_dirty.set)
    except RuntimeError:
        pass


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


def _record_success(result: dict) -> None:
    from .audit import record
    with get_db() as conn:
        set_setting(conn, LAST_SYNC_ERROR, None)
        set_setting(conn, LAST_SYNC_AT, datetime.now(timezone.utc).isoformat())
        set_setting(conn, LAST_SYNC_COUNT, result.get("synced", 0))
        if result.get("synced"):
            record(conn, "sync_pushed", channel="sync",
                   detail=f"{result['synced']} rows")


def _safe_reconcile() -> None:
    try:
        result = reconcile()
        _record_success(result)
    except Exception as exc:  # noqa: BLE001
        from .audit import record
        logger.exception("Sync push failed; will retry on next reconcile")
        with get_db() as conn:
            set_setting(conn, LAST_SYNC_ERROR, str(exc))
            record(conn, "sync_failed", channel="sync", detail=str(exc))


def status() -> dict:
    with get_db() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE sync_status='pending'"
        ).fetchone()["c"]
        last_error = get_setting(conn, LAST_SYNC_ERROR)
        last_synced_at = get_setting(conn, LAST_SYNC_AT)
        last_synced_count = get_setting(conn, LAST_SYNC_COUNT)
    return {
        "enabled": sync_enabled(),
        "pending": pending,
        "last_error": last_error,
        "last_synced_at": last_synced_at,
        "last_synced_count": last_synced_count,
    }
