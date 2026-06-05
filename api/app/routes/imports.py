from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from ..services import imports as svc

router = APIRouter()


class ApproveIn(BaseModel):
    indexes: list[int] | None = None   # None = all non-skipped


@router.post("/api/imports")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    return await svc.start_import(file.filename or "upload", data)


@router.get("/api/imports/{import_id}")
async def get_import(import_id: int):
    return svc.get_import(import_id)


@router.post("/api/imports/{import_id}/approve")
async def approve(import_id: int, body: ApproveIn):
    return svc.approve_import(import_id, body.indexes)
