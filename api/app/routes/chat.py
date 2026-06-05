"""Chat endpoint: SSE stream of agent events (text deltas, tools, gen-UI)."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from ..agent.runtime import sessions
from ..services.google_client import GoogleNotConnectedError
from ..services.receipts import build_receipt_prompt

router = APIRouter()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/api/chat")
async def chat(
    message: str = Form(""),
    session_id: str = Form(""),
    image: UploadFile | None = File(None),
):
    session_id = session_id or f"ui:{uuid.uuid4().hex[:12]}"
    session = sessions.get(session_id, channel="ui")

    image_bytes = await image.read() if image is not None else None
    image_mime = (image.content_type or "image/jpeg") if image is not None else None

    async def stream():
        yield _sse({"type": "session", "session_id": session_id})
        try:
            prompt = message
            if image_bytes:
                yield _sse({"type": "status", "text": "Reading receipt…"})
                prompt = await build_receipt_prompt(message, image_bytes, image_mime)
            if not prompt.strip():
                yield _sse({"type": "done", "text": "Send a message or a receipt image.", "error": None})
                return
            async for event in session.run(prompt):
                yield _sse(event)
        except GoogleNotConnectedError as exc:
            yield _sse({"type": "done", "text": str(exc), "error": "google_not_connected"})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "done", "text": f"Error: {exc}", "error": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/chat/reset")
async def reset_chat(session_id: str = Form(...)):
    sessions.reset(session_id)
    return {"ok": True}
