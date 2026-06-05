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
