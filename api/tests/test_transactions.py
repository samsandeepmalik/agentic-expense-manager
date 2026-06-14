from app.services import transactions as svc


def _create(conn, **overrides):
    data = {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 114.98, "merchant": "Metro", "description": "", "source": "ui",
    }
    data.update(overrides)
    return svc.create_transaction(conn, data)


def test_create_back_calculates_quebec_taxes(conn):
    txn = _create(conn)
    assert txn["amount"] == 100.0
    assert txn["tax_breakdown"] == {"GST": 5.0, "QST": 9.98}
    assert txn["total"] == 114.98
    assert txn["counted"] == 114.98  # percent 100


def test_preview_computes_taxes_without_persisting(conn):
    before = len(svc.list_transactions(conn))
    result = svc.preview_transaction(conn, {
        "type": "expense", "category": "Groceries", "total": 114.98})
    assert result["amount"] == 100.0
    assert result["breakdown"] == {"GST": 5.0, "QST": 9.98}
    assert result["counted"] == 114.98
    assert len(svc.list_transactions(conn)) == before  # preview never writes


def test_non_taxable_category_skips_tax(conn):
    txn = _create(conn, category="Rent", total=1500.0)
    assert txn["amount"] == 1500.0 and txn["tax_breakdown"] == {}


def test_counted_uses_category_percent(conn):
    conn.execute("UPDATE categories SET percent=50 WHERE name='Dining'")
    txn = _create(conn, category="Dining", total=100.0)
    assert txn["counted"] == 50.0


def test_list_filters(conn):
    _create(conn)
    _create(conn, type="income", category="Salary", total=5000.0, date="2026-06-01")
    only_income = svc.list_transactions(conn, type_="income")
    assert len(only_income) == 1 and only_income[0]["category"] == "Salary"
    june = svc.list_transactions(conn, start="2026-06-01", end="2026-06-30")
    assert len(june) == 2


def test_search_q_matches_notes(conn):
    _create(conn, merchant="Metro", notes="reimbursable client lunch")
    _create(conn, merchant="Costco", notes="")
    hits = svc.list_transactions(conn, q="reimbursable")
    assert len(hits) == 1 and hits[0]["merchant"] == "Metro"
    # still matches merchant and description
    assert len(svc.list_transactions(conn, q="Costco")) == 1


def test_update_recomputes(conn):
    txn = _create(conn)
    updated = svc.update_transaction(conn, txn["id"], {"total": 229.96})
    assert updated["amount"] == 200.01 or updated["amount"] == 200.0


# --- sub-category roll-up + id-safe category resolution ---
from app.services import categories as cat_svc  # noqa: E402
from app.errors import AppError  # noqa: E402
import pytest  # noqa: E402


def _parent_child(conn, parent="Food", child="Snacks", budget=300.0):
    p = cat_svc.upsert_category(conn, parent, "expense", 100, True, budget)
    c = cat_svc.upsert_category(conn, child, "expense", 100, True, None,
                                parent_id=p["id"])
    return p, c


def test_budget_spent_includes_subcategory_spend(conn):
    _parent_child(conn)
    _create(conn, category="Snacks", total=50.0)   # recorded under the child
    data = svc.dashboard_data(conn, None)
    food = next(b for b in data["budgets"] if b["name"] == "Food")
    assert food["spent"] == 50.0   # child spend rolls into the parent budget


def test_pie_rolls_subcategory_into_parent(conn):
    _parent_child(conn)
    _create(conn, category="Snacks", total=50.0)
    data = svc.dashboard_data(conn, None)
    assert data["by_category"].get("Food") == 50.0
    assert "Snacks" not in data["by_category"]   # not a separate slice


def test_find_category_ambiguous_name_raises(conn):
    cat_svc.upsert_category(conn, "Travel", "expense", 100, True, None)   # top-level
    biz = cat_svc.upsert_category(conn, "Business", "expense", 100, True, None)
    cat_svc.upsert_category(conn, "Travel", "expense", 50, True, None,
                            parent_id=biz["id"])   # Travel under Business
    with pytest.raises(AppError):
        cat_svc.find_category_by_name(conn, "Travel")
    # disambiguation by parent_id resolves cleanly
    top = cat_svc.find_category_by_name(conn, "Travel", parent_id=0)
    assert top["percent"] == 100


