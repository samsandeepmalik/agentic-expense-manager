from app.services import transactions as txn_svc
from app.services.dedup import flag_duplicates


def test_flags_same_amount_within_one_day(conn):
    txn_svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 114.98, "merchant": "Metro",
    })
    rows = [
        {"date": "2026-06-06", "total": 114.98},   # within ±1 day → dup
        {"date": "2026-06-05", "total": 99.99},    # different amount → not
        {"date": "2026-06-09", "total": 114.98},   # too far → not
    ]
    assert flag_duplicates(conn, rows) == [True, False, False]
