"""WhatsApp accounts: pairing QR, refresh, unpair, plus legacy endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from ..channels.whatsapp import whatsapp

router = APIRouter()


@router.get("/api/whatsapp/accounts")
async def list_accounts():
    return whatsapp.list_accounts()


@router.post("/api/whatsapp/accounts")
async def add_account():
    return await whatsapp.add_account()


@router.get("/api/whatsapp/accounts/{account_id}/qr")
async def account_qr(account_id: str):
    return whatsapp.get(account_id).current_qr()


@router.post("/api/whatsapp/accounts/{account_id}/refresh")
async def refresh_account(account_id: str):
    return await whatsapp.get(account_id).refresh_qr()


@router.delete("/api/whatsapp/accounts/{account_id}")
async def remove_account(account_id: str):
    await whatsapp.remove_account(account_id)
    return {"ok": True}


# --- Legacy single-account endpoints (kept for compatibility) ---------------


@router.get("/api/whatsapp/status")
async def status():
    manager = whatsapp.first()
    return {"status": manager.status if manager else "disconnected"}


@router.get("/api/whatsapp/qr")
async def qr():
    manager = whatsapp.first()
    if manager is None:
        return {"status": "disconnected", "qr": None}
    return manager.current_qr()
