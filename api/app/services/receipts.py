"""Receipt intake pipeline shared by UI and WhatsApp channels.

Image -> NVIDIA OCR text + local image save -> a composed prompt the agent
can act on (it then calls record_transaction with structured fields).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from ..config import config
from . import google_client as gc
from . import vision

logger = logging.getLogger(__name__)

_PDF_MAX_PAGES = 10


def _pdf_to_page_images(data: bytes) -> list[bytes]:
    """Render up to _PDF_MAX_PAGES pages of a PDF to PNG bytes (2x zoom)."""
    import fitz

    images: list[bytes] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        total = doc.page_count
        for index in range(min(total, _PDF_MAX_PAGES)):
            pix = doc.load_page(index).get_pixmap(matrix=fitz.Matrix(2, 2))
            images.append(pix.tobytes("png"))
    if total > _PDF_MAX_PAGES:
        logger.info("PDF receipt truncated: %d of %d pages rendered",
                    _PDF_MAX_PAGES, total)
    return images


async def save_and_ocr(image_bytes: bytes, mime_type: str) -> dict:
    """Save one receipt file to disk and OCR it.

    Shared by build_receipt_prompt (one file) and build_batch_receipt_prompt
    (N files) — the per-file work (save image, OCR, PDF page rendering) is
    identical either way, only the prompt composition differs. Public (not
    module-private) so a caller that wants per-file progress — the chat route,
    for a multi-receipt message — can call it directly in its own loop instead
    of going through build_batch_receipt_prompt's single opaque await.
    """
    extension = (mime_type.split("/") + ["bin"])[1].split("+")[0]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"receipt-{timestamp}-{uuid.uuid4().hex[:6]}.{extension}"

    receipts_dir = config.data_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    image_path = receipts_dir / filename
    image_path.write_bytes(image_bytes)

    ocr_error = ""
    if mime_type == "application/pdf":
        # Render pages -> OCR each -> store first page as a preview image for the UI.
        try:
            pages = _pdf_to_page_images(image_bytes)
            if pages:
                preview_path = image_path.with_suffix(".preview.png")
                preview_path.write_bytes(pages[0])
            texts = []
            for number, page_png in enumerate(pages, start=1):
                texts.append(f"--- page {number} ---")
                texts.append(await vision.extract_text(page_png, "image/png"))
            ocr_text = "\n".join(texts)
        except Exception as exc:  # noqa: BLE001
            ocr_text = ""
            ocr_error = str(exc)
    else:
        try:
            ocr_text = await vision.extract_text(image_bytes, mime_type)
        except Exception as exc:  # noqa: BLE001
            ocr_text = ""
            ocr_error = str(exc)

    return {"filename": filename, "image_path": image_path,
            "ocr_text": ocr_text, "ocr_error": ocr_error}


async def build_receipt_prompt(
    user_text: str, image_bytes: bytes, mime_type: str
) -> str:
    result = await save_and_ocr(image_bytes, mime_type)

    parts = [
        "The user submitted a receipt image.",
        "",
        "OCR-extracted text from the receipt:",
        "---",
        result["ocr_text"] or f"(OCR failed: {result['ocr_error']})",
        "---",
    ]
    if user_text.strip():
        parts.append(f'User note: "{user_text.strip()}"')
    parts.append(
        "Extract the transaction details (date, merchant, total incl. taxes), "
        f"choose a category, and call record_transaction with image_path=\"{result['image_path']}\"."
    )
    return "\n".join(parts)


def compose_batch_prompt(user_text: str, results: list[dict]) -> str:
    """Build the batch prompt text from already-saved+OCR'd per-file results
    (see save_and_ocr). Pure/sync — split out of build_batch_receipt_prompt so
    a caller doing its own per-file loop (for progress reporting) can reuse
    the composition step without re-running the OCR loop."""
    parts = [f"The user submitted {len(results)} receipts in one message.", ""]
    for number, result in enumerate(results, start=1):
        parts.append(f"--- receipt {number} ({result['filename']}) ---")
        parts.append("OCR-extracted text:")
        parts.append(result["ocr_text"] or f"(OCR failed: {result['ocr_error']})")
        parts.append(f'image_path: "{result["image_path"]}"')
        parts.append("")
    if user_text.strip():
        parts.append(f'User note: "{user_text.strip()}"')
        parts.append("")
    parts.append(
        "For EVERY receipt above, extract the transaction details (date, "
        "merchant, total incl. taxes) and choose a category. Present ONE "
        "numbered summary covering ALL receipts (date / merchant / total / "
        "category per item), flagging any item that looks like a likely "
        "duplicate. Wait for a single confirmation covering every item — do "
        "not ask one-by-one. Once the user confirms, call record_transaction "
        "once per item (the existing tool, not a new one), passing that "
        "item's own image_path; for any item you flagged as a likely "
        "duplicate that the user did not ask to drop, pass "
        "confirm_duplicate=true on that call. Skip (do not record) any item "
        "the user explicitly asks to drop. A failure recording one item must "
        "not stop the rest — after all calls, report combined results: the "
        "recorded transaction ids and any failures."
    )
    return "\n".join(parts)


async def build_batch_receipt_prompt(
    user_text: str, files: list[tuple[bytes, str]]
) -> str:
    """Compose a prompt covering N receipts submitted in one chat message.

    Each file is saved + OCR'd independently (a failure on one does not drop
    it from the batch — same fault-tolerant posture as the single-file path).
    Convenience wrapper around save_and_ocr + compose_batch_prompt for callers
    that don't need per-file progress; the chat route calls those directly
    instead, to report progress between files.
    """
    results = [await save_and_ocr(data, mime) for data, mime in files]
    return compose_batch_prompt(user_text, results)


_FILE_ID_RES = (re.compile(r"/file/d/([A-Za-z0-9_-]+)"),
                re.compile(r"[?&]id=([A-Za-z0-9_-]+)"))

_MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
             "application/pdf": ".pdf"}


def extract_file_id(url: str) -> str | None:
    for pattern in _FILE_ID_RES:
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return None


def _drive_download(file_id: str) -> tuple[bytes, str]:
    """Bytes + mimeType of a Drive file. Needs full drive scope."""
    drive = gc.drive_service()
    meta = drive.files().get(fileId=file_id, fields="mimeType").execute()
    data = drive.files().get_media(fileId=file_id).execute()
    return data, meta.get("mimeType", "application/octet-stream")


def download_linked_receipts() -> int:
    """Backfill local copies for txns that have a Drive receipt_link but no
    image_path. Returns how many were downloaded. Failures are audited and
    skipped — the external link still works."""
    from ..db import get_db
    from . import audit
    if not gc.is_connected():
        return 0
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, receipt_link FROM transactions "
            "WHERE receipt_link IS NOT NULL AND image_path IS NULL").fetchall()
    done = 0
    for row in rows:
        file_id = extract_file_id(row["receipt_link"])
        if not file_id:
            continue
        try:
            data, mime = _drive_download(file_id)
        except Exception as exc:  # noqa: BLE001 — keep the link, log, move on
            with get_db() as conn:
                audit.record(conn, "receipt_download_failed", channel="import",
                             ref=str(row["id"]), detail=str(exc))
            continue
        receipts_dir = config.data_dir / "receipts"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        path = receipts_dir / f"drive-{file_id}{_MIME_EXT.get(mime, '.bin')}"
        path.write_bytes(data)
        with get_db() as conn:
            conn.execute("UPDATE transactions SET image_path=? WHERE id=?",
                         (str(path), row["id"]))
        done += 1
    if done:
        with get_db() as conn:
            audit.record(conn, "receipts_downloaded", channel="import",
                         detail=f"{done} receipts copied from Drive")
    return done
