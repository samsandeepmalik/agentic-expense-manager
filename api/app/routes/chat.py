"""Chat: session management + SSE message streaming."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from ..agent.runtime import sessions
from ..db import get_db
from ..services import chat_store
from ..services import imports as imports_svc
from ..services.receipts import build_receipt_prompt

router = APIRouter()

_STATEMENT_EXT = (".csv", ".xlsx", ".xls")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _is_statement(filename: str | None) -> bool:
    name = (filename or "").lower()
    return name.endswith(_STATEMENT_EXT) or name.endswith(".pdf")


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
