"""Pi agent runtime: Claude provider wiring, per-session agents, streaming.

Uses the Pi agent Python SDK (pi-agent). Claude is reached through Anthropic's
native Messages API via a custom provider that authenticates with a Claude
Code Max subscription OAuth token (CLAUDE_CODE_OAUTH_TOKEN, from
`claude setup-token`) or an ANTHROPIC_API_KEY fallback.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pi_agent.agent_core import (
    Agent,
    AgentState,
    AssistantMessage,
    Model,
    TextContent,
)
from pi_agent.pi_ai import create_agent_stream_fn, create_default_registry

from ..config import config
from .anthropic_provider import AnthropicMessagesProvider
from .prompts import system_prompt
from .tools import build_tools

_registry = create_default_registry()
_registry.register("anthropic", AnthropicMessagesProvider())
_stream_fn = create_agent_stream_fn(_registry)


def _claude_model() -> Model:
    return Model(
        id=config.claude_model,
        provider="anthropic",
        api="anthropic",
        base_url=config.anthropic_base_url,
    )


class Session:
    """One conversation: a Pi agent plus a queue-based event bridge."""

    def __init__(self, session_id: str, channel: str) -> None:
        self.id = session_id
        self.channel = channel  # "ui" | "whatsapp"
        self.lock = asyncio.Lock()
        self._ui_specs: list[dict[str, Any]] = []

        self.agent = Agent(
            initial_state=AgentState(
                system_prompt=system_prompt(channel),
                model=_claude_model(),
            ),
            stream_fn=_stream_fn,
            session_id=session_id,
        )
        self.agent.set_tools(
            build_tools(channel, self._ui_specs.append, source=channel)
        )

    async def run(self, text: str) -> AsyncIterator[dict[str, Any]]:
        """Send a user message; yield normalized streaming events:

        {type: "delta", text}            — assistant text chunk
        {type: "tool", name, status}     — tool started / finished
        {type: "ui", spec}               — generative UI spec from render_ui
        {type: "done", text, error}      — final assistant text
        """
        async with self.lock:
            self._ui_specs.clear()
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            def listener(event: dict[str, Any]) -> None:
                queue.put_nowait(event)
                if event.get("type") == "agent_end":
                    queue.put_nowait(None)

            unsubscribe = self.agent.subscribe(listener)
            prompt_task = asyncio.create_task(self.agent.prompt(text))
            final_text_parts: list[str] = []
            emitted_ui = 0

            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    event_type = event.get("type")

                    if event_type == "message_update":
                        inner = event.get("assistant_message_event") or {}
                        if inner.get("type") == "text_delta":
                            delta = inner.get("delta", "")
                            if delta:
                                yield {"type": "delta", "text": delta}

                    elif event_type == "tool_execution_start":
                        yield {
                            "type": "tool",
                            "name": event.get("tool_name", ""),
                            "status": "start",
                        }

                    elif event_type == "tool_execution_end":
                        yield {
                            "type": "tool",
                            "name": event.get("tool_name", ""),
                            "status": "end",
                            "is_error": bool(event.get("is_error")),
                        }
                        # Surface any UI specs produced by render_ui
                        while emitted_ui < len(self._ui_specs):
                            yield {"type": "ui", "spec": self._ui_specs[emitted_ui]}
                            emitted_ui += 1

                    elif event_type == "message_end":
                        message = event.get("message")
                        if isinstance(message, AssistantMessage):
                            text_blocks = [
                                block.text
                                for block in message.content
                                if isinstance(block, TextContent) and block.text
                            ]
                            if text_blocks and message.stop_reason == "stop":
                                final_text_parts = text_blocks
            finally:
                unsubscribe()
                # Ensure the prompt task is finished and exceptions observed
                try:
                    await prompt_task
                except Exception:  # noqa: BLE001 — error surfaced via state below
                    pass

            error = self.agent.state.error
            yield {
                "type": "done",
                "text": "\n".join(final_text_parts).strip(),
                "error": error,
            }


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str, channel: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            session = Session(session_id, channel)
            self._sessions[session_id] = session
        return session

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


sessions = SessionRegistry()


async def run_to_completion(session: Session, text: str) -> str:
    """Run a message and return only the final text (used by WhatsApp)."""
    final = ""
    error: str | None = None
    async for event in session.run(text):
        if event["type"] == "done":
            final = event["text"]
            error = event.get("error")
    if error:
        return f"Sorry, something went wrong: {error}"
    return final or "Done."
