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
