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


def test_update_recomputes(conn):
    txn = _create(conn)
    updated = svc.update_transaction(conn, txn["id"], {"total": 229.96})
    assert updated["amount"] == 200.01 or updated["amount"] == 200.0


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
