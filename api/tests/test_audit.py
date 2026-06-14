from app.services import audit, transactions as txn_svc


def test_transaction_writes_are_audited(conn):
    txn = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 11.0, "source": "whatsapp"})
    rows = audit.recent(conn)
    assert rows[0]["event"] == "transaction_created"
    assert rows[0]["channel"] == "whatsapp"
    assert rows[0]["ref"] == str(txn["id"])

    txn_svc.delete_transaction(conn, txn["id"])
    assert audit.recent(conn)[0]["event"] == "transaction_deleted"


def test_bulk_delete_is_audited_per_transaction(conn):
    a = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 10.0})
    b = txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 20.0})

    txn_svc.bulk_action(conn, [a["id"], b["id"]], "delete")

    deleted = [r for r in audit.recent(conn) if r["event"] == "transaction_deleted"]
    assert {r["ref"] for r in deleted} == {str(a["id"]), str(b["id"])}


def test_sync_failure_recorded(conn, db_path, monkeypatch):
    from app.services import sync

    def boom():
        raise RuntimeError("sheet quota")
    monkeypatch.setattr(sync, "reconcile", boom)
    sync._safe_reconcile()
    rows = audit.recent(conn)
    assert rows[0]["event"] == "sync_failed"
    assert "sheet quota" in rows[0]["detail"]
    assert "sheet quota" in (sync.status().get("last_error") or "")


def test_audit_api(conn, db_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.errors import register_error_handler
    from app.routes import audit as audit_routes

    audit.record(conn, "transaction_created", channel="ui", ref="1")
    conn.commit()
    app = FastAPI()
    register_error_handler(app)
    app.include_router(audit_routes.router)
    client = TestClient(app, raise_server_exceptions=False)
    rows = client.get("/api/audit").json()
    assert rows[0]["event"] == "transaction_created"
