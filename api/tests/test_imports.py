import json
from app.services import imports as svc


def test_persist_import_stores_rows_and_flags_duplicates(conn):
    rows = [
        {"date": "2026-05-01", "type": "expense", "category": "Groceries",
         "merchant": "Metro", "total": 50.0},
        {"date": "2026-05-02", "type": "expense", "category": "Groceries",
         "merchant": "Costco", "total": 20.0},
    ]
    import_id = svc._persist_import(conn, "statement.csv", rows, profile_id=1,
                                    channel="chat")
    conn.commit()  # get_db() owns commit; flush here so get_import's fresh conn sees the row
    record = svc.get_import(import_id)
    assert record["status"] == "review"
    assert len(record["rows"]) == 2
    assert record["channel"] == "chat"
    assert all("duplicate" in r and "skip" in r for r in record["rows"])
