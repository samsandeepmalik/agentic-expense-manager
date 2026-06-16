from app.services import profiles as prof_svc
from app.services import transactions as txn_svc
from app.services.dedup import find_duplicate, flag_duplicates


def _seed(conn, **over):
    base = {"date": "2026-06-05", "type": "expense", "category": "Groceries",
            "total": 114.98, "merchant": "Metro"}
    return txn_svc.create_transaction(conn, base | over)


def test_find_duplicate_matches_fields(conn):
    _seed(conn)
    dup = find_duplicate(conn, {"date": "2026-06-05", "total": 114.98,
                                "merchant": "metro"})
    assert dup is not None
    assert dup["reason"] == "fields"
    assert dup["txn"]["merchant"] == "Metro"


def test_find_duplicate_none_when_merchant_differs(conn):
    _seed(conn)
    assert find_duplicate(conn, {"date": "2026-06-05", "total": 114.98,
                                 "merchant": "Walmart"}) is None


def test_find_duplicate_none_when_amount_differs(conn):
    _seed(conn)
    assert find_duplicate(conn, {"date": "2026-06-05", "total": 99.99,
                                 "merchant": "Metro"}) is None


def test_find_duplicate_none_when_date_differs(conn):
    _seed(conn)
    assert find_duplicate(conn, {"date": "2026-06-06", "total": 114.98,
                                 "merchant": "Metro"}) is None


def test_find_duplicate_matches_receipt_link(conn):
    _seed(conn, receipt_link="https://drive.google.com/abc")
    # Different amount/merchant/date, but same receipt link → still a duplicate.
    dup = find_duplicate(conn, {"date": "2026-09-09", "total": 5.0,
                                "merchant": "Other",
                                "receipt_link": "https://drive.google.com/abc"})
    assert dup is not None
    assert dup["reason"] == "receipt"


def test_find_duplicate_ignores_empty_receipt_link(conn):
    _seed(conn)  # seeded txn has no receipt_link
    assert find_duplicate(conn, {"date": "2026-01-01", "total": 1.0,
                                 "merchant": "Nope", "receipt_link": ""}) is None


def test_find_duplicate_isolated_by_profile(conn):
    biz = prof_svc.create_profile(conn, "Business", "incorporation")
    _seed(conn)  # active (Personal)
    # Same fields but target the Business profile → no match.
    assert find_duplicate(conn, {"date": "2026-06-05", "total": 114.98,
                                 "merchant": "Metro"}, profile_id=biz["id"]) is None


def test_flag_duplicates_uses_the_rule(conn):
    _seed(conn)
    rows = [
        {"date": "2026-06-05", "total": 114.98, "merchant": "Metro"},   # dup
        {"date": "2026-06-06", "total": 114.98, "merchant": "Metro"},   # diff day
        {"date": "2026-06-05", "total": 114.98, "merchant": "Costco"},  # diff merchant
    ]
    assert flag_duplicates(conn, rows) == [True, False, False]
