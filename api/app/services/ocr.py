"""Receipt OCR via NVIDIA build.nvidia.com (NIM) models.

Default model is PaddleOCR (`baidu/paddleocr`), which takes a base64 image and
returns detected text regions. The raw text is handed to the agent, which
structures it into date/amount/GST/QST/etc.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from ..config import config

# NIM endpoints for OCR-style CV models
_OCR_URLS = {
    "baidu/paddleocr": "https://ai.api.nvidia.com/v1/cv/baidu/paddleocr",
    "nvidia/nemoretriever-ocr-v1": "https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-ocr-v1",
}

_ASSETS_URL = "https://api.nvcf.nvidia.com/v2/nvcf/assets"
_MAX_INLINE_BYTES = 180_000  # NIM inline payload limit ~200kb total


class OcrError(RuntimeError):
    pass


async def extract_text(image_bytes: bytes, mime_type: str) -> str:
    """Run OCR on an image, returning the concatenated detected text."""
    if not config.nvidia_api_key:
        raise OcrError("NVIDIA_API_KEY is not configured")

    model = config.nvidia_ocr_model
    url = _OCR_URLS.get(model)
    if url is None:
        raise OcrError(f"Unsupported NVIDIA OCR model: {model}")

    headers = {
        "Authorization": f"Bearer {config.nvidia_api_key}",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        if len(image_bytes) < _MAX_INLINE_BYTES:
            image_field = (
                f"data:{mime_type};base64,"
                + base64.b64encode(image_bytes).decode()
            )
        else:
            asset_id = await _upload_asset(client, image_bytes, mime_type)
            image_field = f"data:{mime_type};asset_id,{asset_id}"
            headers["NVCF-INPUT-ASSET-REFERENCES"] = asset_id

        payload = {"input": [{"type": "image_url", "url": image_field}]}
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            raise OcrError(
                f"NVIDIA OCR failed ({response.status_code}): {response.text[:500]}"
            )
        return _flatten_text(response.json())


async def _upload_asset(
    client: httpx.AsyncClient, data: bytes, mime_type: str
) -> str:
    """Upload large images via the NVCF asset API."""
    headers = {
        "Authorization": f"Bearer {config.nvidia_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    created = await client.post(
        _ASSETS_URL,
        headers=headers,
        json={"contentType": mime_type, "description": "receipt image"},
    )
    if created.status_code != 200:
        raise OcrError(f"Asset creation failed: {created.text[:300]}")
    body = created.json()
    upload_url = body["uploadUrl"]
    asset_id = body["assetId"]

    uploaded = await client.put(
        upload_url,
        content=data,
        headers={
            "Content-Type": mime_type,
            "x-amz-meta-nvcf-asset-description": "receipt image",
        },
    )
    if uploaded.status_code not in (200, 201):
        raise OcrError(f"Asset upload failed: {uploaded.status_code}")
    return asset_id


def _flatten_text(result: Any) -> str:
    """Pull all text strings out of a NIM OCR response, tolerant of shape
    differences between models."""
    texts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            # paddleocr: {"text_detections": [{"text_prediction": {"text": ...}}]}
            text_value = node.get("text")
            if isinstance(text_value, str) and text_value.strip():
                texts.append(text_value.strip())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(result)
    if not texts:
        raise OcrError(f"No text found in OCR response: {str(result)[:300]}")
    return "\n".join(texts)
