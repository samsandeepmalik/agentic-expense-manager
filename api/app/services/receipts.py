"""Receipt intake pipeline shared by UI and WhatsApp channels.

Image -> NVIDIA OCR text + local image save -> a composed prompt the agent
can act on (it then calls record_transaction with structured fields).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from ..config import config
from . import vision


async def build_receipt_prompt(
    user_text: str, image_bytes: bytes, mime_type: str
) -> str:
    extension = (mime_type.split("/") + ["bin"])[1].split("+")[0]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"receipt-{timestamp}-{uuid.uuid4().hex[:6]}.{extension}"

    ocr_task = asyncio.create_task(vision.extract_text(image_bytes, mime_type))

    receipts_dir = config.data_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    image_path = receipts_dir / filename
    image_path.write_bytes(image_bytes)

    # OCR failure degrades gracefully: the image is still stored and the agent
    # can ask the user for the missing details.
    ocr_error = ""
    try:
        ocr_text = await ocr_task
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
