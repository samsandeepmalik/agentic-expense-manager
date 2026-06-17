"""Adversarial verification of sync.py configurable columns + TOTALS + receipts.

These tests are designed to BREAK the sync, not confirm it. Reuses the stateful
fake Sheets client from test_sync.py.
"""

import re
from unittest.mock import patch

import pytest

from app.services import sync, transactions as txn_svc
from test_sync import _fake_sheets


def _patches(grid, sid="adv"):
    return patch.object(sync.gc, "is_connected", return_value=True), \
        patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
        patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
        patch.object(sync.gc, "find_spreadsheet", return_value=None), \
        patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": sid}), \
        patch.object(sync.gc, "is_spreadsheet_alive", return_value=True)


def _reconcile(grid, sid="adv"):
    p = _patches(grid, sid)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        return sync.reconcile()


def _data_rows(tab_rows):
    return [r for r in tab_rows[1:] if r and str(r[0]).isdigit()]


def _totals_rows(tab_rows):
    return [r for r in tab_rows[1:] if r and str(r[0]) == "TOTALS"]


# ---------------------------------------------------------------------------
# HUNT 1: positional drift with a custom column config
# ---------------------------------------------------------------------------

def test_custom_config_header_row_alignment_and_totals_target(conn, db_path):
    """Reordered config, a money column dropped, tax in the middle.
    header[i] must describe row[i]; TOTALS SUM letters must match money cols."""
    # Custom order: id (forced), date, total (money), tax (money, expands),
    # category, amount (money). 'counted' money column DROPPED on purpose.
    sync.set_column_config(1, ["date", "total", "tax", "category", "amount"])
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0})
    conn.commit()
    grid: dict = {}
    _reconcile(grid)

    rows = grid["2026"]
    header = rows[0]
    data = _data_rows(rows)[0]
    assert len(header) == len(data), "header/row length mismatch"

    # Resolve the same column model the code used, to know expected money cols.
    tax_cols = ["GST", "QST"]  # default tax profile seeds GST+QST
    cols = sync._resolve_columns(1, tax_cols)
    expected_labels = [c["label"] for c in cols]
    assert header == expected_labels

    # id forced first
    assert header[0] == "ID"
    # money columns by kind
    money_idx = [i for i, c in enumerate(cols) if c["kind"] == "money"]
    money_letters = {sync._col_letter(i + 1) for i in money_idx}

    totals = _totals_rows(rows)
    assert len(totals) == 1
    totals_row = totals[0]
    sum_letters = set()
    for cell in totals_row:
        if isinstance(cell, str) and cell.startswith("=SUM("):
            m = re.match(r"=SUM\(([A-Z]+)\d+:", cell)
            assert m, f"bad SUM formula {cell}"
            sum_letters.add(m.group(1))
    assert sum_letters == money_letters, (
        f"TOTALS SUM letters {sum_letters} != money cols {money_letters}")

    # Every SUM column in the TOTALS row must align with a money header cell.
    for i, cell in enumerate(totals_row):
        if isinstance(cell, str) and cell.startswith("=SUM("):
            assert cols[i]["kind"] == "money", (
                f"SUM at col {i} ({header[i]}) is not a money column")


def test_minimal_config_only_id_and_date(conn, db_path):
    """Only id+date (no money column at all) -> no TOTALS SUM, no crash."""
    sync.set_column_config(1, ["date"])
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0})
    conn.commit()
    grid: dict = {}
    _reconcile(grid)
    rows = grid["2026"]
    assert rows[0] == ["ID", "Date"]
    # TOTALS row: first cell label, second blank (date isn't money).
    totals = _totals_rows(rows)
    assert len(totals) == 1
    assert totals[0][0] == "TOTALS"
    assert not any(isinstance(c, str) and c.startswith("=SUM(")
                   for c in totals[0])


# ---------------------------------------------------------------------------
# HUNT 2: TOTALS / append collision across many reconciles
# ---------------------------------------------------------------------------

def test_totals_singleton_through_add_delete_cycles(conn, db_path):
    grid: dict = {}
    ids = []

    def add(total):
        t = txn_svc.create_transaction(conn, {
            "date": "2026-06-05", "type": "expense", "category": "Groceries",
            "total": total})
        conn.commit()
        ids.append(t["id"])
        return t["id"]

    p = _patches(grid)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        add(10.0)
        sync.reconcile()
        add(20.0)
        sync.reconcile()
        add(30.0)
        sync.reconcile()
        txn_svc.delete_transaction(conn, ids[1])
        conn.commit()
        sync.reconcile()

    rows = grid["2026"]
    totals = _totals_rows(rows)
    assert len(totals) == 1, f"expected exactly one TOTALS row, got {len(totals)}"
    # TOTALS is frozen at the top (row 2); header is row 1; data is below.
    assert rows[0][0] == "ID" and rows[1][0] == "TOTALS"
    # No TOTALS row leaks into the data region.
    assert all(not (r and r[0] == "TOTALS") for r in rows[2:])
    # Data integrity: the two surviving ids present, deleted one gone.
    data_ids = {int(r[0]) for r in _data_rows(rows)}
    assert data_ids == {ids[0], ids[2]}


