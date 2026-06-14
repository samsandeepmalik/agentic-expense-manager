import re
from unittest.mock import MagicMock, patch

from app.services import sync, transactions as txn_svc


def _fake_sheets(grid=None):
    """Stateful in-memory Sheets fake. grid maps tab title -> list of row lists
    (row 0 is the header). Supports values().get/update/append/clear,
    spreadsheets().get (tab meta) and batchUpdate (addSheet)."""
    grid = grid if grid is not None else {}
    svc = MagicMock()
    ss = svc.spreadsheets.return_value

    def _tab(rng):
        m = re.match(r"'?([^'!]+)'?!", rng)
        return m.group(1) if m else rng

    def _sheet_id_map():
        # sheetId == enumeration index, matching _meta's assignment.
        return {i: t for i, t in enumerate(grid)}

    def _meta(**_kwargs):
        call = MagicMock()
        call.execute.return_value = {"sheets": [
            {"properties": {"title": t, "sheetId": i}}
            for i, t in enumerate(grid)]}
        return call
    ss.get.side_effect = _meta

    def _batch(**kwargs):
        call = MagicMock()

        def run():
            for req in kwargs["body"]["requests"]:
                if "addSheet" in req:
                    props = req["addSheet"]["properties"]
                    # Mirror the live API: an index beyond the current sheet
                    # count is rejected ("new sheet index is too high"), NOT
                    # clamped. Catches the 999-index regression.
                    idx = props.get("index")
                    if idx is not None and idx > len(grid):
                        raise AssertionError(
                            "addSheet index too high: "
                            f"{idx} > {len(grid)}")
                    grid.setdefault(props["title"], [])
                elif "deleteDimension" in req:
                    rng = req["deleteDimension"]["range"]
                    title = _sheet_id_map().get(rng["sheetId"])
                    if title is None:
                        continue
                    rows = grid.get(title, [])
                    start, end = rng["startIndex"], rng["endIndex"]
                    del rows[start:end]
            return {}
        call.execute.side_effect = run
        return call
    ss.batchUpdate.side_effect = _batch

    vals = ss.values.return_value

    def _get(**kwargs):
        rng = kwargs["range"]
        rows = grid.get(_tab(rng), [])
        call = MagicMock()
        if rng.endswith("!1:1"):
            call.execute.return_value = {"values": [rows[0]] if rows else []}
        elif "A3:A" in rng:        # data column (header=row1, TOTALS=row2)
            call.execute.return_value = {"values": [[r[0]] for r in rows[2:] if r]}
        elif "A2:A" in rng:
            call.execute.return_value = {"values": [[r[0]] for r in rows[1:] if r]}
        elif rng.endswith("ZZ2"):  # header + TOTALS rows only
            call.execute.return_value = {"values": rows[:2]}
        else:
            call.execute.return_value = {"values": rows}
        return call
    vals.get.side_effect = _get

    def _update(**kwargs):
        rng, new = kwargs["range"], kwargs["body"]["values"]
        call = MagicMock()

        def run():
            rows = grid.setdefault(_tab(rng), [])
            m = re.search(r"!A(\d+)", rng)
            start = int(m.group(1)) - 1 if m else 0
            while len(rows) < start + len(new):
                rows.append([])
            for i, r in enumerate(new):
                rows[start + i] = list(r)
            return {}
        call.execute.side_effect = run
        return call
    vals.update.side_effect = _update

    def _append(**kwargs):
        rng, new = kwargs["range"], kwargs["body"]["values"]
        call = MagicMock()
        call.execute.side_effect = lambda: (
            grid.setdefault(_tab(rng), []).extend(list(r) for r in new) or {})
        return call
    vals.append.side_effect = _append

    def _clear(**kwargs):
        rng = kwargs["range"]
        call = MagicMock()
        call.execute.side_effect = lambda: (grid.__setitem__(_tab(rng), []) or {})
        return call
    vals.clear.side_effect = _clear
    return svc


def test_reconcile_pushes_once(conn, db_path):
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0})
    conn.commit()
    grid: dict = {}
    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
         patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
         patch.object(sync.gc, "find_spreadsheet", return_value=None), \
         patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": "fake123"}):
        first = sync.reconcile()
        second = sync.reconcile()
    assert first["synced"] == 1
    assert second["synced"] == 0          # idempotent


