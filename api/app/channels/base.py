"""Channel contract: what main.py and the scheduler may rely on.

A channel registry owns N account connections for one transport (WhatsApp,
Telegram, ...). main.py must never import transport-specific names — only
this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

# handler(chat_id, text, image_bytes, image_mime) -> reply text
MessageHandler = Callable[[str, str, bytes | None, str | None], Awaitable[str]]


class BaseChannelRegistry(ABC):
    """All paired accounts for one transport."""

    name: str = "channel"

    @abstractmethod
    def set_handler(self, handler: MessageHandler) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    def list_accounts(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def send_weekly_summary(self) -> None: ...