def test_id_map_never_includes_totals(conn, db_path):
    """_sheet_ids_for_tab must skip the TOTALS row (non-numeric id)."""
    grid: dict = {}
    p = _patches(grid)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        for i in range(3):
            txn_svc.create_transaction(conn, {
                "date": "2026-06-05", "type": "expense",
                "category": "Groceries", "total": 10.0 + i})
        conn.commit()
        sync.reconcile()
        sync.reconcile()
        fake = _fake_sheets(grid)
        id_map = sync._sheet_ids_for_tab(fake, "adv", "2026")
    assert "TOTALS" not in id_map and len(id_map) == 3
    # Every mapped row is a data row (>= 3), never the header or frozen TOTALS.
    assert all(rn >= 3 for rn in id_map.values())
    for rn in id_map.values():
        assert grid["2026"][rn - 1][0] != "TOTALS"


# ---------------------------------------------------------------------------
# HUNT 3: Summary double-count / empty-year edges
# ---------------------------------------------------------------------------

def test_summary_equals_data_only_not_totals(db_path):
    from app.db import get_db
    with get_db() as c:
        txn_svc.create_transaction(c, {"date": "2024-03-01", "type": "expense",
                                       "category": "Groceries", "total": 10.0})
        txn_svc.create_transaction(c, {"date": "2024-05-01", "type": "expense",
                                       "category": "Groceries", "total": 30.0})
        txn_svc.create_transaction(c, {"date": "2025-04-01", "type": "expense",
                                       "category": "Groceries", "total": 20.0})
    grid: dict = {}
    _reconcile(grid)
    # Summary Total formula for a money col must bound each year to its last
    # DATA row, never including the TOTALS row.
    flat = [c for row in grid["Summary"] for c in row]
    formulas = [c for c in flat if isinstance(c, str) and c.startswith("=")]
    assert formulas
    for f in formulas:
        # extract end-row of each per-year SUM range
        for m in re.finditer(r"SUM\('(\d{4})'!([A-Z]+)2:[A-Z]+(\d+)\)", f):
            yr, _letter, endrow = m.group(1), m.group(2), int(m.group(3))
            tab_rows = grid[yr]
            # the row at endrow (1-based) must be a DATA row, not TOTALS
            assert tab_rows[endrow - 1][0] != "TOTALS", (
                f"Summary includes TOTALS row for {yr}: {f}")
            # and the row just after endrow should be TOTALS (the boundary)
            if endrow < len(tab_rows):
                assert tab_rows[endrow][0] == "TOTALS"


def test_summary_year_tab_with_zero_data_rows(db_path):
    """A year tab that exists but has no data rows must not crash and must not
    emit a bogus SUM range for that year."""
    from app.db import get_db
    with get_db() as c:
        txn_svc.create_transaction(c, {"date": "2025-04-01", "type": "expense",
                                       "category": "Groceries", "total": 20.0})
    # Pre-seed an empty 2024 tab (exists, header only, zero data rows).
    grid: dict = {"2024": [["ID", "Date"]]}
    _reconcile(grid)
    # Open-ended SUM from row 3: an empty 2024 tab may be referenced but sums to
    # 0 (no double-count, no crash). Must still capture 2025's data, and every
    # range must start at row 3 (excludes header + frozen TOTALS).
    flat = [c for row in grid.get("Summary", []) for c in row]
    formulas = [c for c in flat if isinstance(c, str) and c.startswith("=")]
    assert formulas and any("'2025'!" in f for f in formulas)
    for f in formulas:
        for m in re.findall(r"'(\d{4})'!([A-Z]+)(\d+):([A-Z]+)(\d*)", f):
            _, _, start, _, end = m
            assert start == "3" and end == ""    # data-only, open-ended


# ---------------------------------------------------------------------------
# HUNT 4: dropped required columns
# ---------------------------------------------------------------------------

def test_amount_column_dropped_does_not_crash(conn, db_path):
    sync.set_column_config(1, ["date", "category", "total"])  # no 'amount'
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0})
    conn.commit()
    grid: dict = {}
    _reconcile(grid)  # must not raise
    assert "Amount" not in grid["2026"][0]
    assert "Total" in grid["2026"][0]


def test_id_cannot_be_dropped_via_service(db_path):
    saved = sync.set_column_config(1, ["date", "category"])  # no id
    assert saved[0] == "id"
    assert sync.get_column_config(1)[0] == "id"


