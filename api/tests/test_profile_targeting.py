"""Agent operates on a NON-active profile by naming it per call — never needs
to switch the active book (which the UI + other channels share). Covers the
read/edit/delete/summary/recurring tools + the service params they rely on."""
import pytest

from app.agent.tools import build_tools
from app.db import get_db
from app.services import profiles as prof_svc
from app.services import recurring as rec_svc
from app.services import transactions as txn_svc


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


def _setup_business_with_txn(total=12.0):
    """Active stays Personal; a Business profile gets one expense."""
    with get_db() as conn:
        biz = prof_svc.create_profile(conn, "Business", "incorporation")
        txn = txn_svc.create_transaction(conn, {
            "date": "2026-06-05", "type": "expense", "category": "Groceries",
            "total": total, "profile_id": biz["id"]})
    return biz["id"], txn["id"]


@pytest.mark.asyncio
async def test_query_targets_named_profile_without_switching_active(db_path):
    biz_id, _ = _setup_business_with_txn()
    ui = build_tools("ui", lambda spec: None, "ui")
    query = _tool(ui, "query_transactions")

    biz = await query.execute("c", {"profile": "Business"})
    personal = await query.execute("c", {})            # active = Personal

    assert biz.details["count"] == 1
    assert personal.details["count"] == 0
    with get_db() as conn:                              # active never moved
        assert prof_svc.get_profile(conn, prof_svc.active_id(conn))["name"] == "Personal"


@pytest.mark.asyncio
async def test_summary_targets_named_profile(db_path):
    _setup_business_with_txn(total=20.0)
    ui = build_tools("ui", lambda spec: None, "ui")
    summary = await _tool(ui, "get_summary").execute("c", {"profile": "Business"})
    assert summary.details["metrics"]["count"] == 1
    assert summary.details["metrics"]["expenses"] > 0


@pytest.mark.asyncio
async def test_update_targets_named_profile(db_path):
    biz_id, txn_id = _setup_business_with_txn(total=12.0)
    ui = build_tools("ui", lambda spec: None, "ui")
    result = await _tool(ui, "update_transaction").execute("c", {
        "id": txn_id, "total": 99.0, "profile": "Business"})
    assert "error" not in result.details
    with get_db() as conn:
        row = conn.execute("SELECT total FROM transactions WHERE id=?",
                           (txn_id,)).fetchone()
    assert row["total"] == 99.0


@pytest.mark.asyncio
async def test_delete_targets_named_profile(db_path):
    biz_id, txn_id = _setup_business_with_txn()
    ui = build_tools("ui", lambda spec: None, "ui")
    result = await _tool(ui, "delete_transaction").execute("c", {
        "id": txn_id, "profile": "Business"})
    assert "error" not in result.details
    with get_db() as conn:
        gone = conn.execute("SELECT COUNT(*) c FROM transactions WHERE id=?",
                            (txn_id,)).fetchone()["c"]
    assert gone == 0


@pytest.mark.asyncio
async def test_recurring_create_and_list_target_named_profile(db_path):
    with get_db() as conn:
        biz = prof_svc.create_profile(conn, "Business", "incorporation")
    ui = build_tools("ui", lambda spec: None, "ui")
    rec = _tool(ui, "manage_recurring")

    created = await rec.execute("c", {
        "action": "create", "profile": "Business",
        "template": {"type": "expense", "category": "Groceries", "total": 50.0},
        "frequency": "monthly", "next_run": "2026-07-01"})
    assert "error" not in created.details

    biz_list = await rec.execute("c", {"action": "list", "profile": "Business"})
    personal_list = await rec.execute("c", {"action": "list"})   # active Personal
    assert len(biz_list.details) == 1
    assert len(personal_list.details) == 0
    with get_db() as conn:
        assert rec_svc.get_rule(conn, biz_list.details[0]["id"])["profile_id"] == biz["id"]