import asyncio

import pytest


def test_every_write_path_requests_sync(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "request_sync", lambda: calls.append(1))
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 10.0})
    txn_svc.update_transaction(conn, txn["id"], {"total": 20.0})
    txn_svc.bulk_action(conn, [txn["id"]], "delete")
    extra = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Dining",
        "total": 5.0})
    txn_svc.delete_transaction(conn, extra["id"])
    assert len(calls) == 5  # create, update, bulk, create, delete


@pytest.mark.asyncio
async def test_sync_worker_coalesces_bursts(db_path, monkeypatch):
    ran = []
    monkeypatch.setattr(sync, "_safe_reconcile", lambda: ran.append(1))
    monkeypatch.setattr(sync, "sync_enabled", lambda: True)
    worker = asyncio.create_task(sync.sync_worker(debounce=0.05))
    await asyncio.sleep(0.01)          # let worker install loop + event
    for _ in range(10):
        sync.request_sync()            # burst of writes
    await asyncio.sleep(0.2)
    worker.cancel()
    assert ran == [1]                  # one reconcile, not ten


def test_receipt_upload_uses_column(conn, monkeypatch):
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 9.0})
    conn.execute("UPDATE transactions SET receipt_link='https://drive/y' WHERE id=?",
                 (txn["id"],))
    fresh = dict(conn.execute("SELECT * FROM transactions WHERE id=?",
                              (txn["id"],)).fetchone())
    fresh["category"] = "Groceries"
    profile = {"id": 1, "name": "Personal", "drive_folder_id": None}
    name, link = sync._maybe_upload_receipt(conn, fresh, profile)
    assert link == "https://drive/y"
    assert name  # derived readable name even when only a link exists


def test_notes_round_trip_and_pending(conn, db_path):
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 12.0, "notes": "first note"})
    assert txn["notes"] == "first note"
    assert txn["sync_status"] == "pending"
    updated = txn_svc.update_transaction(conn, txn["id"], {"notes": "edited"})
    assert updated["notes"] == "edited"
    assert updated["sync_status"] == "pending"


def test_reconcile_header_contains_notes(conn, db_path):
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0, "notes": "with note"})
    conn.commit()
    grid: dict = {}
    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
         patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
         patch.object(sync.gc, "find_spreadsheet", return_value=None), \
         patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": "nt"}):
        sync.reconcile()
    header = grid["2026"][0]
    assert "Notes" in header
    notes_idx = header.index("Notes")
    assert grid["2026"][2][notes_idx] == "with note"   # row 1 header, row 2 TOTALS, data row 3


def test_row_to_dict_surfaces_parent_for_child_category(conn, db_path):
    from app.services import categories as cat_svc
    parent = cat_svc.upsert_category(conn, "Food", "expense", 100.0, True, None)
    cat_svc.upsert_category(conn, "Snacks", "expense", 100.0, True, None,
                            parent_id=parent["id"])
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Snacks",
        "total": 10.0})
    assert txn["category"] == "Snacks"
    assert txn["category_parent"] == "Food"
    # Top-level category → no parent.
    top = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 5.0})
    assert top["category"] == "Groceries"
    assert top["category_parent"] is None


def test_dashboard_by_category_rolls_subcategory_into_parent(conn, db_path):
    # The pie groups sub-category spend under the top-level parent so the chart
    # shows the parent total rather than fragmented child slices.
    from app.services import categories as cat_svc
    parent = cat_svc.upsert_category(conn, "Food", "expense", 100.0, True, None)
    cat_svc.upsert_category(conn, "Snacks", "expense", 100.0, True, None,
                            parent_id=parent["id"])
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Snacks",
        "total": 10.0})
    conn.commit()
    data = txn_svc.dashboard_data(conn, "2026-06")
    assert data["by_category"].get("Food") == 10.0
    assert "Snacks" not in data["by_category"]


