"""WhatsApp channel via neonize (whatsmeow bindings).

Pairing: the user scans a QR code shown on the dashboard. QR codes arrive via
the client.event.qr callback; each code is valid ~20 seconds, so only the
freshest one is served.

Multiple accounts: WhatsAppRegistry manages one WhatsAppManager (one neonize
client + session DB) per paired account. Incoming text/image messages from any
account are routed to an async handler injected by the app (which runs the
receipt pipeline / agent) and the reply is sent back.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import qrcode

from ..config import config
from ..errors import AppError

logger = logging.getLogger(__name__)

# handler(chat_id, text, image_bytes, image_mime) -> reply text
MessageHandler = Callable[[str, str, bytes | None, str | None], Awaitable[str]]

_QR_CODE_TTL_SECONDS = 20


class WhatsAppManager:
    """One paired WhatsApp account: neonize client + session DB + QR state."""

    def __init__(self, account_id: str = "default",
                 db_path: Path | None = None) -> None:
        self.id = account_id
        self._db_path = db_path or (config.data_dir / "whatsapp.sqlite3")
        self.device: str = ""
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

        client = NewAClient(str(self._db_path))
        self._client = client

        # neonize delivers each rotating QR code (valid ~20s) via a dedicated
        # callback, replacing the default render-to-terminal behaviour.
        async def _on_qr(_client: Any, code: bytes) -> None:
            self._qr_codes = [code.decode()]
            self._qr_issued_at = time.time()
            if self.status != "connected":
                self.status = "qr"
            logger.info("WhatsApp[%s] QR code rotated", self.id)

        client.event.qr(_on_qr)

        @client.event(ConnectedEv)
        async def _on_connected(_client: Any, _event: Any) -> None:
            self.status = "connected"
            self._qr_codes = []
            try:
                me = await client.get_me()
                self.device = (getattr(me, "PushName", "")
                               or str(getattr(me, "JID", "")))
            except Exception:  # noqa: BLE001 — cosmetic only
                self.device = ""
            logger.info("WhatsApp[%s] connected", self.id)

        @client.event(PairStatusEv)
        async def _on_paired(_client: Any, event: Any) -> None:
            self.status = "connected"
            self._qr_codes = []
            logger.info("WhatsApp[%s] paired", self.id)

        @client.event(LoggedOutEv)
        async def _on_logged_out(_client: Any, _event: Any) -> None:
            self.status = "disconnected"
            logger.info("WhatsApp[%s] logged out", self.id)

        @client.event(MessageEv)
        async def _on_message(_client: Any, event: Any) -> None:
            try:
                await self._handle_message(event)
            except Exception:  # noqa: BLE001
                logger.exception("WhatsApp[%s] message handling failed", self.id)

        async def _connect() -> None:
            try:
                await client.connect()
            except Exception:  # noqa: BLE001
                logger.exception("WhatsApp[%s] connection ended", self.id)
                self.status = "disconnected"
                self._connect_task = None

        self._connect_task = asyncio.create_task(_connect())

    async def stop(self, unpair: bool = False) -> None:
        """Tear down the client; with unpair=True also log out server-side."""
        task, client = self._connect_task, self._client
        self._connect_task = None
        self._client = None
        self._qr_codes = []
        self.status = "disconnected"

        if client is not None:
            if unpair:
                try:
                    await client.logout()
                except Exception:  # noqa: BLE001 — may never have paired
                    logger.exception("WhatsApp[%s] logout failed", self.id)
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 — old client may already be dead
                logger.exception("WhatsApp[%s] disconnect failed", self.id)
        if task is not None:
            task.cancel()

    async def refresh_qr(self) -> dict[str, Any]:
        """Tear down a stale pairing client and reconnect to get fresh QR codes.

        No-op while connected. Reuses start() — event wiring unchanged.
        """
        if self.status == "connected":
            return self.current_qr()
        await self.stop()
        await self.start()
        return self.current_qr()

    # ------------------------------------------------------------------
    # QR for the dashboard
    # ------------------------------------------------------------------

    def current_qr(self) -> dict[str, Any]:
        """Status plus a data-URI PNG of the currently valid QR code."""
        if self.status == "connected":
            return {"status": "connected", "qr": None}
        if not self._qr_codes:
            return {"status": self.status, "qr": None}

        # WhatsApp invalidates each code ~20s after issue; serving a stale one
        # makes the phone's scanner fail silently (it then suggests linking by
        # phone number). Small grace covers render/poll latency only.
        elapsed = time.time() - self._qr_issued_at
        if elapsed > _QR_CODE_TTL_SECONDS + 5:
            return {"status": "qr_expired", "qr": None}

        image = qrcode.make(self._qr_codes[-1])
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        return {"status": "qr", "qr": data_uri}

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "device": self.device, **self.current_qr()}

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


class WhatsAppRegistry:
    """All paired WhatsApp accounts. Session DBs live in data_dir/whatsapp/."""

    def __init__(self) -> None:
        self._managers: dict[str, WhatsAppManager] = {}
        self._handler: MessageHandler | None = None

    def _sessions_dir(self) -> Path:
        directory = config.data_dir / "whatsapp"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def set_handler(self, handler: MessageHandler) -> None:
        self._handler = handler
        for manager in self._managers.values():
            manager.set_handler(handler)

    def _add(self, account_id: str, db_path: Path) -> WhatsAppManager:
        manager = WhatsAppManager(account_id, db_path)
        if self._handler is not None:
            manager.set_handler(self._handler)
        self._managers[account_id] = manager
        return manager

    async def start(self) -> None:
        """Start every known account; with none, open a default pairing slot."""
        legacy = config.data_dir / "whatsapp.sqlite3"
        if legacy.exists() and "default" not in self._managers:
            self._add("default", legacy)
        for path in sorted(self._sessions_dir().glob("*.sqlite3")):
            if path.stem not in self._managers:
                self._add(path.stem, path)
        if not self._managers:
            self._add("default", self._sessions_dir() / "default.sqlite3")
        for manager in self._managers.values():
            await manager.start()

    def list_accounts(self) -> list[dict[str, Any]]:
        return [manager.info() for manager in self._managers.values()]

    def get(self, account_id: str) -> WhatsAppManager:
        manager = self._managers.get(account_id)
        if manager is None:
            raise AppError("whatsapp_account_not_found",
                           f"No WhatsApp account: {account_id}", 404)
        return manager

    async def add_account(self) -> dict[str, Any]:
        account_id = uuid.uuid4().hex[:8]
        manager = self._add(account_id,
                            self._sessions_dir() / f"{account_id}.sqlite3")
        await manager.start()
        return manager.info()

    async def remove_account(self, account_id: str) -> None:
        manager = self.get(account_id)
        await manager.stop(unpair=True)
        self._managers.pop(account_id, None)
        for suffix in ("", "-wal", "-shm"):
            Path(f"{manager._db_path}{suffix}").unlink(missing_ok=True)

    def first(self) -> WhatsAppManager | None:
        return next(iter(self._managers.values()), None)

    async def send_weekly_summary(self) -> None:
        from ..db import get_db, get_setting
        from ..services.summary_text import weekly_summary_text
        with get_db() as conn:
            chat_id = get_setting(conn, "whatsapp_summary_chat")
        if not chat_id:
            return
        for manager in self._managers.values():
            if manager.status == "connected" and chat_id in manager._reply_jids:
                await manager.send(chat_id, weekly_summary_text())
                return


whatsapp = WhatsAppRegistry()
