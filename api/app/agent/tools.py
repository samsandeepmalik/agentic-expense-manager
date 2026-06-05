"""Agent tools — thin async wrappers over the sheets business logic.

`build_tools` is a factory: the render_ui tool needs a per-session sink so
generated UI specs can be streamed out to the active channel.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from pi_agent.agent_core import AgentTool, AgentToolResult, TextContent

from ..services import sheets

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
        "amount": {"type": "number", "description": "Pre-tax amount"},
        "gst": {"type": "number"},
        "qst": {"type": "number"},
        "total": {"type": "number", "description": "Grand total incl. taxes"},
        "image_link": {"type": "string"},
        "source": {"type": "string"},
    },
    "required": ["date", "type", "category", "amount"],
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
    async def record_transaction(
        tool_call_id: str,
        params: dict[str, Any],
        abort_event: asyncio.Event | None = None,
        on_update: Any = None,
    ) -> AgentToolResult[Any]:
        result = await asyncio.to_thread(
            sheets.record_transaction,
            date=params["date"],
            type_=params["type"],
            category=params["category"],
            description=params.get("description", ""),
            merchant=params.get("merchant", ""),
            amount=float(params["amount"]),
            gst=float(params.get("gst", 0) or 0),
            qst=float(params.get("qst", 0) or 0),
            total=(float(params["total"]) if params.get("total") is not None else None),
            image_link=params.get("image_link", ""),
            source=params.get("source", source),
        )
        return _text_result(result)

    async def query_transactions(
        tool_call_id: str,
        params: dict[str, Any],
        abort_event: asyncio.Event | None = None,
        on_update: Any = None,
    ) -> AgentToolResult[Any]:
        result = await asyncio.to_thread(
            sheets.list_transactions,
            start_date=params.get("start_date"),
            end_date=params.get("end_date"),
            type_=params.get("type"),
            category=params.get("category"),
        )
        return _text_result({"count": len(result), "transactions": result})

    async def get_summary(
        tool_call_id: str,
        params: dict[str, Any],
        abort_event: asyncio.Event | None = None,
        on_update: Any = None,
    ) -> AgentToolResult[Any]:
        result = await asyncio.to_thread(
            sheets.summarize,
            start_date=params.get("start_date"),
            end_date=params.get("end_date"),
        )
        return _text_result(result)

    async def manage_categories(
        tool_call_id: str,
        params: dict[str, Any],
        abort_event: asyncio.Event | None = None,
        on_update: Any = None,
    ) -> AgentToolResult[Any]:
        action = params["action"]
        if action == "list":
            result: Any = await asyncio.to_thread(sheets.list_categories)
        elif action == "upsert":
            result = await asyncio.to_thread(
                sheets.upsert_category,
                params["name"],
                params.get("type", "expense"),
                float(params.get("percent", 100)),
            )
        elif action == "delete":
            deleted = await asyncio.to_thread(sheets.delete_category, params["name"])
            result = {"deleted": deleted, "name": params["name"]}
        else:  # unreachable per schema
            raise ValueError(f"Unknown action: {action}")
        return _text_result(result)

    async def render_ui(
        tool_call_id: str,
        params: dict[str, Any],
        abort_event: asyncio.Event | None = None,
        on_update: Any = None,
    ) -> AgentToolResult[Any]:
        spec = {"title": params.get("title", ""), "components": params["components"]}
        ui_sink(spec)
        return _text_result({"rendered": True, "components": len(spec["components"])})

    tools = [
        AgentTool(
            name="record_transaction",
            label="Record transaction",
            description=(
                "Append an income or expense entry to the Google Sheet. The "
                "counted amount is computed from the category percent formula."
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
                "Totals (income, expenses, net), expense breakdown by category "
                "and monthly trend for an optional date range."
            ),
            parameters=GET_SUMMARY_SCHEMA,
            execute=get_summary,
        ),
        AgentTool(
            name="manage_categories",
            label="Manage categories",
            description=(
                "List, add/update (upsert) or delete income/expense categories. "
                "Each category has a percent (0-100) counting formula, default 100."
            ),
            parameters=MANAGE_CATEGORIES_SCHEMA,
            execute=manage_categories,
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
