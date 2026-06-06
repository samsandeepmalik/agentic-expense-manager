"""Imports audit at file level: one import_uploaded + one import_approved row."""
import json

from app.db import get_db
from app.services import audit
from app.services import imports as imp_svc


def _seed_review_import(conn, n_rows=3) -> int:
    rows = [{"date": "2026-06-01", "type": "expense", "category": "Groceries",
             "merchant": f"Shop{i}", "description": "", "total": 10.0 + i,
             "duplicate": False, "skip": False} for i in range(n_rows)]
    cur = conn.execute(
        "INSERT INTO imports(filename, status, rows) VALUES (?, 'review', ?)",
        ("statement.csv", json.dumps(rows)))
    return cur.lastrowid


def test_approve_writes_single_file_level_audit_row(db_path):
    with get_db() as conn:
        import_id = _seed_review_import(conn, n_rows=3)
    result = imp_svc.approve_import(import_id, None)
    assert result["created"] == 3
    with get_db() as conn:
        events = [r["event"] for r in audit.recent(conn)]
        approved = [r for r in audit.recent(conn) if r["event"] == "import_approved"]
    assert events.count("transaction_created") == 0      # no per-row spam
    assert len(approved) == 1
    assert "statement.csv" in approved[0]["detail"]
    assert "3" in approved[0]["detail"]
    assert approved[0]["channel"] == "import"
