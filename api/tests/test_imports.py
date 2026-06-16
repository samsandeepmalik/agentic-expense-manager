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


def test_import_summary_buckets_and_unresolved(conn):
    rows = [
        {"date": "2026-05-01", "type": "expense", "category": "Groceries",
         "merchant": "Metro", "total": 50.0},
        {"date": "2026-05-02", "type": "expense", "category": "Nonsense Cat",
         "merchant": "UBER *EATS", "total": 24.1},
    ]
    import_id = svc._persist_import(conn, "s.csv", rows, 1, "chat")
    conn.commit()
    summary = svc.import_summary(conn, import_id)
    assert summary["total_rows"] == 2
    assert summary["to_record"] == 2  # no dups in a fresh db
    labels = {c["label"]: c for c in summary["parsed_categories"]}
    assert labels["Groceries"]["resolved_category_id"] is not None
    assert any(u["merchant"] == "UBER *EATS" for u in summary["unresolved"])
    assert len(summary["sample"]) <= 10


from app.services import categories as cat_svc


def test_remap_import_applies_mapping_redups_idempotent(conn):
    rideshare = cat_svc.upsert_category(conn, "Rideshare", "expense", 100, True, None)
    rows = [
        {"date": "2026-05-02", "type": "expense", "category": "Nonsense",
         "merchant": "UBER *EATS 800", "total": 24.1},
        {"date": "2026-05-03", "type": "expense", "category": "Nonsense",
         "merchant": "LYFT RIDE", "total": 12.0},
    ]
    import_id = svc._persist_import(conn, "s.csv", rows, 1, "chat")
    conn.commit()
    result = svc.remap_import(conn, import_id, [
        {"match": {"contains": "UBER"}, "category_id": rideshare["id"]},
        {"match": {"index": 1}, "category_id": rideshare["id"]},
    ])
    assert result["unresolved"] == []
    stored = svc.get_import(import_id)["rows"]
    assert all(r["category_id"] == rideshare["id"] for r in stored)
    again = svc.remap_import(conn, import_id, [
        {"match": {"contains": "UBER"}, "category_id": rideshare["id"]},
    ])
    assert again["total_rows"] == 2


import asyncio


def test_classify_and_start_csv_is_statement(conn, monkeypatch):
    async def fake_parse(text, profile_id):
        return [{"date": "2026-05-01", "type": "expense", "category": "Groceries",
                 "merchant": "Metro", "total": 5.0}]
    monkeypatch.setattr(svc, "parse_with_agent", fake_parse)
    result = asyncio.run(svc.classify_and_start("bank.csv", b"a,b\n1,2\n", 1))
    assert result["kind"] == "statement"
    assert result["import_id"] is not None


def test_classify_and_start_scanned_pdf_is_receipt(conn, monkeypatch):
    from app.errors import AppError
    def boom(filename, data):
        raise AppError("pdf_unreadable", "scanned", 422)
    monkeypatch.setattr(svc, "extract_text", boom)
    result = asyncio.run(svc.classify_and_start("scan.pdf", b"%PDF", 1))
    assert result["kind"] == "receipt"
