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
from ..services import recurring as rec_svc
from ..services import transactions as txn_svc
from ..services.periods import resolve_period

UiSink = Callable[[dict[str, Any]], None]


def _text_result(payload: Any) -> AgentToolResult[Any]:
    return AgentToolResult(
        content=[TextContent(text=json.dumps(payload, default=str))],
        details=payload,
    )


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
    },
}

GET_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "period": {"type": "string"},
        "start_date": {"type": "string"},
        "end_date": {"type": "string"},
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
    },
    "required": ["action"],
}

MANAGE_BUDGETS_SCHEMA = {
    "type": "object",
    "properties": {"category": {"type": "string"},
                   "budget_monthly": {"type": ["number", "null"]}},
    "required": ["category"],
}

MANAGE_RECURRING_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["list", "create", "delete"]},
        "template": {"type": "object"}, "frequency": {"type": "string"},
        "next_run": {"type": "string"}, "rule_id": {"type": "number"},
    },
    "required": ["action"],
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
                    return txn_svc.create_transaction(conn, {
                        "date": params["date"], "type": params["type"],
                        "category": params["category"],
                        "description": params.get("description", ""),
                        "merchant": params.get("merchant", ""),
                        "total": float(params["total"]),
                        "image_path": params.get("image_path"),
                        "source": params.get("source", source),
                        "loan": bool(params.get("loan", False)),
                    })
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001 — friendly degradation
            return _text_result({"error": f"That didn't work: {exc}"})

    async def query_transactions(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    return txn_svc.list_transactions(
                        conn, start=params.get("start_date"), end=params.get("end_date"),
                        type_=params.get("type"), category=params.get("category"))
            rows = await asyncio.to_thread(work)
            return _text_result({"count": len(rows), "transactions": rows[:200]})
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def get_summary(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    period = params.get("period")
                    if params.get("start_date") and params.get("end_date"):
                        period = f"{params['start_date']}:{params['end_date']}"
                    return txn_svc.dashboard_data(conn, period)
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def manage_categories(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    action = params["action"]
                    if action == "list":
                        return cat_svc.list_categories(conn)
                    if action == "upsert":
                        return cat_svc.upsert_category(
                            conn, params["name"], params.get("type", "expense"),
                            float(params.get("percent", 100)),
                            bool(params.get("taxable", True)),
                            params.get("budget_monthly"))
                    category = cat_svc.find_category_by_name(conn, params["name"])
                    if category:
                        cat_svc.delete_category(conn, category["id"])
                    return {"deleted": bool(category)}
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def manage_budgets(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    category = cat_svc.find_category_by_name(conn, params["category"])
                    if category is None:
                        return {"error": f"Unknown category {params['category']}"}
                    return cat_svc.upsert_category(
                        conn, category["name"], category["type"], category["percent"],
                        category["taxable"], params.get("budget_monthly"))
            return _text_result(await asyncio.to_thread(work))
        except Exception as exc:  # noqa: BLE001
            return _text_result({"error": f"That didn't work: {exc}"})

    async def manage_recurring(tool_call_id, params, abort_event=None, on_update=None):
        try:
            def work():
                with get_db() as conn:
                    action = params["action"]
                    if action == "list":
                        return rec_svc.list_rules(conn)
                    if action == "create":
                        return rec_svc.create_rule(conn, params["template"],
                                                   params["frequency"], params["next_run"])
                    rec_svc.delete_rule(conn, int(params["rule_id"]))
                    return {"deleted": True}
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
            description="List transactions with optional date/type/category filters.",
            parameters=QUERY_TRANSACTIONS_SCHEMA,
            execute=query_transactions,
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
                "List, create or delete recurring transaction rules (e.g. rent, "
                "salary) that auto-record on a weekly/biweekly/monthly schedule."
            ),
            parameters=MANAGE_RECURRING_SCHEMA,
            execute=manage_recurring,
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
