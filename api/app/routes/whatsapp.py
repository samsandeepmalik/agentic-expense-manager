"""WhatsApp accounts: pairing QR, refresh, unpair, allowlist, legacy endpoints."""

from __future__ import annotations

import re

from fastapi import APIRouter
from pydantic import BaseModel

from ..channels.whatsapp import whatsapp
from ..db import get_db, get_setting, set_setting
from ..settings_keys import WHATSAPP_ALLOWED_SENDERS

router = APIRouter()

_ALLOWED_KEY = WHATSAPP_ALLOWED_SENDERS


class AllowedIn(BaseModel):
    number: str


def _normalize(number: str) -> str:
    return re.sub(r"\D", "", number)


def _read_allowed(conn) -> list[str]:
    return get_setting(conn, _ALLOWED_KEY) or []


@router.get("/api/whatsapp/allowed")
async def list_allowed():
    with get_db() as conn:
        return {"allowed": _read_allowed(conn)}


@router.post("/api/whatsapp/allowed")
async def add_allowed(body: AllowedIn):
    number = _normalize(body.number)
    with get_db() as conn:
        allowed = _read_allowed(conn)
        if number and number not in allowed:
            allowed.append(number)
        set_setting(conn, _ALLOWED_KEY, allowed)
        return {"allowed": allowed}


@router.delete("/api/whatsapp/allowed/{number}")
async def remove_allowed(number: str):
    number = _normalize(number)
    with get_db() as conn:
        allowed = [n for n in _read_allowed(conn) if n != number]
        set_setting(conn, _ALLOWED_KEY, allowed)
        return {"allowed": allowed}


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
