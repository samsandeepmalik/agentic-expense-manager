"""Chat: session management + SSE message streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from datetime import date

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from ..agent.runtime import sessions
from ..db import get_db
from ..services import chat_store
from ..services import google_client as gc
from ..services import imports as imports_svc
from ..services import profiles as prof_svc
from ..services.receipts import build_receipt_prompt

logger = logging.getLogger(__name__)

router = APIRouter()

_STATEMENT_EXT = (".csv", ".xlsx", ".xls", ".pdf")


async def _try_upload_import_source(import_id: int, filename: str,
                                    data: bytes, content_type: str | None) -> None:
    """Upload source file to Drive and store link on import. Silent on failure."""
    def _upload() -> None:
        import_record = imports_svc.get_import(import_id)
        with get_db() as conn:
            profile = prof_svc.get_profile(conn, import_record["profile_id"])
        mime = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        result = gc.upload_receipt_image(
            filename, data, mime, profile=profile, date=date.today().isoformat())
        link = result["link"] if isinstance(result, dict) else result
        imports_svc.set_source_link(import_id, link)

    try:
        await asyncio.to_thread(_upload)
    except Exception:
        logger.debug("Drive upload skipped for import %s", import_id, exc_info=True)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _is_statement(filename: str | None) -> bool:
    return bool((filename or "").lower().endswith(_STATEMENT_EXT))


@router.get("/api/chat/sessions")
async def list_sessions(channel: str = "ui"):
    with get_db() as conn:
        return chat_store.list_sessions(conn, channel=channel)


@router.post("/api/chat/sessions")
async def create_session():
    with get_db() as conn:
        return chat_store.create_session(conn)


@router.get("/api/chat/sessions/{session_id}")
async def session_history(session_id: str):
    with get_db() as conn:
        return {"session": chat_store.get_session(conn, session_id),
                "messages": chat_store.list_messages(conn, session_id)}


@router.delete("/api/chat/sessions/{session_id}")
async def delete_session(session_id: str):
    with get_db() as conn:
        chat_store.delete_session(conn, session_id)
    sessions.reset(session_id)
    return {"ok": True}


@router.post("/api/chat/sessions/{session_id}/messages")
async def send_message(session_id: str, message: str = Form(""),
                       file: UploadFile | None = File(None)):
    session = sessions.get(session_id, channel="ui")
    data = await file.read() if file is not None else None
    filename = file.filename if file is not None else None
    content_type = file.content_type if file is not None else None
    is_image = bool(content_type and content_type.startswith("image/"))

    async def stream():
        try:
            prompt = message
            if data and not is_image and _is_statement(filename):
                yield _sse({"type": "status", "text": "Reading statement…"})
                result = await imports_svc.classify_and_start(filename, data)
                if result["kind"] == "statement":
                    await _try_upload_import_source(
                        result["import_id"], filename, data, content_type)
                    prompt = (f"{message}\n\n[The user uploaded the statement "
                              f"'{filename}'. It was parsed as import "
                              f"#{result['import_id']}. Review it with "
                              f"get_import_summary and follow the import flow.]")
                elif result["kind"] == "failed":
                    yield _sse({"type": "done",
                                "text": "I couldn't read that statement. "
                                        "Try a CSV export.",
                                "error": result.get("error")})
                    return
                else:   # receipt (e.g. single-row PDF)
                    prompt = await build_receipt_prompt(message, data, content_type)
            elif data:   # image -> receipt
                yield _sse({"type": "status", "text": "Reading receipt…"})
                prompt = await build_receipt_prompt(message, data, content_type)
            if not prompt.strip():
                yield _sse({"type": "done", "text": "Send a message or file.", "error": None})
                return
            async for event in session.run(prompt):
                yield _sse(event)
        except Exception as exc:  # noqa: BLE001 — degrade, never 500 mid-stream
            yield _sse({"type": "done",
                        "text": "Sorry, something went wrong on my side. Try again.",
                        "error": str(exc)})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
