"""Receipt text extraction with a selectable provider.

Providers:
- nvidia  — NVIDIA NIM PaddleOCR (services/ocr.py, untouched)
- claude  — Anthropic Messages API vision (Claude Max OAuth or API key)
- openai  — OpenAI chat completions vision

The active provider lives in the settings table ("ocr_provider").
"""

from __future__ import annotations

import base64

import httpx

from ..config import config
from ..db import get_db, get_setting
from . import ocr

PROVIDERS = ("nvidia", "claude", "openai")
DEFAULT_PROVIDER = "nvidia"

_PROMPT = (
    "Transcribe ALL text visible in this receipt image, line by line, "
    "top to bottom. Output only the raw text — no commentary."
)

# Subscription OAuth tokens are only honoured for Claude Code clients; the
# first system block must identify as Claude Code (matches anthropic_provider).
_CLAUDE_CODE_SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."


class VisionError(RuntimeError):
    pass


def current_provider() -> str:
    with get_db() as conn:
        provider = get_setting(conn, "ocr_provider")
    return provider if provider in PROVIDERS else DEFAULT_PROVIDER


def available_providers() -> dict[str, bool]:
    return {
        "nvidia": bool(config.nvidia_api_key),
        "claude": bool(config.claude_oauth_token or config.anthropic_api_key),
        "openai": bool(config.openai_api_key),
    }


async def extract_text(image_bytes: bytes, mime_type: str) -> str:
    provider = current_provider()
    if provider == "claude":
        return await _claude_extract(image_bytes, mime_type)
    if provider == "openai":
        return await _openai_extract(image_bytes, mime_type)
    return await _nvidia_extract(image_bytes, mime_type)


async def _nvidia_extract(image_bytes: bytes, mime_type: str) -> str:
    return await ocr.extract_text(image_bytes, mime_type)


async def _claude_extract(image_bytes: bytes, mime_type: str) -> str:
    headers = {
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    system_blocks = []
    if config.claude_oauth_token:
        headers["authorization"] = f"Bearer {config.claude_oauth_token}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
        system_blocks.append({"type": "text", "text": _CLAUDE_CODE_SYSTEM})
    elif config.anthropic_api_key:
        headers["x-api-key"] = config.anthropic_api_key
    else:
        raise VisionError("No Claude credentials for OCR "
                          "(CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY)")

    payload = {
        "model": config.claude_model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64", "media_type": mime_type,
                "data": base64.b64encode(image_bytes).decode()}},
            {"type": "text", "text": _PROMPT},
        ]}],
    }
    if system_blocks:
        payload["system"] = system_blocks

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{config.anthropic_base_url.rstrip('/')}/v1/messages",
            headers=headers, json=payload)
    if response.status_code != 200:
        raise VisionError(
            f"Claude OCR failed ({response.status_code}): {response.text[:300]}")
    blocks = response.json().get("content", [])
    text = "\n".join(b.get("text", "") for b in blocks
                     if b.get("type") == "text").strip()
    if not text:
        raise VisionError("Claude OCR returned no text")
    return text


async def _openai_extract(image_bytes: bytes, mime_type: str) -> str:
    if not config.openai_api_key:
        raise VisionError("OPENAI_API_KEY is not configured")
    data_uri = f"data:{mime_type};base64," + base64.b64encode(image_bytes).decode()
    payload = {
        "model": config.openai_vision_model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _PROMPT},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.openai_api_key}"},
            json=payload)
    if response.status_code != 200:
        raise VisionError(
            f"OpenAI OCR failed ({response.status_code}): {response.text[:300]}")
    text = (response.json().get("choices", [{}])[0]
            .get("message", {}).get("content") or "").strip()
    if not text:
        raise VisionError("OpenAI OCR returned no text")
    return text
