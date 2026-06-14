"""Agent tools — thin async wrappers over the SQLite business logic.

`build_tools` is a factory: the render_ui tool needs a per-session sink so
generated UI specs can be streamed out to the active channel.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from pi_agent.agent_core import AgentTool, AgentToolResult, TextContent

from ..db import get_db
from ..services import categories as cat_svc
from ..services import profiles as prof_svc
from ..services import recurring as rec_svc
from ..services import transactions as txn_svc
from ..services.periods import resolve_period

UiSink = Callable[[dict[str, Any]], None]


def _text_result(payload: Any) -> AgentToolResult[Any]:
    return AgentToolResult(
        content=[TextContent(text=json.dumps(payload, default=str))],
        details=payload,
    )


def _resolve_pid(conn, params: dict) -> int:
    """Resolve the target profile id from an optional `profile` name param,
    defaulting to the active profile. Lets category/budget tools target the same
    profile the agent is recording into instead of silently using the active one."""
    wanted = (params.get("profile") or "").strip().lower()
    if not wanted:
        return prof_svc.active_id(conn)
    match = next((p for p in prof_svc.list_profiles(conn)
                  if p["name"].lower() == wanted), None)
    if match is None:
        raise ValueError(f"Unknown profile: {params['profile']}")
    return match["id"]


RECORD_TRANSACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "type": {"type": "string", "enum": ["income", "expense"]},
        "category": {"type": "string"},
        "description": {"type": "string"},
        "merchant": {"type": "string"},
        "total": {"type": "number", "description": "Grand total paid incl. taxes"},
        "image_path": {"type": "string"},
        "source": {"type": "string"},
        "loan": {"type": "boolean",
                 "description": "True if this is a loan (money lent/borrowed), default false"},
        "notes": {"type": "string", "description": "Optional free-text notes about this transaction"},
        "receipt_link": {"type": "string",
                         "description": "External URL to a Drive doc or receipt (optional)"},
        "profile": {"type": "string",
                    "description": "Profile name to record into; defaults to the active profile"},
    },
    "required": ["date", "type", "category", "total"],
}

QUERY_TRANSACTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "start_date": {"type": "string", "description": "YYYY-MM-DD inclusive"},
        "end_date": {"type": "string", "description": "YYYY-MM-DD inclusive"},
        "type": {"type": "string", "enum": ["income", "expense"]},
        "category": {"type": "string"},
        "q": {"type": "string", "description": "Full-text search across merchant, description, notes"},
        "loan": {"type": "boolean", "description": "Filter to loan transactions only (true) or non-loans (false)"},
        "profile": {"type": "string",
                    "description": "Profile name to query; defaults to the active profile"},
    },
}

UPDATE_TRANSACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "description": "Transaction ID to update"},
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "type": {"type": "string", "enum": ["income", "expense"]},
        "category": {"type": "string"},
        "total": {"type": "number"},
        "merchant": {"type": "string"},
        "description": {"type": "string"},
        "notes": {"type": "string"},
        "loan": {"type": "boolean"},
        "profile": {"type": "string",
                    "description": "Profile the transaction lives in; defaults to the active profile"},
    },
    "required": ["id"],
}

DELETE_TRANSACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "description": "Transaction ID to delete"},
        "profile": {"type": "string",
                    "description": "Profile the transaction lives in; defaults to the active profile"},
    },
    "required": ["id"],
}

GET_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "period": {"type": "string"},
        "start_date": {"type": "string"},
        "end_date": {"type": "string"},
        "profile": {"type": "string",
                    "description": "Profile to summarise; defaults to the active profile"},
    },
}

MANAGE_CATEGORIES_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["list", "upsert", "delete"]},
        "name": {"type": "string"},
        "type": {"type": "string", "enum": ["income", "expense"]},
        "percent": {
            "type": "number",
            "description": "Counting formula: % of total counted (0-100)",
        },
        "taxable": {"type": "boolean"},
        "budget_monthly": {"type": ["number", "null"]},
        "parent_id": {"type": "number",
                      "description": "Parent category id for a sub-category; 0 = top-level"},
        "profile": {"type": "string",
                    "description": "Profile name to act on; defaults to the active profile"},
    },
    "required": ["action"],
}

MANAGE_BUDGETS_SCHEMA = {
    "type": "object",
    "properties": {"category": {"type": "string"},
                   "budget_monthly": {"type": ["number", "null"]},
                   "profile": {"type": "string",
                               "description": "Profile name; defaults to the active profile"}},
    "required": ["category"],
}

MANAGE_RECURRING_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["list", "create", "update", "delete"]},
        "template": {"type": "object"}, "frequency": {"type": "string"},
        "next_run": {"type": "string"}, "rule_id": {"type": "number"},
        "active": {"type": "boolean",
                   "description": "For action=update: pause (false) or resume (true) a rule"},
        "profile": {"type": "string",
                    "description": "Profile the rule belongs to; defaults to the active profile"},
    },
    "required": ["action"],
}

LIST_PROFILES_SCHEMA = {"type": "object", "properties": {}}

SET_ACTIVE_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "profile": {"type": "string", "description": "Profile name to activate"},
        "profile_id": {"type": "number"},
    },
}

RENDER_UI_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["metric", "barChart", "lineChart", "pieChart", "table"],
                    },
                    "title": {"type": "string"},
                    "label": {"type": "string"},
                    "value": {"type": ["number", "string"]},
                    "unit": {"type": "string"},
                    "data": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Chart rows, e.g. [{name:'2026-01', income:100, expenses:50}]",
                    },
                    "xKey": {"type": "string", "description": "Key for x-axis / pie label"},
                    "series": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Value keys to plot",
                    },
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array"}},
                },
                "required": ["type"],
            },
        },
    },
    "required": ["components"],
}


def build_tools(channel: str, ui_sink: UiSink, source: str) -> list[AgentTool]:
    async def record_transaction(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    data = {
                        "date": params["date"], "type": params["type"],
                        "category": params["category"],
                        "description": params.get("description", ""),
                        "merchant": params.get("merchant", ""),
                        "total": float(params["total"]),
                        "image_path": params.get("image_path"),
                        "source": params.get("source", source),
                        "loan": bool(params.get("loan", False)),
                        "notes": params.get("notes", ""),
                        "receipt_link": params.get("receipt_link"),
                    }
                    wanted = (params.get("profile") or "").strip().lower()
                    if wanted:
                        match = next((p for p in prof_svc.list_profiles(conn)
                                      if p["name"].lower() == wanted), None)
                        if match is None:
                            raise ValueError(f"Unknown profile: {params['profile']}")
                        data["profile_id"] = match["id"]
                    return txn_svc.create_transaction(conn, data)
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001 — friendly degradation
            return _text_result({"error": f"That didn't work: {exc}"})

    async def query_transactions(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    return txn_svc.list_transactions(
                        conn, start=params.get("start_date"), end=params.get("end_date"),
                        type_=params.get("type"), category=params.get("category"),
                        q=params.get("q"), profile_id=_resolve_pid(conn, params))
            rows = await asyncio.to_thread(work)
            loan_filter = params.get("loan")
            if loan_filter is not None:
                rows = [r for r in rows if bool(r.get("loan")) == bool(loan_filter)]
            return _text_result({"count": len(rows), "transactions": rows[:200]})
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def update_transaction(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    pid = _resolve_pid(conn, params)
                    changes = {k: v for k, v in params.items()
                               if k not in ("id", "profile") and v is not None}
                    return txn_svc.update_transaction(conn, params["id"], changes,
                                                      profile_id=pid)
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def delete_transaction(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    txn_svc.delete_transaction(conn, params["id"],
                                               profile_id=_resolve_pid(conn, params))
                    return {"deleted": params["id"]}
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def get_summary(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    period = params.get("period")
                    if params.get("start_date") and params.get("end_date"):
                        period = f"{params['start_date']}:{params['end_date']}"
                    return txn_svc.dashboard_data(conn, period,
                                                  profile_id=_resolve_pid(conn, params))
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def manage_categories(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    pid = _resolve_pid(conn, params)
                    action = params["action"]
                    if action == "list":
                        return cat_svc.list_categories(conn, profile_id=pid)
                    if action == "upsert":
                        return cat_svc.upsert_category(
                            conn, params["name"], params.get("type", "expense"),
                            float(params.get("percent", 100)),
                            bool(params.get("taxable", True)),
                            params.get("budget_monthly"),
                            parent_id=int(params.get("parent_id", 0)),
                            profile_id=pid)
                    category = cat_svc.find_category_by_name(
                        conn, params["name"], profile_id=pid)
                    if category:
                        cat_svc.delete_category(conn, category["id"], profile_id=pid)
                    return {"deleted": bool(category)}
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def manage_budgets(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    pid = _resolve_pid(conn, params)
                    category = cat_svc.find_category_by_name(
                        conn, params["category"], profile_id=pid)
                    if category is None:
                        return {"error": f"Unknown category {params['category']}"}
                    # Preserve parent_id so a budget on a sub-category updates that
                    # child, not a new top-level row with the same name.
                    return cat_svc.upsert_category(
                        conn, category["name"], category["type"], category["percent"],
                        category["taxable"], params.get("budget_monthly"),
                        parent_id=category["parent_id"], profile_id=pid)
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def manage_recurring(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    pid = _resolve_pid(conn, params)
                    action = params["action"]
                    if action == "list":
                        return rec_svc.list_rules(conn, profile_id=pid)
                    if action == "create":
                        return rec_svc.create_rule(conn, params["template"],
                                                   params["frequency"], params["next_run"],
                                                   profile_id=pid)
                    if action == "update":   # edit fields or pause/resume (active)
                        changes = {k: params[k] for k in
                                   ("template", "frequency", "next_run", "active")
                                   if params.get(k) is not None}
                        return rec_svc.update_rule(
                            conn, int(params["rule_id"]), changes, profile_id=pid)
                    rec_svc.delete_rule(conn, int(params["rule_id"]), profile_id=pid)
                    return {"deleted": True}
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def list_profiles(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    return {"profiles": prof_svc.list_profiles(conn)}
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def set_active_profile(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    pid = params.get("profile_id")
                    if pid is None:
                        wanted = (params.get("profile") or "").strip().lower()
                        match = next((p for p in prof_svc.list_profiles(conn)
                                      if p["name"].lower() == wanted), None)
                        if match is None:
                            return {"error": f"Unknown profile: {params.get('profile')}"}
                        pid = match["id"]
                    return {"active": prof_svc.set_active(conn, int(pid))}
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def render_ui(tool_call_id, params, abort_event=None, on_update=None):
        try:
            spec = {"title": params.get("title", ""), "components": params["components"]}
            ui_sink(spec)
            return _text_result({"rendered": True, "components": len(spec["components"])})
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    tools = [
        AgentTool(
            name="record_transaction",
            label="Record transaction",
            description=(
                "Record an income or expense. Provide the TOTAL PAID — taxes "
                "and the counted amount are derived server-side from the "
                "category's taxable flag and the active tax profile."
            ),
            parameters=RECORD_TRANSACTION_SCHEMA,
            execute=record_transaction,
        ),
        AgentTool(
            name="query_transactions",
            label="Query transactions",
            description="List transactions with optional date/type/category/text/loan filters.",
            parameters=QUERY_TRANSACTIONS_SCHEMA,
            execute=query_transactions,
        ),
        AgentTool(
            name="update_transaction",
            label="Update transaction",
            description=(
                "Edit an existing transaction by id. Pass only the fields to change "
                "(date, type, category, total, merchant, description, notes, loan). "
                "Taxes are recomputed automatically."
            ),
            parameters=UPDATE_TRANSACTION_SCHEMA,
            execute=update_transaction,
        ),
        AgentTool(
            name="delete_transaction",
            label="Delete transaction",
            description=(
                "Permanently delete a transaction by id. Always confirm with the user before calling."
            ),
            parameters=DELETE_TRANSACTION_SCHEMA,
            execute=delete_transaction,
        ),
        AgentTool(
            name="get_summary",
            label="Get summary",
            description=(
                "Totals (income, expenses, net), expense breakdown by category, "
                "budgets and monthly trend for an optional period or date range."
            ),
            parameters=GET_SUMMARY_SCHEMA,
            execute=get_summary,
        ),
        AgentTool(
            name="manage_categories",
            label="Manage categories",
            description=(
                "List, add/update (upsert) or delete income/expense categories. "
                "Each category has a percent (0-100) counting formula, a taxable "
                "flag and an optional monthly budget."
            ),
            parameters=MANAGE_CATEGORIES_SCHEMA,
            execute=manage_categories,
        ),
        AgentTool(
            name="manage_budgets",
            label="Manage budgets",
            description="Set or clear the monthly budget for a category.",
            parameters=MANAGE_BUDGETS_SCHEMA,
            execute=manage_budgets,
        ),
        AgentTool(
            name="manage_recurring",
            label="Manage recurring",
            description=(
                "List, create, update (edit fields or pause/resume via active) or "
                "delete recurring transaction rules (e.g. rent, salary) that "
                "auto-record on a weekly/biweekly/monthly schedule."
            ),
            parameters=MANAGE_RECURRING_SCHEMA,
            execute=manage_recurring,
        ),
        AgentTool(
            name="list_profiles",
            label="List profiles",
            description="List all profiles (Personal, Business, etc.) and which is active.",
            parameters=LIST_PROFILES_SCHEMA,
            execute=list_profiles,
        ),
        # Available on every channel (incl. WhatsApp) so a chat/WhatsApp user can
        # switch the active book — the prompt asks for the profile per record, so
        # they need a way to change it.
        AgentTool(
            name="set_active_profile",
            label="Switch profile",
            description=("Switch the active profile by name or id. Note: this changes "
                         "the active profile everywhere (web + WhatsApp)."),
            parameters=SET_ACTIVE_PROFILE_SCHEMA,
            execute=set_active_profile,
        ),
    ]

    if channel == "ui":
        tools.append(
            AgentTool(
                name="render_ui",
                label="Render UI",
                description=(
                    "Render charts/tables/metric cards on the user's dashboard. "
                    "Use for trends, breakdowns and summaries."
                ),
                parameters=RENDER_UI_SCHEMA,
                execute=render_ui,
            )
        )

    return tools