def test_reconcile_emits_subcategory_column(conn, db_path):
    from app.services import categories as cat_svc
    parent = cat_svc.upsert_category(conn, "Food", "expense", 100.0, True, None)
    cat_svc.upsert_category(conn, "Snacks", "expense", 100.0, True, None,
                            parent_id=parent["id"])
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Snacks",
        "total": 10.0})
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 5.0})
    conn.commit()
    grid: dict = {}
    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
         patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
         patch.object(sync.gc, "find_spreadsheet", return_value=None), \
         patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": "sc"}):
        sync.reconcile()
    header = grid["2026"][0]
    assert header.index("Sub-category") == header.index("Category") + 1
    cat_idx, sub_idx = header.index("Category"), header.index("Sub-category")
    rows = {r[0]: r for r in grid["2026"][1:]}
    # Sub-category txn → parent in Category, leaf in Sub-category.
    snack_row = next(r for r in grid["2026"][1:] if r[sub_idx] == "Snacks")
    assert snack_row[cat_idx] == "Food"
    # Top-level txn → leaf in Category, blank Sub-category.
    groc_row = next(r for r in grid["2026"][1:] if r[cat_idx] == "Groceries")
    assert groc_row[sub_idx] == ""


def _reconcile_with(grid):
    return patch.object(sync.gc, "is_connected", return_value=True), \
        patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
        patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
        patch.object(sync.gc, "find_spreadsheet", return_value=None), \
        patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": "del"})


def test_reconcile_deletes_removed_row(conn, db_path):
    ids = []
    for i in range(3):
        t = txn_svc.create_transaction(conn, {
            "date": "2026-06-05", "type": "expense", "category": "Groceries",
            "total": 10.0 + i})
        ids.append(t["id"])
    conn.commit()
    grid: dict = {}
    patches = _reconcile_with(grid)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        sync.reconcile()   # seed header + rows (full rewrite)
        sync.reconcile()   # idempotent incremental
        before = len(grid["2026"])
        txn_svc.delete_transaction(conn, ids[1])
        conn.commit()
        sync.reconcile()   # should delete the row
    id_col = [r[0] for r in grid["2026"][1:] if r and str(r[0]).isdigit()]
    assert str(ids[1]) not in [str(x) for x in id_col]
    assert ids[0] in [int(x) for x in id_col]
    assert ids[2] in [int(x) for x in id_col]
    assert len(grid["2026"]) == before - 1


def test_reconcile_deletes_last_and_middle_no_corruption(conn, db_path):
    ids = []
    for i in range(4):
        t = txn_svc.create_transaction(conn, {
            "date": "2026-06-05", "type": "expense", "category": "Groceries",
            "total": 10.0 + i})
        ids.append(t["id"])
    conn.commit()
    grid: dict = {}
    patches = _reconcile_with(grid)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        sync.reconcile()
        sync.reconcile()
        # delete middle (ids[1]) and last (ids[3]) in one go
        txn_svc.delete_transaction(conn, ids[1])
        txn_svc.delete_transaction(conn, ids[3])
        conn.commit()
        sync.reconcile()
    remaining = {int(r[0]) for r in grid["2026"][1:] if r and str(r[0]).isdigit()}
    assert remaining == {ids[0], ids[2]}


def test_reconcile_no_deletion_no_data_row_deleted(conn, db_path):
    """An idempotent re-sync with no data changes must not delete any DATA row.
    (Totals-row maintenance may delete the bottom TOTALS row; that never touches
    data, so we assert no data row was removed by checking the surviving ids.)"""
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 10.0})
    conn.commit()
    grid: dict = {}
    fake = _fake_sheets(grid)
    data_row_deletes = []
    orig_batch = fake.spreadsheets.return_value.batchUpdate.side_effect

    def spy_batch(**kwargs):
        for req in kwargs["body"]["requests"]:
            if "deleteDimension" in req:
                rng = req["deleteDimension"]["range"]
                title = list(grid)[rng["sheetId"]]
                rows = grid.get(title, [])
                victim = rows[rng["startIndex"]] if rng["startIndex"] < len(rows) else []
                # Record only deletions that target a DATA row (numeric id cell).
                if victim and str(victim[0]).isdigit():
                    data_row_deletes.append(req)
        return orig_batch(**kwargs)
    fake.spreadsheets.return_value.batchUpdate.side_effect = spy_batch

    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=fake), \
         patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
         patch.object(sync.gc, "find_spreadsheet", return_value=None), \
         patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": "nd"}):
        sync.reconcile()
        sync.reconcile()
    assert data_row_deletes == []
    surviving = {int(r[0]) for r in grid["2026"][1:] if r and str(r[0]).isdigit()}
    assert surviving == {txn["id"]}


