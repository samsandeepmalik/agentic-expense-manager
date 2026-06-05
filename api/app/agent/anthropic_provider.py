"""Anthropic Messages API provider for the pi-agent runtime.

Supports two auth modes:
- Claude Code Max subscription OAuth token (``claude setup-token``) via
  ``CLAUDE_CODE_OAUTH_TOKEN`` — sent as a Bearer token with the OAuth beta
  header, mirroring how the Pi coding agent's own ``/login`` works.
- Classic ``ANTHROPIC_API_KEY`` (x-api-key) as a fallback.

Implements the pi-agent Provider protocol: ``stream(request, abort_event)``
returns an AssistantStream fed by Anthropic's SSE events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from pi_agent.agent_core.event_stream import AssistantMessageEventStream
from pi_agent.agent_core.types import (
    AssistantContentBlock,
    AssistantMessage,
    AssistantStream,
    ImageContent,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
)
from pi_agent.pi_ai.types import PiAIRequest

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_MAX_TOKENS = 8192

# Subscription OAuth tokens are only honoured for Claude Code clients; the
# first system block must identify as Claude Code (same as Pi's TS provider).
_CLAUDE_CODE_SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."


class AnthropicMessagesProvider:
    async def stream(
        self,
        request: PiAIRequest,
        abort_event: asyncio.Event | None = None,
    ) -> AssistantStream:
        stream = AssistantMessageEventStream()
        asyncio.create_task(self._emit(stream, request, abort_event))
        return stream

    # ------------------------------------------------------------------

    def _auth_headers(self, request: PiAIRequest) -> dict[str, str]:
        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        oauth_token, api_key = _resolve_auth(request)

        if oauth_token:
            headers["authorization"] = f"Bearer {oauth_token}"
            headers["anthropic-beta"] = "oauth-2025-04-20"
        elif api_key:
            headers["x-api-key"] = api_key
        else:
            raise RuntimeError(
                "No Claude credentials. Run `claude setup-token` and set "
                "CLAUDE_CODE_OAUTH_TOKEN (Claude Max subscription), or set "
                "ANTHROPIC_API_KEY."
            )
        return headers

    def _build_payload(self, request: PiAIRequest) -> dict[str, Any]:
        oauth_token, _api_key = _resolve_auth(request)
        uses_oauth = bool(oauth_token)

        system_blocks: list[dict[str, Any]] = []
        if uses_oauth:
            system_blocks.append({"type": "text", "text": _CLAUDE_CODE_SYSTEM})
        if request.context.system_prompt:
            system_blocks.append(
                {"type": "text", "text": request.context.system_prompt}
            )

        payload: dict[str, Any] = {
            "model": request.model.id,
            "max_tokens": _MAX_TOKENS,
            "stream": True,
            "messages": _to_anthropic_messages(request.context.messages),
        }
        if system_blocks:
            payload["system"] = system_blocks

        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": dict(tool.parameters or {"type": "object", "properties": {}}),
            }
            for tool in (request.context.tools or [])
        ]
        if tools:
            payload["tools"] = tools
        return payload

    # ------------------------------------------------------------------

    async def _emit(
        self,
        stream: AssistantMessageEventStream,
        request: PiAIRequest,
        abort_event: asyncio.Event | None,
    ) -> None:
        await asyncio.sleep(0)
        model = request.model
        partial = AssistantMessage(
            content=[],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage(cost=UsageCost()),
            stop_reason="stop",
        )
        tool_json_buffers: dict[int, str] = {}

        try:
            headers = self._auth_headers(request)
            payload = self._build_payload(request)
            base_url = (model.base_url or _DEFAULT_BASE_URL).rstrip("/")
            url = f"{base_url}/v1/messages"

            stream.push({"type": "start", "partial": partial})

            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode(errors="replace")
                        raise RuntimeError(
                            f"Anthropic API {response.status_code}: {body[:500]}"
                        )

                    async for line in response.aiter_lines():
                        if abort_event is not None and abort_event.is_set():
                            raise _Aborted()
                        if not line.startswith("data: "):
                            continue
                        event = json.loads(line[6:])
                        self._apply_event(stream, partial, event, tool_json_buffers)
                        if event.get("type") == "message_stop":
                            break

            stream.push(
                {
                    "type": "done",
                    "reason": "toolUse" if partial.stop_reason == "toolUse" else (
                        "length" if partial.stop_reason == "length" else "stop"
                    ),
                    "message": partial,
                }
            )
        except _Aborted:
            stream.push(
                {
                    "type": "error",
                    "reason": "aborted",
                    "error": _error_message(model, "aborted", "Request aborted"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Anthropic stream failed")
            stream.push(
                {
                    "type": "error",
                    "reason": "error",
                    "error": _error_message(model, "error", str(exc)),
                }
            )

    def _apply_event(
        self,
        stream: AssistantMessageEventStream,
        partial: AssistantMessage,
        event: dict[str, Any],
        tool_json_buffers: dict[int, str],
    ) -> None:
        event_type = event.get("type")

        if event_type == "message_start":
            usage = event.get("message", {}).get("usage", {})
            partial.usage.input = usage.get("input_tokens", 0)
            partial.usage.cache_read = usage.get("cache_read_input_tokens", 0) or 0
            return

        if event_type == "content_block_start":
            index = len(partial.content)
            block = event.get("content_block", {})
            block_type = block.get("type")
            if block_type == "text":
                partial.content.append(TextContent(text=""))
                stream.push({"type": "text_start", "content_index": index, "partial": partial})
            elif block_type == "thinking":
                partial.content.append(ThinkingContent(thinking=""))
                stream.push({"type": "thinking_start", "content_index": index, "partial": partial})
            elif block_type == "tool_use":
                partial.content.append(
                    ToolCall(id=block.get("id", ""), name=block.get("name", ""), arguments={})
                )
                tool_json_buffers[index] = ""
                stream.push({"type": "toolcall_start", "content_index": index, "partial": partial})
            return

        if event_type == "content_block_delta":
            if not partial.content:
                return
            index = min(event.get("index", len(partial.content) - 1), len(partial.content) - 1)
            block = partial.content[index]
            delta = event.get("delta", {})
            delta_type = delta.get("type")

            if delta_type == "text_delta" and isinstance(block, TextContent):
                text = delta.get("text", "")
                block.text += text
                stream.push(
                    {"type": "text_delta", "content_index": index, "delta": text, "partial": partial}
                )
            elif delta_type == "thinking_delta" and isinstance(block, ThinkingContent):
                thinking = delta.get("thinking", "")
                block.thinking += thinking
                stream.push(
                    {"type": "thinking_delta", "content_index": index, "delta": thinking, "partial": partial}
                )
            elif delta_type == "input_json_delta" and isinstance(block, ToolCall):
                fragment = delta.get("partial_json", "")
                tool_json_buffers[index] = tool_json_buffers.get(index, "") + fragment
                stream.push(
                    {"type": "toolcall_delta", "content_index": index, "delta": fragment, "partial": partial}
                )
            return

        if event_type == "content_block_stop":
            if not partial.content:
                return
            index = min(event.get("index", len(partial.content) - 1), len(partial.content) - 1)
            block = partial.content[index]
            if isinstance(block, TextContent):
                stream.push(
                    {"type": "text_end", "content_index": index, "content": block.text, "partial": partial}
                )
            elif isinstance(block, ThinkingContent):
                stream.push(
                    {"type": "thinking_end", "content_index": index, "content": block.thinking, "partial": partial}
                )
            elif isinstance(block, ToolCall):
                raw = tool_json_buffers.get(index, "")
                try:
                    block.arguments = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    block.arguments = {}
                stream.push(
                    {"type": "toolcall_end", "content_index": index, "tool_call": block, "partial": partial}
                )
            return

        if event_type == "message_delta":
            delta = event.get("delta", {})
            stop_reason = delta.get("stop_reason")
            if stop_reason:
                partial.stop_reason = _map_stop_reason(stop_reason)
            usage = event.get("usage", {})
            if usage.get("output_tokens") is not None:
                partial.usage.output = usage["output_tokens"]
                partial.usage.total_tokens = (
                    partial.usage.input + partial.usage.output + partial.usage.cache_read
                )
            return

        if event_type == "error":
            raise RuntimeError(str(event.get("error", {}).get("message", "unknown error")))


# ---------------------------------------------------------------------------
# Message conversion: pi-agent domain model -> Anthropic Messages API
# ---------------------------------------------------------------------------


def _to_anthropic_messages(messages: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, UserMessage):
            result.append({"role": "user", "content": _user_content(message)})
        elif isinstance(message, AssistantMessage):
            content = _assistant_content(message)
            if content:
                result.append({"role": "assistant", "content": content})
        elif isinstance(message, ToolResultMessage):
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": _tool_result_content(message),
                            "is_error": message.is_error,
                        }
                    ],
                }
            )
    return _merge_consecutive(result)


def _user_content(message: UserMessage) -> list[dict[str, Any]] | str:
    if isinstance(message.content, str):
        return message.content
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextContent):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.mime_type,
                        "data": block.data,
                    },
                }
            )
    return blocks


def _assistant_content(message: AssistantMessage) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextContent) and block.text:
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolCall):
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.arguments,
                }
            )
    return blocks


def _tool_result_content(message: ToolResultMessage) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextContent):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.mime_type,
                        "data": block.data,
                    },
                }
            )
    return blocks or [{"type": "text", "text": ""}]


def _merge_consecutive(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic requires alternating roles; merge consecutive same-role
    messages (e.g. multiple tool_result blocks)."""
    merged: list[dict[str, Any]] = []
    for message in messages:
        if merged and merged[-1]["role"] == message["role"]:
            previous = merged[-1]
            if isinstance(previous["content"], str):
                previous["content"] = [{"type": "text", "text": previous["content"]}]
            content = message["content"]
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            previous["content"] = [*previous["content"], *content]
        else:
            merged.append(message)
    return merged


def _map_stop_reason(reason: str) -> StopReason:
    if reason == "max_tokens":
        return "length"
    if reason == "tool_use":
        return "toolUse"
    return "stop"


def _error_message(model: Any, stop_reason: StopReason, text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="")],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(cost=UsageCost()),
        stop_reason=stop_reason,
        error_message=text,
    )


def _resolve_auth(request: PiAIRequest) -> tuple[str | None, str | None]:
    """Return (oauth_token, api_key). `claude setup-token` tokens start with
    sk-ant-oat; one pasted into ANTHROPIC_API_KEY is treated as OAuth rather
    than sent as x-api-key."""
    oauth_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or os.getenv(
        "ANTHROPIC_OAUTH_TOKEN"
    )
    api_key = request.api_key or os.getenv("ANTHROPIC_API_KEY")
    if not oauth_token and api_key and api_key.startswith("sk-ant-oat"):
        oauth_token = api_key
        api_key = None
    return oauth_token or None, api_key or None


class _Aborted(RuntimeError):
    pass
