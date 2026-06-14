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


async def build_receipt_prompt(
    user_text: str, image_bytes: bytes, mime_type: str
) -> str:
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

    parts = [
        "The user submitted a receipt image.",
        f"Saved receipt image path: {image_path}",
        "",
        "OCR-extracted text from the receipt:",
        "---",
        ocr_text or f"(OCR failed: {ocr_error})",
        "---",
    ]
    if user_text.strip():
        parts.append(f'User note: "{user_text.strip()}"')
    parts.append(
        "Extract the transaction details (date, merchant, total incl. taxes), "
        "choose a category, and call record_transaction with image_path set."
    )
    return "\n".join(parts)


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
