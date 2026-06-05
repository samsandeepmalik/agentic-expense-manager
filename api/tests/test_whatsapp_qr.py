import time

import pytest

from app.channels.whatsapp import _QR_CODE_TTL_SECONDS, WhatsAppManager


def test_current_qr_fresh_returns_data_uri():
    manager = WhatsAppManager()
    manager.status = "qr"
    manager._qr_codes = ["test-code"]
    manager._qr_issued_at = time.time()
    result = manager.current_qr()
    assert result["status"] == "qr"
    assert result["qr"].startswith("data:image/png;base64,")


def test_current_qr_expires_after_single_ttl():
    """WhatsApp codes die ~20s after issue; never serve a stale one."""
    manager = WhatsAppManager()
    manager.status = "qr"
    manager._qr_codes = ["test-code"]
    manager._qr_issued_at = time.time() - (_QR_CODE_TTL_SECONDS + 6)
    assert manager.current_qr() == {"status": "qr_expired", "qr": None}


@pytest.mark.asyncio
async def test_refresh_qr_resets_and_restarts(monkeypatch):
    manager = WhatsAppManager()
    manager.status = "qr_expired"
    manager._qr_codes = ["stale"]
    started = []

    async def fake_start():
        started.append(True)

    monkeypatch.setattr(manager, "start", fake_start)
    result = await manager.refresh_qr()
    assert started == [True]
    assert manager._qr_codes == []
    assert result == manager.current_qr()


@pytest.mark.asyncio
async def test_refresh_qr_noop_when_connected(monkeypatch):
    manager = WhatsAppManager()
    manager.status = "connected"

    async def fail_start():
        raise AssertionError("must not restart while connected")

    monkeypatch.setattr(manager, "start", fail_start)
    assert await manager.refresh_qr() == {"status": "connected", "qr": None}


@pytest.mark.asyncio
async def test_registry_add_list_remove(monkeypatch, tmp_path, db_path):
    from app.channels import whatsapp as wa
    from app.config import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    registry = wa.WhatsAppRegistry()

    async def fake_start(self):
        self.status = "qr"
    async def fake_stop(self, unpair=False):
        self.status = "disconnected"
        self._stopped_unpair = unpair

    monkeypatch.setattr(wa.WhatsAppManager, "start", fake_start)
    monkeypatch.setattr(wa.WhatsAppManager, "stop", fake_stop)

    account = await registry.add_account()
    assert account["status"] == "qr" and account["id"]

    listed = registry.list_accounts()
    assert len(listed) == 1 and listed[0]["id"] == account["id"]

    manager = registry.get(account["id"])
    manager._db_path.write_bytes(b"session")          # simulate session file

    await registry.remove_account(account["id"])
    assert registry.list_accounts() == []
    assert not manager._db_path.exists()              # session wiped
    assert manager._stopped_unpair is True


@pytest.mark.asyncio
async def test_registry_start_creates_default_when_empty(monkeypatch, tmp_path):
    from app.channels import whatsapp as wa
    from app.config import config

    monkeypatch.setattr(config, "data_dir", tmp_path)

    async def fake_start(self):
        self.status = "qr"
    monkeypatch.setattr(wa.WhatsAppManager, "start", fake_start)

    registry = wa.WhatsAppRegistry()
    await registry.start()
    accounts = registry.list_accounts()
    assert len(accounts) == 1 and accounts[0]["id"] == "default"
