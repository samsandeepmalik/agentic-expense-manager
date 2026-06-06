"""Imported rows carrying a Drive/receipt URL store it on the transaction."""
import json

from app.db import get_db
from app.services import imports as imp_svc

LINK = "https://drive.google.com/file/d/1AbCdEfGhIjK/view"


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