def test_format_tab_freezes_and_currency():
    sheets = MagicMock()
    cols = [
        {"key": "id", "label": "ID", "kind": "plain", "tax_name": None},
        {"key": "date", "label": "Date", "kind": "date", "tax_name": None},
        {"key": "amount", "label": "Amount", "kind": "money", "tax_name": None},
        {"key": "tax", "label": "GST", "kind": "money", "tax_name": "GST"},
        {"key": "tax", "label": "QST", "kind": "money", "tax_name": "QST"},
    ]
    sync._format_tab(sheets, "sid", 0, cols)
    body = sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
    reqs = body["requests"]
    frozen = [r for r in reqs if "updateSheetProperties" in r]
    assert frozen and frozen[0]["updateSheetProperties"]["properties"][
        "gridProperties"]["frozenRowCount"] == 2   # header + frozen TOTALS
    currency = [r for r in reqs if "repeatCell" in r
                and r["repeatCell"]["cell"]["userEnteredFormat"]
                .get("numberFormat", {}).get("type") == "CURRENCY"]
    assert len(currency) >= 1


def test_summary_tab_built_across_years(db_path):
    from app.db import get_db
    with get_db() as c:
        txn_svc.create_transaction(c, {"date": "2024-03-01", "type": "expense",
                                       "category": "Groceries", "total": 10.0})
        txn_svc.create_transaction(c, {"date": "2025-04-01", "type": "expense",
                                       "category": "Groceries", "total": 20.0})
    grid: dict = {}
    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
         patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
         patch.object(sync.gc, "find_spreadsheet", return_value=None), \
         patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": "sm"}):
        sync.reconcile()
    assert "Summary" in grid
    flat = [cell for row in grid["Summary"] for cell in row]
    amount_formula = next(c for c in flat if isinstance(c, str)
                          and c.startswith("=SUM("))
    assert "SUM('2024'!" in amount_formula and "SUM('2025'!" in amount_formula


def test_summary_skipped_for_legacy_layout(db_path):
    from app.db import get_db
    with get_db() as c:
        txn_svc.create_transaction(c, {"date": "2026-06-01", "type": "expense",
                                       "category": "Groceries", "total": 10.0})
    grid = {"Transactions": []}
    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
         patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
         patch.object(sync.gc, "find_spreadsheet", return_value=None), \
         patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": "lg"}):
        sync.reconcile()
    assert "Summary" not in grid


def test_summary_idempotent(db_path):
    from app.db import get_db
    with get_db() as c:
        txn_svc.create_transaction(c, {"date": "2024-03-01", "type": "expense",
                                       "category": "Groceries", "total": 10.0})
        txn_svc.create_transaction(c, {"date": "2025-04-01", "type": "expense",
                                       "category": "Groceries", "total": 20.0})
    grid: dict = {}
    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
         patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
         patch.object(sync.gc, "find_spreadsheet", return_value=None), \
         patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": "si"}):
        sync.reconcile()
        first = [list(r) for r in grid["Summary"]]
        sync.reconcile()
        second = [list(r) for r in grid["Summary"]]
    assert first == second
    summary_tabs = [t for t in grid if t == "Summary"]
    assert len(summary_tabs) == 1


