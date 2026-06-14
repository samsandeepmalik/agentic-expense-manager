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
    parent_id: int = Field(default=0, ge=0)


class CategoryReparent(BaseModel):
    parent_id: int = Field(ge=0)


class TaxComponent(BaseModel):
    name: str
    rate: float = Field(ge=0)


class TaxProfileIn(BaseModel):
    name: str = Field(min_length=1)
    components: list[TaxComponent]
    activate: bool = False


@router.get("/api/categories")
async def list_categories(profile_id: int | None = None):
    # profile_id lets callers (e.g. the import grid targeting a non-active book)
    # fetch a specific profile's categories; defaults to the active profile.
    with get_db() as conn:
        return svc.list_categories(conn, profile_id)


@router.post("/api/categories")
async def upsert_category(body: CategoryIn):
    with get_db() as conn:
        return svc.upsert_category(conn, body.name, body.type, body.percent,
                                   body.taxable, body.budget_monthly,
                                   parent_id=body.parent_id)


@router.delete("/api/categories/{category_id}")
async def delete_category(category_id: int):
    with get_db() as conn:
        svc.delete_category(conn, category_id)
    return {"ok": True}


@router.patch("/api/categories/{category_id}")
async def reparent_category(category_id: int, body: CategoryReparent):
    with get_db() as conn:
        return svc.update_category(conn, category_id, parent_id=body.parent_id)


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
