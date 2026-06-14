"""Tests for agent tool capability gaps: notes+receipt_link on record,
update_transaction, delete_transaction, and query filters (q, loan).

We test the underlying service calls that the tools delegate to, which avoids
the need to run async tool handlers in a synchronous pytest environment.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_category(conn, name="Food", type_="expense"):
    from app.services import categories as cat_svc
    return cat_svc.upsert_category(conn, name, type_, 100, False, None)


def _make_txn(conn, *, category="Food", notes="", receipt_link=None, loan=False,
              merchant="Cafe", total=10.0, date="2026-01-15"):
    from app.services import transactions as txn_svc
    return txn_svc.create_transaction(conn, {
        "date": date,
        "type": "expense",
        "category": category,
        "merchant": merchant,
        "total": total,
        "notes": notes,
        "receipt_link": receipt_link,
        "loan": loan,
    })


# ---------------------------------------------------------------------------
# 1. record_transaction persists notes + receipt_link
# ---------------------------------------------------------------------------

def test_create_transaction_persists_notes_and_receipt_link(conn):
    _make_category(conn)
    txn = _make_txn(conn, notes="work lunch", receipt_link="https://drive.example.com/receipt1")
    assert txn["notes"] == "work lunch"
    assert txn["receipt_link"] == "https://drive.example.com/receipt1"


def test_create_transaction_empty_notes_and_no_receipt_link(conn):
    _make_category(conn)
    txn = _make_txn(conn)
    assert txn["notes"] == ""
    assert txn["receipt_link"] is None


# ---------------------------------------------------------------------------
# 2. update_transaction changes a field and sets sync_status = pending
# ---------------------------------------------------------------------------

def test_update_transaction_changes_merchant_and_marks_pending(conn):
    _make_category(conn)
    txn = _make_txn(conn, merchant="OldShop")
    txn_id = txn["id"]

    from app.services import transactions as txn_svc
    updated = txn_svc.update_transaction(conn, txn_id, {"merchant": "NewShop"})
    assert updated["merchant"] == "NewShop"
    assert updated["sync_status"] == "pending"


def test_update_transaction_notes(conn):
    _make_category(conn)
    txn = _make_txn(conn, notes="original")
    from app.services import transactions as txn_svc
    updated = txn_svc.update_transaction(conn, txn["id"], {"notes": "revised note"})
    assert updated["notes"] == "revised note"


def test_update_transaction_total_recomputes(conn):
    _make_category(conn)
    txn = _make_txn(conn, total=10.0)
    from app.services import transactions as txn_svc
    updated = txn_svc.update_transaction(conn, txn["id"], {"total": 20.0})
    assert updated["total"] == 20.0


# ---------------------------------------------------------------------------
# 3. delete_transaction removes the transaction
# ---------------------------------------------------------------------------

def test_delete_transaction_removes_row(conn):
    _make_category(conn)
    txn = _make_txn(conn)
    txn_id = txn["id"]

    from app.services import transactions as txn_svc
    from app.errors import AppError
    txn_svc.delete_transaction(conn, txn_id)

    with pytest.raises(AppError) as exc_info:
        txn_svc.get_transaction(conn, txn_id)
    assert exc_info.value.code == "transaction_not_found"


def test_delete_transaction_nonexistent_raises(conn):
    from app.services import transactions as txn_svc
    from app.errors import AppError
    with pytest.raises(AppError) as exc_info:
        txn_svc.delete_transaction(conn, 99999)
    assert exc_info.value.code == "transaction_not_found"


# ---------------------------------------------------------------------------
# 4. query_transactions: q filter searches merchant/description/notes
# ---------------------------------------------------------------------------

def test_query_q_filter_matches_merchant(conn):
    _make_category(conn)
    _make_txn(conn, merchant="Starbucks", notes="")
    _make_txn(conn, merchant="McDonalds", notes="")

    from app.services import transactions as txn_svc
    rows = txn_svc.list_transactions(conn, q="Starbucks")
    assert len(rows) == 1
    assert rows[0]["merchant"] == "Starbucks"


def test_query_q_filter_matches_notes(conn):
    _make_category(conn)
    _make_txn(conn, merchant="Shop", notes="client dinner")
    _make_txn(conn, merchant="Shop", notes="personal lunch")

    from app.services import transactions as txn_svc
    rows = txn_svc.list_transactions(conn, q="client")
    assert len(rows) == 1
    assert rows[0]["notes"] == "client dinner"


def test_query_q_filter_case_insensitive(conn):
    _make_category(conn)
    _make_txn(conn, merchant="Walmart", notes="")

    from app.services import transactions as txn_svc
    rows = txn_svc.list_transactions(conn, q="walmart")
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 5. loan filter (applied in tool handler; test the service + filter logic)
# ---------------------------------------------------------------------------

def test_loan_filter_keeps_only_loans(conn):
    _make_category(conn)
    _make_txn(conn, merchant="LoanGuy", loan=True)
    _make_txn(conn, merchant="NormalGuy", loan=False)

    from app.services import transactions as txn_svc
    all_rows = txn_svc.list_transactions(conn)
    assert len(all_rows) == 2

    # Replicate the tool handler filter
    loan_rows = [r for r in all_rows if bool(r.get("loan")) is True]
    assert len(loan_rows) == 1
    assert loan_rows[0]["merchant"] == "LoanGuy"


def test_loan_filter_excludes_loans(conn):
    _make_category(conn)
    _make_txn(conn, merchant="LoanGuy", loan=True)
    _make_txn(conn, merchant="NormalGuy", loan=False)

    from app.services import transactions as txn_svc
    all_rows = txn_svc.list_transactions(conn)
    non_loan_rows = [r for r in all_rows if bool(r.get("loan")) is False]
    assert len(non_loan_rows) == 1
    assert non_loan_rows[0]["merchant"] == "NormalGuy"
