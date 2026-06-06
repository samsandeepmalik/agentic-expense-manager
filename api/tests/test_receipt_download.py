"""Lazy download: txns with receipt_link but no image_path get a local file."""
from app.db import get_db
from app.services import receipts as rcpt
from app.services import transactions as txn_svc

LINK = "https://drive.google.com/file/d/1FileId99/view"


def test_download_linked_receipts(monkeypatch, tmp_path, db_path):
    with get_db() as conn:
        txn = txn_svc.create_transaction(conn, {
            "date": "2026-06-01", "type": "expense", "category": "Dining",
            "total": 9.0, "receipt_link": LINK})

    monkeypatch.setattr(rcpt, "_drive_download",
                        lambda file_id: (b"\x89PNG fake", "image/png"))
    monkeypatch.setattr(rcpt.gc, "is_connected", lambda: True)
    monkeypatch.setattr(rcpt.config, "data_dir", tmp_path)

    done = rcpt.download_linked_receipts()
    assert done == 1
    with get_db() as conn:
        row = conn.execute("SELECT image_path FROM transactions WHERE id=?",
                           (txn["id"],)).fetchone()
    assert row["image_path"] and row["image_path"].endswith(".png")


def test_download_skips_when_not_connected(monkeypatch, db_path):
    monkeypatch.setattr(rcpt.gc, "is_connected", lambda: False)
    assert rcpt.download_linked_receipts() == 0


def test_extract_file_id():
    assert rcpt.extract_file_id(LINK) == "1FileId99"
    assert rcpt.extract_file_id("https://drive.google.com/open?id=1Xyz_-9") == "1Xyz_-9"
    assert rcpt.extract_file_id("https://example.com/receipt.png") is None
