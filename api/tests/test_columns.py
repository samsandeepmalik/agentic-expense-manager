"""Configurable Sheet columns + per-tab totals row + receipt name/link."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import register_error_handler
from app.services import sync, transactions as txn_svc
from app.settings_keys import SHEET_COLUMN_CONFIG  # noqa: F401

from test_sync import _fake_sheets


# --- column registry / config helpers ----------------------------------

def test_default_columns_replace_image_link_with_receipt_pair():
    assert "receipt_name" in sync.DEFAULT_COLUMNS
    assert "receipt_link" in sync.DEFAULT_COLUMNS
    # created/updated excluded by default
    assert "created" not in sync.DEFAULT_COLUMNS
    assert "updated" not in sync.DEFAULT_COLUMNS
    # every default key is a known registry key
    assert all(k in sync.COLUMN_REGISTRY for k in sync.DEFAULT_COLUMNS)


def test_get_column_config_defaults_and_forces_id_first(conn, db_path):
    cols = sync.get_column_config(1)
    assert cols[0] == "id"
    assert cols == sync.get_column_config(1)


def test_set_and_get_column_config_filters_unknown_and_forces_id(conn, db_path):
    sync.set_column_config(1, ["merchant", "amount", "bogus", "date"])
    cols = sync.get_column_config(1)
    assert cols[0] == "id"           # id forced first
    assert "bogus" not in cols       # unknown dropped
    assert cols == ["id", "merchant", "amount", "date"]


def test_resolve_columns_expands_tax_and_aligns(conn, db_path):
    sync.set_column_config(1, ["id", "date", "amount", "tax", "total"])
    cols = sync._resolve_columns(1, ["GST", "QST"])
    labels = sync._build_headers(cols)
    assert labels == ["ID", "Date", "Amount", "GST", "QST", "Total"]
    # money kinds detected for amount/tax/total
    kinds = {c["label"]: c["kind"] for c in cols}
    assert kinds["GST"] == "money" and kinds["QST"] == "money"
    assert kinds["Amount"] == "money" and kinds["Total"] == "money"


def test_build_row_aligns_with_headers_for_custom_order(conn, db_path):
    sync.set_column_config(1, ["id", "merchant", "tax", "amount", "receipt_name",
                               "receipt_link"])
    cols = sync._resolve_columns(1, ["GST", "QST"])
    headers = sync._build_headers(cols)
    txn = {
        "id": 7, "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "category_parent": None, "description": "d", "merchant": "Costco",
        "amount": 10.0, "tax_breakdown": {"GST": 1.0, "QST": 2.0},
        "total": 13.0, "counted": 13.0, "source": "ui", "loan": False,
        "notes": "", "created_at": "c", "updated_at": "u",
    }
    ctx = {"receipt_name": "RNAME", "receipt_link": "RLINK"}
    row = sync._build_row(txn, cols, ctx)
    assert len(row) == len(headers)
    d = dict(zip(headers, row))
    assert d["ID"] == 7
    assert d["Merchant"] == "Costco"
    assert d["GST"] == 1.0 and d["QST"] == 2.0
    assert d["Amount"] == 10.0
    assert d["Receipt"] == "RNAME"
    assert d["Receipt Link"] == "RLINK"


# --- reconcile with custom config ---------------------------------------

def _reconcile(grid, sheet_id="cfg"):
    return patch.object(sync.gc, "is_connected", return_value=True), \
        patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
        patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
        patch.object(sync.gc, "find_spreadsheet", return_value=None), \
        patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": sheet_id}), \
        patch.object(sync.gc, "is_spreadsheet_alive", return_value=True)


def test_reconcile_writes_custom_header_order(conn, db_path):
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0})
    conn.commit()
    sync.set_column_config(1, ["id", "merchant", "amount", "date"])
    grid: dict = {}
    p = _reconcile(grid)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        sync.reconcile()
    header = grid["2026"][0]
    assert header == ["ID", "Merchant", "Amount", "Date"]


def test_reconcile_dropping_amount_does_not_crash_format(conn, db_path):
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0})
    conn.commit()
    sync.set_column_config(1, ["id", "date", "merchant", "notes"])
    grid: dict = {}
    p = _reconcile(grid)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        sync.reconcile()  # must not raise on missing Amount/Date-money col
    header = grid["2026"][0]
    assert "Amount" not in header


def test_receipt_name_and_link_are_separate_columns(conn, db_path):
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "merchant": "Costco", "total": 9.0})
    conn.execute("UPDATE transactions SET receipt_link='https://drive/y' WHERE id=?",
                 (txn["id"],))
    conn.commit()
    grid: dict = {}
    p = _reconcile(grid)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        sync.reconcile()
    header = grid["2026"][0]
    assert "Receipt" in header and "Receipt Link" in header
    name_idx, link_idx = header.index("Receipt"), header.index("Receipt Link")
    data_row = next(r for r in grid["2026"][1:] if r and str(r[0]).isdigit())
    assert data_row[link_idx] == "https://drive/y"
    # derived readable name: date_slug(merchant)
    assert data_row[name_idx] and data_row[name_idx] != "https://drive/y"
    assert "costco" in data_row[name_idx].lower()


# --- totals row ----------------------------------------------------------

def _data_rows(rows):
    return [r for r in rows[1:] if r and str(r[0]).isdigit()]


def test_totals_row_frozen_at_top_with_sum_formulas(conn, db_path):
    for i in range(3):
        txn_svc.create_transaction(conn, {
            "date": "2026-06-05", "type": "expense", "category": "Groceries",
            "total": 10.0 + i})
    conn.commit()
    grid: dict = {}
    p = _reconcile(grid)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        sync.reconcile()
    rows = grid["2026"]
    assert rows[1][0] == "TOTALS"            # frozen TOTALS at the top (row 2)
    header = rows[0]
    amount_idx = header.index("Amount")
    cell = rows[1][amount_idx]
    # open-ended SUM from row 3 (data) → auto-extends, excludes header + TOTALS
    assert isinstance(cell, str) and cell.startswith("=SUM(") and "3:" in cell


def test_totals_row_not_duplicated_on_resync(conn, db_path):
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 10.0})
    conn.commit()
    grid: dict = {}
    p = _reconcile(grid)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        sync.reconcile()
        txn_svc.create_transaction(conn, {
            "date": "2026-06-05", "type": "expense", "category": "Dining",
            "total": 20.0})
        conn.commit()
        sync.reconcile()
    rows = grid["2026"]
    totals = [r for r in rows if r and r[0] == "TOTALS"]
    assert len(totals) == 1
    assert rows[1][0] == "TOTALS"            # singleton, frozen at the top
    # exactly two data rows survive below the frozen header+TOTALS
    assert len(_data_rows(rows)) == 2


def test_legacy_tab_gets_totals_row(conn, db_path):
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 10.0})
    conn.commit()
    grid = {"Transactions": []}
    p = _reconcile(grid)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        sync.reconcile()
    rows = grid["Transactions"]
    assert rows[1][0] == "TOTALS"            # legacy tab also gets a frozen TOTALS


def test_summary_excludes_totals_row(conn, db_path):
    from app.db import get_db
    with get_db() as c:
        txn_svc.create_transaction(c, {"date": "2024-03-01", "type": "expense",
                                       "category": "Groceries", "total": 10.0})
        txn_svc.create_transaction(c, {"date": "2025-04-01", "type": "expense",
                                       "category": "Groceries", "total": 20.0})
    grid: dict = {}
    p = _reconcile(grid, "smtot")
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        sync.reconcile()
    flat = [cell for row in grid["Summary"] for cell in row]
    amount_formula = next(c for c in flat if isinstance(c, str)
                          and c.startswith("=SUM("))
    # Summary sums each year OPEN-ENDED from row 3, so the header (row 1) and the
    # frozen TOTALS row (row 2) are excluded.
    assert "3:" in amount_formula
    import re
    for m in re.findall(r"'(\d{4})'!([A-Z]+)(\d+):([A-Z]+)(\d*)", amount_formula):
        _, _, start, _, end = m
        assert start == "3" and end == "", \
            "Summary must sum data rows only (open-ended from row 3)"


# --- endpoints -----------------------------------------------------------

@pytest.fixture()
def client(db_path):
    from app.routes import google_auth
    app = FastAPI()
    register_error_handler(app)
    app.include_router(google_auth.router)
    return TestClient(app, raise_server_exceptions=False)


def test_get_columns_returns_available_and_selected(client):
    resp = client.get("/api/google/columns?profile_id=1")
    assert resp.status_code == 200
    body = resp.json()
    keys = [c["key"] for c in body["available"]]
    assert "tax" in keys                       # tax is one selectable entry
    tax_entry = next(c for c in body["available"] if c["key"] == "tax")
    assert tax_entry["label"] == "Tax columns"
    assert body["selected"][0] == "id"
    assert body["profile_id"] == 1


def test_put_columns_saves_forces_id_and_triggers_sync(client, monkeypatch):
    called = []
    monkeypatch.setattr(sync, "request_sync", lambda: called.append(1))
    resp = client.put("/api/google/columns",
                      json={"profile_id": 1,
                            "columns": ["merchant", "amount", "date"]})
    assert resp.status_code == 200
    assert resp.json()["selected"] == ["id", "merchant", "amount", "date"]
    assert called == [1]                        # sync triggered
    assert sync.get_column_config(1) == ["id", "merchant", "amount", "date"]


def test_put_columns_rejects_unknown_keys(client):
    resp = client.put("/api/google/columns",
                      json={"profile_id": 1, "columns": ["merchant", "bogus"]})
    assert resp.status_code == 400
