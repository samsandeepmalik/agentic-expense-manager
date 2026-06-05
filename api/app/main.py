"""FastAPI application — local-first backend for UI and WhatsApp channels."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agent.runtime import run_to_completion, sessions
from .channels.whatsapp import whatsapp
from .config import config
from .db import init_db
from .errors import register_error_handler
from .routes import (categories, chat, dashboard, google_auth, imports,
                     recurring, sync, transactions)
from .routes import whatsapp as whatsapp_routes
from .services.receipts import build_receipt_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _handle_whatsapp_message(chat_id, text, image_bytes, image_mime):
    session = sessions.get(f"wa:{chat_id}", channel="whatsapp")
    try:
        prompt = text
        if image_bytes:
            prompt = await build_receipt_prompt(text, image_bytes,
                                                image_mime or "image/jpeg")
        if not prompt.strip():
            return 'Send a receipt photo or e.g. "spent $20 on groceries".'
        return await run_to_completion(session, prompt)
    except Exception as exc:  # noqa: BLE001
        logger.exception("WhatsApp pipeline failed")
        return f"Sorry, something went wrong: {exc}"


async def _scheduler_loop():
    """Hourly reconcile, daily recurring run, Sunday 18:00 weekly summary."""
    from .db import get_db
    from .services.recurring import run_due_rules
    from .services.sync import _safe_reconcile, sync_enabled

    last_summary_day: date | None = None
    while True:
        try:
            with get_db() as conn:
                run_due_rules(conn)
            if sync_enabled():
                await asyncio.to_thread(_safe_reconcile)
            now = datetime.now()
            if (now.weekday() == 6 and now.hour >= 18
                    and last_summary_day != now.date()):
                await whatsapp.send_weekly_summary()
                last_summary_day = now.date()
        except Exception:  # noqa: BLE001
            logger.exception("Scheduler tick failed")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    whatsapp.set_handler(_handle_whatsapp_message)
    try:
        await whatsapp.start()
    except Exception:  # noqa: BLE001
        logger.exception("WhatsApp channel failed to start")
    scheduler = asyncio.create_task(_scheduler_loop())
    yield
    scheduler.cancel()


app = FastAPI(title="Expense Manager API", lifespan=lifespan)
register_error_handler(app)

app.add_middleware(CORSMiddleware, allow_origins=[config.web_origin],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

for module in (chat, dashboard, transactions, categories, recurring,
               imports, sync, whatsapp_routes, google_auth):
    app.include_router(module.router)


@app.get("/api/health")
async def health():
    return {"ok": True}
