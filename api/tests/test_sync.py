from unittest.mock import MagicMock, patch

from app.services import sync, transactions as txn_svc


def _fake_sheets(store):
    """Minimal in-memory fake for spreadsheets().values() get/update/append."""
    sheets = MagicMock()
    values = sheets.spreadsheets.return_value.values.return_value
    values.get.return_value.execute.side_effect = lambda: {
        "values": [[str(i)] for i in store]}
    def _append(**kwargs):
        call = MagicMock()
        call.execute.side_effect = lambda: store.append(
            kwargs["body"]["values"][0][0]) or {}
        return call
    values.append.side_effect = _append
    values.update.return_value.execute.return_value = {}
    sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
        "spreadsheetId": "fake123"}
    return sheets


def test_reconcile_pushes_once(conn, db_path):
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0})
    conn.commit()
    store: list = []
    with patch.object(sync.gc, "is_connected", return_value=True), \
         patch.object(sync.gc, "sheets_service", return_value=_fake_sheets(store)):
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
    assert sync._maybe_upload_receipt(conn, fresh) == "https://drive/y"
