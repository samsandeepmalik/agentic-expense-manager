"""App settings endpoints (currently: OCR provider selection)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..db import get_db, set_setting
from ..errors import AppError
from ..settings_keys import OCR_PROVIDER
from ..services import vision

router = APIRouter()


class OcrIn(BaseModel):
    provider: str


def _state() -> dict:
    return {"provider": vision.current_provider(),
            "available": vision.available_providers()}


@router.get("/api/settings/ocr")
async def get_ocr():
    return _state()


@router.post("/api/settings/ocr")
async def set_ocr(body: OcrIn):
    if body.provider not in vision.PROVIDERS:
        raise AppError("invalid_provider",
                       f"OCR provider must be one of {', '.join(vision.PROVIDERS)}")
    with get_db() as conn:
        set_setting(conn, OCR_PROVIDER, body.provider)
    return _state()
