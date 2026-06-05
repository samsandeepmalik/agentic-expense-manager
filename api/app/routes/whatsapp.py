"""WhatsApp connect status + QR for the dashboard."""

from __future__ import annotations

from fastapi import APIRouter

from ..channels.whatsapp import whatsapp

router = APIRouter()


@router.get("/api/whatsapp/status")
async def status():
    return {"status": whatsapp.status}


@router.get("/api/whatsapp/qr")
async def qr():
    return whatsapp.current_qr()
