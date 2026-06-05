from datetime import date

from app.services import recurring as svc
from app.services import transactions as txn_svc


def test_next_run_monthly_clamps_short_months():
    assert svc.next_run_after(date(2026, 1, 31), "monthly") == date(2026, 2, 28)
    assert svc.next_run_after(date(2026, 6, 15), "monthly") == date(2026, 7, 15)
    assert svc.next_run_after(date(2026, 6, 1), "weekly") == date(2026, 6, 8)
    assert svc.next_run_after(date(2026, 6, 1), "biweekly") == date(2026, 6, 15)


def test_run_due_rules_records_and_advances(conn):
    rule = svc.create_rule(conn, template={
        "type": "expense", "category": "Rent", "total": 1500.0,
        "merchant": "Landlord", "description": "Monthly rent",
    }, frequency="monthly", next_run="2026-06-01")

    created = svc.run_due_rules(conn, today=date(2026, 6, 5))
    assert created == 1
    txns = txn_svc.list_transactions(conn)
    assert txns[0]["category"] == "Rent" and txns[0]["source"] == "recurring"

    rules = svc.list_rules(conn)
    assert rules[0]["next_run"] == "2026-07-01"

    # Running again same day: nothing due
    assert svc.run_due_rules(conn, today=date(2026, 6, 5)) == 0