def test_id_cannot_be_dropped_via_put_route(db_path):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.put("/api/google/columns",
                   json={"profile_id": 1, "columns": ["date", "category"]})
    assert r.status_code == 200, r.text
    assert r.json()["selected"][0] == "id"


def test_put_route_rejects_unknown_keys(db_path):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.put("/api/google/columns",
                   json={"profile_id": 1, "columns": ["date", "boguscol"]})
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# HUNT 5: receipt name/link edge cases
# ---------------------------------------------------------------------------

def test_receipt_no_link_no_image(conn, db_path):
    txn = {"id": 1, "date": "2026-06-05", "merchant": "Costco",
           "image_path": None, "receipt_link": None}
    name, link = sync._maybe_upload_receipt(conn, txn, {"id": 1, "name": "P"})
    assert name == "" and link == ""


def test_receipt_missing_merchant_key_does_not_throw(conn, db_path):
    # txn dict literally missing 'merchant' key, only a link present.
    txn = {"id": 1, "date": "2026-06-05", "receipt_link": "https://drive/z"}
    name, link = sync._maybe_upload_receipt(conn, txn, {"id": 1, "name": "P"})
    assert link == "https://drive/z"
    assert name == "2026-06-05_receipt"  # slug falls back to 'receipt'


def test_receipt_upload_failure_propagates_or_handled(conn, db_path, tmp_path):
    """If the image exists but upload raises, the whole reconcile must fail
    loudly (caught by _safe_reconcile) rather than silently writing bad cells."""
    img = tmp_path / "r.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    txn = {"id": 1, "date": "2026-06-05", "merchant": "Shop",
           "image_path": str(img), "receipt_link": None}
    with patch.object(sync.gc, "upload_receipt_image",
                      side_effect=RuntimeError("drive 500")):
        with pytest.raises(RuntimeError):
            sync._maybe_upload_receipt(conn, txn, {"id": 1, "name": "P"})


def test_receipt_name_blank_merchant(conn, db_path):
    txn = {"id": 1, "date": "2026-06-05", "merchant": "", "receipt_link": "x"}
    name, _ = sync._maybe_upload_receipt(conn, txn, {"id": 1, "name": "P"})
    assert name == "2026-06-05_receipt"


# ---------------------------------------------------------------------------
# HUNT 6: legacy vs year tab + config switch full rewrite
# ---------------------------------------------------------------------------

def test_legacy_tab_gets_totals_no_summary(db_path):
    from app.db import get_db
    with get_db() as c:
        txn_svc.create_transaction(c, {"date": "2026-06-01", "type": "expense",
                                       "category": "Groceries", "total": 10.0})
    grid: dict = {"Transactions": []}
    _reconcile(grid)
    assert "Summary" not in grid
    assert _totals_rows(grid["Transactions"]), "legacy tab missing TOTALS"
    assert len(_totals_rows(grid["Transactions"])) == 1


def test_config_switch_triggers_full_rewrite(conn, db_path):
    """Changing the column config must rewrite the tab cleanly (new header,
    realigned rows, exactly one TOTALS)."""
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0})
    conn.commit()
    grid: dict = {}
    p = _patches(grid)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        sync.reconcile()
        header_before = list(grid["2026"][0])
        # change config: drop most columns
        sync.set_column_config(1, ["date", "total"])
        sync.reconcile()
        header_after = list(grid["2026"][0])
    assert header_before != header_after
    assert header_after == ["ID", "Date", "Total"]
    rows = grid["2026"]
    assert len(_totals_rows(rows)) == 1
    data = _data_rows(rows)[0]
    assert len(data) == len(header_after)


# ---------------------------------------------------------------------------
# HUNT 7: tax component in txn not in active tax profile
# ---------------------------------------------------------------------------

def test_extra_tax_component_stays_aligned(conn, db_path):
    """A txn carrying a tax component NOT in the active tax profile must produce
    a stable, aligned column set (extra name appended, header==row length)."""
    import json
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0})
    # Inject an extra tax component not present in the active tax profile.
    bd = dict(txn["tax_breakdown"])
    bd["ZZZ"] = 1.23
    conn.execute("UPDATE transactions SET tax_breakdown=? WHERE id=?",
                 (json.dumps(bd), txn["id"]))
    conn.commit()
    grid: dict = {}
    _reconcile(grid)
    header = grid["2026"][0]
    assert "ZZZ" in header
    data = _data_rows(grid["2026"])[0]
    assert len(header) == len(data)
    zzz_idx = header.index("ZZZ")
    assert float(data[zzz_idx]) == 1.23
    # TOTALS SUM must cover the extra tax column too.
    totals = _totals_rows(grid["2026"])[0]
    assert isinstance(totals[zzz_idx], str) and totals[zzz_idx].startswith("=SUM(")
    letter = sync._col_letter(zzz_idx + 1)
    assert totals[zzz_idx].startswith(f"=SUM({letter}3:")   # open-ended from data row 3
