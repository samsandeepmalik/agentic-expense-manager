"""FastAPI application — single backend API for UI and WhatsApp channels."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agent.runtime import run_to_completion, sessions
from .channels.whatsapp import whatsapp
from .config import config
from .routes import categories, chat, dashboard, google_auth
from .routes import whatsapp as whatsapp_routes
from .services.google_client import GoogleNotConnectedError
from .services.receipts import build_receipt_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _handle_whatsapp_message(
    chat_id: str, text: str, image_bytes: bytes | None, image_mime: str | None
) -> str:
    """Route WhatsApp messages through the same agent pipeline as the UI."""
    session = sessions.get(f"wa:{chat_id}", channel="whatsapp")
    try:
        prompt = text
        if image_bytes:
            prompt = await build_receipt_prompt(text, image_bytes, image_mime or "image/jpeg")
        if not prompt.strip():
            return "Send me a receipt photo or a message like \"spent $20 on groceries\"."
        return await run_to_completion(session, prompt)
    except GoogleNotConnectedError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("WhatsApp pipeline failed")
        return f"Sorry, something went wrong: {exc}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    whatsapp.set_handler(_handle_whatsapp_message)
    try:
        await whatsapp.start()
    except Exception:  # noqa: BLE001 — API still works without WhatsApp
        logger.exception("WhatsApp channel failed to start")
    yield


app = FastAPI(title="Expense Manager API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(whatsapp_routes.router)
app.include_router(categories.router)
app.include_router(dashboard.router)
app.include_router(google_auth.router)


@app.get("/api/health")
async def health():
    return {"ok": True}
