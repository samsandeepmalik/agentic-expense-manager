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
from ..errors import AppError
from ..services import chat_store
from ..services import google_client as gc
from ..services import imports as imports_svc
from ..services import mime_check
from ..services import profiles as prof_svc
from ..services.receipts import (
    build_receipt_prompt,
    compose_batch_prompt,
    save_and_ocr,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_STATEMENT_EXT = (".csv", ".xlsx", ".xls", ".pdf")
# Extensions that unambiguously mean "statement" — used only to reject a
# multi-file request that mixes a statement in with receipts. .pdf is
# deliberately excluded here: in a multi-file request a PDF is always
# treated as a receipt (see send_message), so it must not trip this check.
_HARD_STATEMENT_EXT = (".csv", ".xlsx", ".xls")
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
_MAX_FILES = 10


async def _try_upload_import_source(import_id: int, filename: str,
                                    data: bytes, content_type: str | None) -> None:
    """Upload source file to Drive (private) and store link on import.

    The file is NOT made world-readable — statement source files contain
    sensitive financial data (account numbers, full transaction history) and
    do not need to be accessible via an anonymous link. Failures are logged at
    WARNING level and swallowed so the import flow is not interrupted.
    """
    def _upload() -> None:
        import_record = imports_svc.get_import(import_id)
        with get_db() as conn:
            profile = prof_svc.get_profile(conn, import_record["profile_id"])
        mime = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        result = gc.upload_receipt_image(
            filename, data, mime, profile=profile, date=date.today().isoformat(),
            public=False)
        link = result["link"] if isinstance(result, dict) else result
        imports_svc.set_source_link(import_id, link)

    try:
        await asyncio.to_thread(_upload)
    except Exception as exc:
        logger.warning("Drive upload failed for import %s: %s", import_id, type(exc).__name__)


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
                       files: list[UploadFile] = File([])):
    session = sessions.get(session_id, channel="ui")

    if len(files) > _MAX_FILES:
        raise AppError("too_many_files",
                       f"Attach at most {_MAX_FILES} files per message "
                       f"({len(files)} received)", 400)

    uploads = []
    for f in files:
        data = await f.read()
        filename = f.filename
        content_type = f.content_type
        if len(data) > _MAX_UPLOAD_BYTES:
            raise AppError("file_too_large",
                           f"Upload exceeds the 20 MB limit ({len(data) // 1024 // 1024} MB received)",
                           413)
        uploads.append({
            "data": data, "filename": filename, "content_type": content_type,
            "is_image": bool(content_type and content_type.startswith("image/")),
        })

    multi = len(uploads) > 1
    if multi and any((u["filename"] or "").lower().endswith(_HARD_STATEMENT_EXT)
                     for u in uploads):
        raise AppError("mixed_statement",
                       "Attach a statement on its own, separate from receipts.", 400)

    for u in uploads:
        if not u["filename"]:
            continue
        if multi:
            # Multi-file: every attachment (including a PDF that would
            # otherwise be ambiguous) goes through the receipt pipeline.
            mime_check.check_receipt(u["filename"], u["content_type"])
        elif u["is_image"]:
            mime_check.check_receipt(u["filename"], u["content_type"])
        elif _is_statement(u["filename"]):
            mime_check.check_statement(u["filename"], u["content_type"])
        else:
            # Non-image, non-statement: treated as a receipt (e.g. single-page
            # PDF that classify_and_start routes to build_receipt_prompt).
            # Still gate on allowed receipt extensions/MIME to block .exe etc.
            mime_check.check_receipt(u["filename"], u["content_type"])

    async def stream():
        try:
            prompt = message
            if multi:
                total = len(uploads)
                results = []
                for index, u in enumerate(uploads, start=1):
                    yield _sse({"type": "status",
                               "text": f"Reading receipt {index} of {total}…"})
                    results.append(await save_and_ocr(u["data"], u["content_type"]))
                prompt = compose_batch_prompt(message, results)
            elif uploads:
                data = uploads[0]["data"]
                filename = uploads[0]["filename"]
                content_type = uploads[0]["content_type"]
                is_image = uploads[0]["is_image"]
                if data and not is_image and _is_statement(filename):
                    yield _sse({"type": "status", "text": "Reading statement…"})
                    result = await imports_svc.classify_and_start(filename, data)
                    if result["kind"] == "statement":
                        yield _sse({"type": "status", "text": "Uploading to Drive…"})
                        await _try_upload_import_source(
                            result["import_id"], filename, data, content_type)
                        # Do NOT embed the raw filename in the prompt — it is a
                        # client-supplied multipart field and could contain prompt
                        # injection payloads. The import_id is sufficient for the
                        # agent to call get_import_summary and proceed.
                        prompt = (f"{message}\n\n[A statement file was uploaded and "
                                  f"parsed as import #{result['import_id']}. "
                                  f"Review it with get_import_summary and follow "
                                  f"the import flow.]")
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
                        "error": exc.message if isinstance(exc, AppError) else "Internal server error"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
