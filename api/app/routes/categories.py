"""Category configuration endpoints (name, type, percent formula)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import sheets

router = APIRouter()


class CategoryIn(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(pattern="^(income|expense)$")
    percent: float = Field(default=100.0, ge=0, le=100)


@router.get("/api/categories")
async def list_categories():
    return await asyncio.to_thread(sheets.list_categories)


@router.post("/api/categories")
async def upsert_category(category: CategoryIn):
    return await asyncio.to_thread(
        sheets.upsert_category, category.name, category.type, category.percent
    )


@router.delete("/api/categories/{name}")
async def delete_category(name: str):
    deleted = await asyncio.to_thread(sheets.delete_category, name)
    return {"deleted": deleted}
