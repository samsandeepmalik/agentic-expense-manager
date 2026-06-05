"""WhatsApp channel via neonize (whatsmeow bindings).

Pairing: the user scans a QR code shown on the dashboard. QR codes come from
the QREv event; each code in the batch is valid ~20 seconds, so the manager
rotates through them by elapsed time.

Incoming text/image messages are routed to an async handler injected by the
app (which runs the receipt pipeline / agent) and the reply is sent back.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import qrcode

from ..config import config

logger = logging.getLogger(__name__)

# handler(chat_id, text, image_bytes, image_mime) -> reply text
MessageHandler = Callable[[str, str, bytes | None, str | None], Awaitable[str]]

_QR_CODE_TTL_SECONDS = 20


class WhatsAppManager:
    def __init__(self) -> None:
        self.status: str = "disconnected"  # disconnected | qr | connected
        self._qr_codes: list[str] = []
        self._qr_issued_at: float = 0.0
        self._client: Any = None
        self._connect_task: asyncio.Task | None = None
        self._handler: MessageHandler | None = None
        self._reply_jids: dict[str, Any] = {}

    def set_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create the client and connect in the background."""
        if self._connect_task is not None:
            return

        # Imported lazily: neonize loads a native library at import time.
        from neonize.aioze.client import NewAClient
        from neonize.events import (
            ConnectedEv,
            LoggedOutEv,
            MessageEv,
            PairStatusEv,
        )

        db_path = str(config.data_dir / "whatsapp.sqlite3")
        client = NewAClient(db_path)
        self._client = client

        # neonize delivers each rotating QR code (valid ~20s) via a dedicated
        # callback, replacing the default render-to-terminal behaviour.
        async def _on_qr(_client: Any, code: bytes) -> None:
            self._qr_codes = [code.decode()]
            self._qr_issued_at = time.time()
            if self.status != "connected":
                self.status = "qr"
            logger.info("WhatsApp QR code rotated")

        client.event.qr(_on_qr)

        @client.event(ConnectedEv)
        async def _on_connected(_client: Any, _event: Any) -> None:
            self.status = "connected"
            self._qr_codes = []
            logger.info("WhatsApp connected")

        @client.event(PairStatusEv)
        async def _on_paired(_client: Any, event: Any) -> None:
            self.status = "connected"
            self._qr_codes = []
            logger.info("WhatsApp paired")

        @client.event(LoggedOutEv)
        async def _on_logged_out(_client: Any, _event: Any) -> None:
            self.status = "disconnected"
            logger.info("WhatsApp logged out")

        @client.event(MessageEv)
        async def _on_message(_client: Any, event: Any) -> None:
            try:
                await self._handle_message(event)
            except Exception:  # noqa: BLE001
                logger.exception("WhatsApp message handling failed")

        async def _connect() -> None:
            try:
                await client.connect()
            except Exception:  # noqa: BLE001
                logger.exception("WhatsApp connection ended")
                self.status = "disconnected"
                self._connect_task = None

        self._connect_task = asyncio.create_task(_connect())

    # ------------------------------------------------------------------
    # QR for the dashboard
    # ------------------------------------------------------------------

    def current_qr(self) -> dict[str, Any]:
        """Status plus a data-URI PNG of the currently valid QR code."""
        if self.status == "connected":
            return {"status": "connected", "qr": None}
        if not self._qr_codes:
            return {"status": self.status, "qr": None}

        elapsed = time.time() - self._qr_issued_at
        if elapsed > _QR_CODE_TTL_SECONDS * 3:  # no rotation lately — stale
            return {"status": "qr_expired", "qr": None}

        image = qrcode.make(self._qr_codes[-1])
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        return {"status": "qr", "qr": data_uri}

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def _handle_message(self, event: Any) -> None:
        source = event.Info.MessageSource
        if source.IsFromMe or source.IsGroup:
            return
        if self._handler is None:
            return

        message = event.Message
        text = message.conversation or message.extendedTextMessage.text or ""

        image_bytes: bytes | None = None
        image_mime: str | None = None
        if message.HasField("imageMessage"):
            image_mime = message.imageMessage.mimetype or "image/jpeg"
            text = text or message.imageMessage.caption or ""
            image_bytes = await self._client.download_any(message)

        if not text and not image_bytes:
            return

        from neonize.utils.jid import Jid2String, JIDToNonAD

        chat_jid = source.Chat
        chat_id = Jid2String(JIDToNonAD(chat_jid))
        self._reply_jids[chat_id] = chat_jid

        from ..db import get_db, set_setting
        with get_db() as conn:
            set_setting(conn, "whatsapp_summary_chat", chat_id)

        reply = await self._handler(chat_id, text, image_bytes, image_mime)
        if reply:
            await self._client.send_message(chat_jid, reply)

    async def send(self, chat_id: str, text: str) -> None:
        jid = self._reply_jids.get(chat_id)
        if jid is None or self._client is None:
            raise RuntimeError(f"No known WhatsApp chat: {chat_id}")
        await self._client.send_message(jid, text)

    async def send_weekly_summary(self) -> None:
        from ..db import get_db, get_setting
        from ..services.summary_text import weekly_summary_text
        with get_db() as conn:
            chat_id = get_setting(conn, "whatsapp_summary_chat")
        if chat_id and self.status == "connected" and chat_id in self._reply_jids:
            await self.send(chat_id, weekly_summary_text())


whatsapp = WhatsAppManager()
