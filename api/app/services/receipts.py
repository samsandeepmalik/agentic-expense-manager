"""Receipt intake pipeline shared by UI and WhatsApp channels.

Image -> NVIDIA OCR text + Google Drive upload -> a composed prompt the agent
can act on (it then calls record_transaction with structured fields).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from . import google_client as gc
from . import ocr


async def build_receipt_prompt(
    user_text: str, image_bytes: bytes, mime_type: str
) -> str:
    extension = (mime_type.split("/") + ["bin"])[1].split("+")[0]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"receipt-{timestamp}-{uuid.uuid4().hex[:6]}.{extension}"

    ocr_task = asyncio.create_task(ocr.extract_text(image_bytes, mime_type))
    upload_task = asyncio.create_task(
        asyncio.to_thread(gc.upload_receipt_image, filename, image_bytes, mime_type)
    )

    # OCR failure degrades gracefully: the image is still stored and the agent
    # can ask the user for the missing details.
    ocr_error = ""
    try:
        ocr_text = await ocr_task
    except Exception as exc:  # noqa: BLE001
        ocr_text = ""
        ocr_error = str(exc)

    image_link = await upload_task

    parts = [
        "The user submitted a receipt image.",
        f"Google Drive image link: {image_link}",
        "",
        "OCR-extracted text from the receipt:",
        "---",
        ocr_text or f"(OCR failed: {ocr_error})",
        "---",
    ]
    if user_text.strip():
        parts.append(f'User note: "{user_text.strip()}"')
    parts.append(
        "Extract the transaction details (date, merchant, amount, GST, QST, "
        "total), choose a category, and record it with the image link."
    )
    return "\n".join(parts)
