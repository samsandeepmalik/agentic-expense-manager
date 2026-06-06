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


def test_loan_via_route(db_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes import transactions as txn_routes
    app = FastAPI()
    app.include_router(txn_routes.router)
    client = TestClient(app)
    created = client.post("/api/transactions", json={
        "date": "2026-06-01", "type": "expense", "category": "Groceries",
        "total": 25.0, "loan": True}).json()
    assert created["loan"] is True
    patched = client.patch(f"/api/transactions/{created['id']}",
                           json={"loan": False}).json()
    assert patched["loan"] is False


def test_loan_in_csv_export(db_path):
    from app.db import get_db
    with get_db() as conn:
        txn_svc.create_transaction(conn, _data(loan=True))
        out = txn_svc.export_csv(conn)
    header, first = out.splitlines()[0], out.splitlines()[1]
    assert "loan" in header
    assert first.rstrip().endswith("True")