def test_reconcile_per_profile_uses_separate_spreadsheets(db_path, monkeypatch):
    from app.db import get_db
    from app.services import profiles as prof_svc

    created_ids = iter(["sheetPersonal", "sheetInc"])

    with get_db() as conn:
        txn_svc.create_transaction(conn, {"date": "2026-06-01", "type": "expense",
                                          "category": "Groceries", "total": 10.0})
        inc = prof_svc.create_profile(conn, "Inc", "incorporation")
        prof_svc.set_active(conn, inc["id"])
        txn_svc.create_transaction(conn, {"date": "2026-06-02", "type": "expense",
                                          "category": "Groceries", "total": 99.0})
        prof_svc.set_active(conn, 1)

    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", side_effect=lambda: _fake_sheets()), \
         patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
         patch.object(sync.gc, "find_spreadsheet", return_value=None), \
         patch.object(sync.gc, "drive_create_spreadsheet",
                      side_effect=lambda title, folder_id: {"id": next(created_ids)}):
        sync.reconcile()

    with get_db() as conn:
        sheet_ids = [r["spreadsheet_id"] for r in
                     conn.execute("SELECT spreadsheet_id FROM profiles ORDER BY id")]
    assert all(sheet_ids) and sheet_ids[0] != sheet_ids[1]   # one sheet per profile


def test_sheet_has_one_column_per_tax_component(conn, db_path):
    from app.db import get_db
    with get_db() as c:
        c2 = txn_svc.create_transaction(c, {"date": "2026-06-05", "type": "expense",
                                            "category": "Groceries", "total": 50.0})
    grid: dict = {}
    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
         patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
         patch.object(sync.gc, "find_spreadsheet", return_value=None), \
         patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": "qc"}):
        sync.reconcile()
    header = grid["2026"][0]
    assert "GST" in header and "QST" in header
    assert "Taxes" not in header
    gst_idx, qst_idx = header.index("GST"), header.index("QST")
    data_row = grid["2026"][2]   # row 1 header, row 2 TOTALS, data starts row 3
    assert float(data_row[gst_idx]) > 0 and float(data_row[qst_idx]) > 0


def test_sheet_single_tax_column_for_one_component(conn, db_path):
    from app.db import get_db
    from app.services import categories as cat_svc
    with get_db() as c:
        cat_svc.save_tax_profile(c, "Ontario",
                                 [{"name": "HST", "rate": 13.0}], activate=True)
        txn_svc.create_transaction(c, {"date": "2026-06-05", "type": "expense",
                                       "category": "Groceries", "total": 50.0})
    grid: dict = {}
    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(grid)), \
         patch.object(sync.gc, "ensure_drive_folder", return_value="fake-folder"), \
         patch.object(sync.gc, "find_spreadsheet", return_value=None), \
         patch.object(sync.gc, "drive_create_spreadsheet", return_value={"id": "on"}):
        sync.reconcile()
    header = grid["2026"][0]
    assert "HST" in header and "GST" not in header and "QST" not in header


def test_receipt_upload_uses_real_mime_for_pdf(conn, tmp_path, monkeypatch):
    """Regression: PDF receipts uploaded to Drive with application/pdf, not image/pdf."""
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    captured = {}

    def fake_upload(filename, data, mime_type, profile, date=""):
        captured["mime"] = mime_type
        captured["filename"] = filename
        return {"link": "https://drive/x", "name": filename}

    monkeypatch.setattr(sync.gc, "upload_receipt_image", fake_upload)
    txn = {"id": 999, "date": "2026-06-10", "merchant": "Costco",
           "image_path": str(pdf), "receipt_link": None}
    name, link = sync._maybe_upload_receipt(conn, txn, {"id": 1, "name": "Personal"})
    assert link == "https://drive/x"
    assert name.endswith(".pdf")
    assert captured["mime"] == "application/pdf"
    assert captured["filename"].endswith(".pdf")


def test_ensure_spreadsheet_reuses_existing_sheet(conn, monkeypatch):
    """No stored id but a same-named sheet exists -> reuse it, don't duplicate."""
    prof = dict(conn.execute("SELECT * FROM profiles WHERE id=1").fetchone())
    prof["spreadsheet_id"] = None
    monkeypatch.setattr(sync.gc, "ensure_drive_folder", lambda p: "folderX")
    monkeypatch.setattr(sync.gc, "find_spreadsheet", lambda name, folder: "existingSheet")
    created = []
    monkeypatch.setattr(sync.gc, "drive_create_spreadsheet",
                        lambda title, folder: created.append(title) or {"id": "NEW"})
    sid = sync._ensure_spreadsheet(conn, prof)
    assert sid == "existingSheet"
    assert created == []
    assert conn.execute("SELECT spreadsheet_id FROM profiles WHERE id=1"
                        ).fetchone()[0] == "existingSheet"
