"""Import targets an explicit profile (chosen in the UI popup), not just the
active one. Falls back to active profile when none given; rejects unknown ids."""
import pytest

from app.db import get_db
from app.errors import AppError
from app.services import imports as imp_svc
from app.services import profiles as prof_svc


async def _fake_parse(text, profile_id):
    return [{"date": "2026-06-01", "type": "expense", "category": "Groceries",
             "merchant": "Metro", "description": "", "total": 20.0}]


@pytest.mark.asyncio
async def test_explicit_profile_id_overrides_active(db_path, monkeypatch):
    monkeypatch.setattr(imp_svc, "parse_with_agent", _fake_parse)
    with get_db() as conn:
        other = conn.execute(
            "INSERT INTO profiles(name, kind) VALUES ('Incorp','incorporation')"
        ).lastrowid
        active = prof_svc.active_id(conn)
    assert other != active

    record = await imp_svc.start_import("sheet.csv", b"x", profile_id=other)

    assert record["profile_id"] == other          # stamped on the import
    assert record["status"] == "review"


@pytest.mark.asyncio
async def test_defaults_to_active_profile_when_omitted(db_path, monkeypatch):
    monkeypatch.setattr(imp_svc, "parse_with_agent", _fake_parse)
    with get_db() as conn:
        active = prof_svc.active_id(conn)

    record = await imp_svc.start_import("sheet.csv", b"x")

    assert record["profile_id"] == active


@pytest.mark.asyncio
async def test_unknown_profile_id_rejected(db_path, monkeypatch):
    monkeypatch.setattr(imp_svc, "parse_with_agent", _fake_parse)
    with pytest.raises(AppError) as exc:
        await imp_svc.start_import("sheet.csv", b"x", profile_id=9999)
    assert exc.value.code == "profile_not_found"


def _seed_review(conn, rows):
    import json
    cur = conn.execute(
        "INSERT INTO imports(filename, status, rows) VALUES ('s.csv','review',?)",
        (json.dumps(rows),))
    return cur.lastrowid


def test_approve_uses_edited_rows(db_path):
    # The user edits rows in the review grid (category by id, loan, notes);
    # approve must honour those edits, not the originally-parsed values.
    with get_db() as conn:
        dining = conn.execute(
            "SELECT id FROM categories WHERE name='Dining'").fetchone()["id"]
        import_id = _seed_review(conn, [
            {"date": "2026-06-01", "type": "expense", "category": "Groceries",
             "merchant": "Metro", "total": 20.0, "loan": False, "notes": "",
             "skip": False}])
    edited = [{"date": "2026-06-01", "type": "expense", "category_id": dining,
               "merchant": "Metro", "total": 20.0, "loan": True,
               "notes": "20% of bill", "skip": False}]
    result = imp_svc.approve_import(import_id, None, rows=edited)
    assert result["created"] == 1
    with get_db() as conn:
        txn = conn.execute("SELECT * FROM transactions").fetchone()
    assert txn["category_id"] == dining        # edited category id honoured
    assert txn["loan"] == 1                     # edited loan honoured
    assert txn["notes"] == "20% of bill"        # edited notes honoured


def test_approve_rejects_row_count_mismatch(db_path):
    with get_db() as conn:
        import_id = _seed_review(conn, [
            {"date": "2026-06-01", "type": "expense", "category": "Groceries",
             "merchant": "M", "total": 1.0, "skip": False}])
    with pytest.raises(AppError) as exc:
        imp_svc.approve_import(import_id, None, rows=[])   # wrong length
    assert exc.value.code == "rows_mismatch"
