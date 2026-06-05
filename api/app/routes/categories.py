"""Category + tax profile configuration endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import get_db
from ..services import categories as svc

router = APIRouter()


class CategoryIn(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(pattern="^(income|expense)$")
    percent: float = Field(default=100.0, ge=0, le=100)
    taxable: bool = True
    budget_monthly: float | None = Field(default=None, ge=0)


class TaxComponent(BaseModel):
    name: str
    rate: float = Field(ge=0)


class TaxProfileIn(BaseModel):
    name: str = Field(min_length=1)
    components: list[TaxComponent]
    activate: bool = False


@router.get("/api/categories")
async def list_categories():
    with get_db() as conn:
        return svc.list_categories(conn)


@router.post("/api/categories")
async def upsert_category(body: CategoryIn):
    with get_db() as conn:
        return svc.upsert_category(conn, body.name, body.type, body.percent,
                                   body.taxable, body.budget_monthly)


@router.delete("/api/categories/{category_id}")
async def delete_category(category_id: int):
    with get_db() as conn:
        svc.delete_category(conn, category_id)
    return {"ok": True}


@router.get("/api/tax-profiles")
async def list_tax_profiles():
    with get_db() as conn:
        return svc.list_tax_profiles(conn)


@router.post("/api/tax-profiles")
async def save_tax_profile(body: TaxProfileIn):
    with get_db() as conn:
        return svc.save_tax_profile(
            conn, body.name, [c.model_dump() for c in body.components], body.activate
        )
