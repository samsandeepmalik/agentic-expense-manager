from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from ..errors import AppError
from ..services import imports as svc
from ..services import receipts as receipts_svc

router = APIRouter()

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


class ApproveIn(BaseModel):
    indexes: list[int] | None = None   # None = all non-skipped
    rows: list[dict] | None = None     # edited rows from the review grid


@router.post("/api/imports")
async def upload(file: UploadFile = File(...),
                 profile_id: int | None = Form(None)):
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise AppError("file_too_large",
                       f"Upload exceeds the 20 MB limit ({len(data) // 1024 // 1024} MB received)",
                       413)
    return await svc.start_import(file.filename or "upload", data, profile_id)


@router.get("/api/imports/{import_id}")
async def get_import(import_id: int):
    return svc.get_import(import_id)


@router.post("/api/imports/{import_id}/approve")
async def approve(import_id: int, body: ApproveIn):
    result = svc.approve_import(import_id, body.indexes, body.rows)
    asyncio.get_running_loop().run_in_executor(
        None, receipts_svc.download_linked_receipts)
    return result