def test_create_by_category_id_is_unambiguous(conn):
    biz = cat_svc.upsert_category(conn, "Business", "expense", 100, True, None)
    child = cat_svc.upsert_category(conn, "Travel", "expense", 50, True, None,
                                    parent_id=biz["id"])
    cat_svc.upsert_category(conn, "Travel", "expense", 100, True, None)   # collision
    txn = svc.create_transaction(conn, {
        "date": "2026-06-05", "type": "expense", "category_id": child["id"],
        "total": 100.0, "merchant": "x"})
    assert txn["category_id"] == child["id"]
    assert txn["counted"] == 50.0   # used the CHILD's 50% — not the top-level


def test_bulk_recategorize_still_works(conn):
    t1 = _create(conn, category="Groceries")
    n = svc.bulk_action(conn, [t1["id"]], "recategorize", category="Dining")
    assert n == 1
    assert svc.get_transaction(conn, t1["id"])["category"] == "Dining"


def test_bulk_delete(conn):
    ids = [_create(conn)["id"] for _ in range(3)]
    svc.bulk_action(conn, ids[:2], "delete")
    assert len(svc.list_transactions(conn)) == 1


def test_csv_export(conn):
    _create(conn)
    csv_text = svc.export_csv(conn)
    assert "Metro" in csv_text and csv_text.startswith("id,date,type")


def test_dashboard_data_fresh_db_returns_zeros(conn):
    data = svc.dashboard_data(conn, "2026-06")
    assert data["metrics"] == {"income": 0.0, "expenses": 0.0, "net": 0.0, "count": 0}
    assert data["recent"] == [] and isinstance(data["budgets"], list)


def test_dashboard_data_aggregates(conn):
    conn.execute("UPDATE categories SET budget_monthly=600 WHERE name='Groceries'")
    _create(conn)                                   # expense 114.98 Groceries
    _create(conn, type="income", category="Salary", total=5000.0)
    data = svc.dashboard_data(conn, "2026-06")
    assert data["metrics"]["income"] == 5000.0
    assert data["metrics"]["expenses"] == 114.98
    assert data["metrics"]["net"] == 4885.02
    assert data["by_category"] == {"Groceries": 114.98}
    groceries = [b for b in data["budgets"] if b["name"] == "Groceries"][0]
    assert groceries["budget"] == 600 and groceries["spent"] == 114.98
    assert len(data["trend"]) == 6 and data["trend"][-1]["month"] == "2026-06"


def test_dashboard_trend_spans_months(conn):
    _create(conn, date="2026-04-10", total=114.98)  # expense in April
    _create(conn, date="2026-06-10", type="income", category="Salary", total=5000.0)
    data = svc.dashboard_data(conn, "2026-06")
    by_month = {t["month"]: t for t in data["trend"]}
    assert by_month["2026-04"] == {"month": "2026-04", "income": 0.0, "expenses": 114.98}
    assert by_month["2026-05"] == {"month": "2026-05", "income": 0.0, "expenses": 0.0}
    assert by_month["2026-06"] == {"month": "2026-06", "income": 5000.0, "expenses": 0.0}


def test_reupload_receipt_resets_link_and_pending(conn, tmp_path):
    img = tmp_path / "r.jpg"
    img.write_bytes(b"fakejpg")
    txn = _create(conn, image_path=str(img))
    conn.execute("UPDATE transactions SET receipt_link='https://drive/x', "
                 "sync_status='synced' WHERE id=?", (txn["id"],))
    result = svc.reupload_receipt(conn, txn["id"])
    assert result["receipt_link"] is None
    assert result["sync_status"] == "pending"
    event = conn.execute(
        "SELECT event FROM audit_log WHERE ref=? ORDER BY id DESC LIMIT 1",
        (str(txn["id"]),)).fetchone()
    assert event["event"] == "receipt_reupload"


def test_reupload_receipt_no_local_image_422(conn):
    from app.errors import AppError
    txn = _create(conn)  # no image_path
    try:
        svc.reupload_receipt(conn, txn["id"])
        assert False, "expected AppError"
    except AppError as exc:
        assert exc.status == 422
        assert exc.code == "no_local_receipt"


def test_reupload_receipt_wrong_profile_404(conn):
    from app.errors import AppError
    from app.services import profiles as prof_svc
    txn = _create(conn)
    other = prof_svc.create_profile(conn, "Inc", "incorporation")
    try:
        svc.reupload_receipt(conn, txn["id"], profile_id=other["id"])
        assert False, "expected AppError"
    except AppError as exc:
        assert exc.status == 404
