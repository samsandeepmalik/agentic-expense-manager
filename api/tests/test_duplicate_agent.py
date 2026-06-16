import pytest

from app.agent.tools import build_tools
from app.db import get_db
from app.services import transactions as txn_svc


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


def _seed():
    with get_db() as conn:
        txn_svc.create_transaction(conn, {
            "date": "2026-06-05", "type": "expense", "category": "Groceries",
            "total": 50.0, "merchant": "Metro"})


@pytest.mark.asyncio
async def test_record_warns_on_duplicate_without_inserting(db_path):
    _seed()
    rec = _tool(build_tools("ui", lambda s: None, "ui"), "record_transaction")
    result = await rec.execute("c", {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0, "merchant": "Metro"})
    assert result.details.get("duplicate") is True
    assert result.details["match"]["id"] > 0
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM transactions "
                         "WHERE merchant='Metro'").fetchone()["c"]
    assert n == 1  # the duplicate was NOT recorded


@pytest.mark.asyncio
async def test_record_inserts_when_confirm_duplicate(db_path):
    _seed()
    rec = _tool(build_tools("ui", lambda s: None, "ui"), "record_transaction")
    result = await rec.execute("c", {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 50.0, "merchant": "Metro", "confirm_duplicate": True})
    assert result.details.get("duplicate") is not True
    assert result.details.get("id", 0) > 0
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM transactions "
                         "WHERE merchant='Metro'").fetchone()["c"]
    assert n == 2
