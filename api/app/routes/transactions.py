"""Transactions API: CRUD, bulk, CSV export, receipt images."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from ..db import get_db
from ..errors import AppError
from ..services import transactions as svc
from ..services.periods import resolve_period

router = APIRouter()


class TransactionIn(BaseModel):
    date: str
    type: str = Field(pattern="^(income|expense)$")
    category: str = ""          # name (legacy) — category_id preferred when set
    category_id: int | None = None
    total: float = Field(gt=0)
    merchant: str = ""
    description: str = ""
    notes: str = ""
    loan: bool = False
    receipt_link: str | None = None
    confirm_duplicate: bool = False


class TransactionPatch(BaseModel):
    date: str | None = None
    type: str | None = None
    category: str | None = None
    category_id: int | None = None
    total: float | None = None
    merchant: str | None = None
    description: str | None = None
    notes: str | None = None
    loan: bool | None = None
    receipt_link: str | None = None  # explicitly nullable — null clears the link

    @field_validator("date", "type", "total", "merchant", "description", "notes",
                     mode="before")
    @classmethod
    def reject_explicit_null(cls, v: object, info) -> object:
        if v is None:
            raise ValueError(f"{info.field_name} cannot be set to null")
        return v


class PreviewIn(BaseModel):
    type: str = Field(pattern="^(income|expense)$")
    category: str = ""
    category_id: int | None = None
    total: float = Field(gt=0)


class BulkIn(BaseModel):
    ids: list[int]
    action: str = Field(pattern="^(delete|recategorize)$")
    category: str | None = None
    category_id: int | None = None


@router.get("/api/transactions")
async def list_transactions(period: str | None = None,
                            type: str | None = Query(default=None),
                            category: str | None = None, q: str | None = None,
                            limit: int = 100, offset: int = 0):
    start, end = resolve_period(period) if period else (None, None)
    with get_db() as conn:
        return svc.list_transactions(conn, start=start, end=end, type_=type,
                                     category=category, q=q, limit=limit, offset=offset)


@router.post("/api/transactions")
async def create_transaction(body: TransactionIn):
    with get_db() as conn:
        return svc.create_transaction(conn, body.model_dump(), check_duplicate=True)


@router.post("/api/transactions/preview")
async def preview_transaction(body: PreviewIn):
    with get_db() as conn:
        return svc.preview_transaction(conn, body.model_dump())


@router.patch("/api/transactions/{txn_id}")
async def update_transaction(txn_id: int, body: TransactionPatch):
    # exclude_unset=True: only fields the client explicitly sent are included,
    # so an explicit null clears the value (e.g. receipt_link: null removes the
    # link) while omitted fields are left unchanged. The old "if v is not None"
    # filter silently dropped null, making it impossible to clear receipt_link.
    changes = body.model_dump(exclude_unset=True)
    with get_db() as conn:
        return svc.update_transaction(conn, txn_id, changes)


@router.delete("/api/transactions/{txn_id}")
async def delete_transaction(txn_id: int):
    with get_db() as conn:
        svc.delete_transaction(conn, txn_id)
    return {"ok": True}


@router.post("/api/transactions/{txn_id}/reupload-receipt")
async def reupload_receipt(txn_id: int):
    with get_db() as conn:
        return svc.reupload_receipt(conn, txn_id)


@router.post("/api/transactions/bulk")
async def bulk(body: BulkIn):
    with get_db() as conn:
        count = svc.bulk_action(conn, body.ids, body.action, body.category,
                                category_id=body.category_id)
    return {"ok": True, "affected": count}


@router.get("/api/transactions/export.csv")
async def export_csv():
    with get_db() as conn:
        return PlainTextResponse(svc.export_csv(conn), media_type="text/csv")


@router.get("/api/receipts/{txn_id}")
async def receipt_image(txn_id: int):
    with get_db() as conn:
        txn = svc.get_transaction(conn, txn_id)
    if not txn["image_path"]:
        raise AppError("no_receipt", "Transaction has no receipt image", 404)
    return FileResponse(txn["image_path"])


@router.get("/api/receipts/{txn_id}/preview")
async def receipt_preview(txn_id: int):
    with get_db() as conn:
        txn = svc.get_transaction(conn, txn_id)
    if not txn["image_path"]:
        raise AppError("no_receipt", "Transaction has no receipt image", 404)
    path = Path(txn["image_path"])
    if path.suffix.lower() == ".pdf":
        preview = path.with_suffix(".preview.png")
        if preview.exists():
            return FileResponse(str(preview))
    return FileResponse(txn["image_path"])
