"""Imported rows carrying a Drive/receipt URL store it on the transaction."""
import json

from app.db import get_db
from app.services import imports as imp_svc

LINK = "https://drive.google.com/file/d/1AbCdEfGhIjK/view"
SOURCE = "https://drive.google.com/file/d/SOURCE_FILE/view"


def test_receipt_link_stored_on_approve(db_path):
    rows = [{"date": "2026-06-01", "type": "expense", "category": "Groceries",
             "merchant": "Metro", "description": "", "total": 20.0,
             "receipt_link": LINK, "duplicate": False, "skip": False}]
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO imports(filename, status, rows) VALUES (?, 'review', ?)",
            ("sheet.csv", json.dumps(rows)))
        import_id = cur.lastrowid
    imp_svc.approve_import(import_id, None)
    with get_db() as conn:
        txn = conn.execute("SELECT * FROM transactions").fetchone()
    assert txn["receipt_link"] == LINK


def test_create_transaction_accepts_receipt_link(db_path):
    from app.services import transactions as txn_svc
    with get_db() as conn:
        txn = txn_svc.create_transaction(conn, {
            "date": "2026-06-01", "type": "expense", "category": "Dining",
            "total": 12.0, "receipt_link": LINK})
    assert txn["receipt_link"] == LINK


# ---------------------------------------------------------------------------
# source_link fallback: import.source_link injected when row lacks receipt_link
# ---------------------------------------------------------------------------

def test_approve_uses_source_link_when_row_has_no_receipt_link(db_path):
    """Rows without a per-row receipt_link should inherit the import's source_link."""
    rows = [
        {"date": "2026-06-01", "type": "expense", "category": "Groceries",
         "merchant": "Metro", "description": "", "total": 20.0,
         "duplicate": False, "skip": False},
        {"date": "2026-06-02", "type": "expense", "category": "Dining",
         "merchant": "Chipotle", "description": "", "total": 15.0,
         "duplicate": False, "skip": False},
    ]
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO imports(filename, status, rows, source_link) "
            "VALUES (?, 'review', ?, ?)",
            ("bank.csv", json.dumps(rows), SOURCE))
        import_id = cur.lastrowid
    imp_svc.approve_import(import_id, None)
    with get_db() as conn:
        txns = conn.execute(
            "SELECT receipt_link FROM transactions ORDER BY id").fetchall()
    assert len(txns) == 2
    assert all(t["receipt_link"] == SOURCE for t in txns)


def test_approve_row_receipt_link_takes_precedence_over_source_link(db_path):
    """A row's own receipt_link must not be overwritten by import.source_link."""
    rows = [
        # row with its own link — must keep its own
        {"date": "2026-06-01", "type": "expense", "category": "Groceries",
         "merchant": "Metro", "description": "", "total": 20.0,
         "receipt_link": LINK, "duplicate": False, "skip": False},
        # row without a link — must get source_link
        {"date": "2026-06-02", "type": "expense", "category": "Dining",
         "merchant": "Chipotle", "description": "", "total": 15.0,
         "duplicate": False, "skip": False},
    ]
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO imports(filename, status, rows, source_link) "
            "VALUES (?, 'review', ?, ?)",
            ("bank.csv", json.dumps(rows), SOURCE))
        import_id = cur.lastrowid
    imp_svc.approve_import(import_id, None)
    with get_db() as conn:
        txns = conn.execute(
            "SELECT receipt_link FROM transactions ORDER BY id").fetchall()
    assert txns[0]["receipt_link"] == LINK    # own link preserved
    assert txns[1]["receipt_link"] == SOURCE  # source_link injected


def test_approve_no_source_link_rows_without_link_get_null(db_path):
    """When source_link is NULL on the import, rows without receipt_link stay NULL."""
    rows = [{"date": "2026-06-01", "type": "expense", "category": "Groceries",
             "merchant": "Metro", "description": "", "total": 20.0,
             "duplicate": False, "skip": False}]
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO imports(filename, status, rows) VALUES (?, 'review', ?)",
            ("bank.csv", json.dumps(rows)))
        import_id = cur.lastrowid
    imp_svc.approve_import(import_id, None)
    with get_db() as conn:
        txn = conn.execute("SELECT receipt_link FROM transactions").fetchone()
    assert txn["receipt_link"] is None


# ---------------------------------------------------------------------------
# set_source_link
# ---------------------------------------------------------------------------

def test_set_source_link_persists_on_import(db_path):
    rows = [{"date": "2026-06-01", "type": "expense", "category": "Groceries",
             "merchant": "Metro", "description": "", "total": 5.0,
             "duplicate": False, "skip": False}]
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO imports(filename, status, rows) VALUES (?, 'review', ?)",
            ("s.csv", json.dumps(rows)))
        import_id = cur.lastrowid
    imp_svc.set_source_link(import_id, SOURCE)
    record = imp_svc.get_import(import_id)
    assert record["source_link"] == SOURCE


def test_set_source_link_missing_id_is_noop(db_path):
    """Calling set_source_link with a non-existent id must not raise."""
    imp_svc.set_source_link(99999, SOURCE)  # should not raise
