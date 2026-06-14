import pytest

from app.agent.tools import build_tools
from app.db import get_db
from app.services import profiles as prof_svc


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


@pytest.mark.asyncio
async def test_record_transaction_targets_named_profile(db_path):
    with get_db() as conn:
        prof_svc.create_profile(conn, "Business", "incorporation")  # id 2
    tools = build_tools("ui", lambda spec: None, "ui")
    record = _tool(tools, "record_transaction")
    result = await record.execute("call-1", {
        "date": "2026-06-05", "type": "expense", "category": "Groceries",
        "total": 12.0, "profile": "Business"})
    assert "error" not in result.details
    with get_db() as conn:
        biz = prof_svc.list_profiles(conn)[1]
        rows = conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE profile_id=?",
            (biz["id"],)).fetchone()["c"]
    assert rows == 1


@pytest.mark.asyncio
async def test_list_and_switch_on_all_channels(db_path):
    with get_db() as conn:
        prof_svc.create_profile(conn, "Business", "incorporation")
    wa = build_tools("whatsapp", lambda spec: None, "whatsapp")
    assert any(t.name == "list_profiles" for t in wa)
    # set_active_profile is available on every channel now (incl. WhatsApp).
    assert any(t.name == "set_active_profile" for t in wa)
    # render_ui stays UI-only.
    assert not any(t.name == "render_ui" for t in wa)

    ui = build_tools("ui", lambda spec: None, "ui")
    listed = await _tool(ui, "list_profiles").execute("c", {})
    assert {"Personal", "Business"} <= {p["name"] for p in listed.details["profiles"]}
    switched = await _tool(ui, "set_active_profile").execute("c", {"profile": "Business"})
    assert switched.details["active"]["name"] == "Business"
    with get_db() as conn:
        assert prof_svc.get_profile(conn, prof_svc.active_id(conn))["name"] == "Business"
