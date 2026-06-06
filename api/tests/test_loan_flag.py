"""Loan flag: default false, settable on create/update, survives round-trip."""
from app.db import get_db
from app.services import transactions as txn_svc


def _data(**over):
    return {"date": "2026-06-01", "type": "expense", "category": "Groceries",
            "total": 10.0} | over


def test_loan_defaults_false(db_path):
    with get_db() as conn:
        txn = txn_svc.create_transaction(conn, _data())
    assert txn["loan"] is False


def test_loan_set_on_create(db_path):
    with get_db() as conn:
        txn = txn_svc.create_transaction(conn, _data(loan=True))
    assert txn["loan"] is True


def test_loan_update(db_path):
    with get_db() as conn:
        txn = txn_svc.create_transaction(conn, _data())
        updated = txn_svc.update_transaction(conn, txn["id"], {"loan": True})
    assert updated["loan"] is True


def test_loan_migration_idempotent(db_path):
    from app.db import init_db
    init_db()  # second run on an already-migrated DB must not raise
    with get_db() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
    assert "loan" in cols
