"""Recurring rule configuration."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import get_db
from ..services import recurring as svc

router = APIRouter()


class RuleIn(BaseModel):
    template: dict
    frequency: str = Field(pattern="^(weekly|biweekly|monthly)$")
    next_run: str


class RulePatch(BaseModel):
    template: dict | None = None
    frequency: str | None = None
    next_run: str | None = None
    active: bool | None = None


@router.get("/api/recurring")
async def list_rules():
    with get_db() as conn:
        return svc.list_rules(conn)


@router.post("/api/recurring")
async def create_rule(body: RuleIn):
    with get_db() as conn:
        return svc.create_rule(conn, body.template, body.frequency, body.next_run)


@router.patch("/api/recurring/{rule_id}")
async def update_rule(rule_id: int, body: RulePatch):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    with get_db() as conn:
        return svc.update_rule(conn, rule_id, changes)


@router.delete("/api/recurring/{rule_id}")
async def delete_rule(rule_id: int):
    with get_db() as conn:
        svc.delete_rule(conn, rule_id)
    return {"ok": True}
