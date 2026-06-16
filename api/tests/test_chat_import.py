"""Tests for chat route statement-import integration (Task 7)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import app.routes.chat as chat_routes


def _client(monkeypatch):
    async def fake_classify(filename, data, profile_id=None):
        return {"kind": "statement", "import_id": 42}
    monkeypatch.setattr(chat_routes.imports_svc, "classify_and_start", fake_classify)

    captured = {}

    class FakeSession:
        async def run(self, prompt):
            captured["prompt"] = prompt
            yield {"type": "done", "text": "ok", "error": None}

    monkeypatch.setattr(chat_routes.sessions, "get",
                        lambda sid, channel="ui": FakeSession())
    app = FastAPI(); app.include_router(chat_routes.router)
    return TestClient(app), captured


def test_csv_upload_starts_import_and_injects_id(monkeypatch):
    client, captured = _client(monkeypatch)
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""},
                       files={"file": ("bank.csv", b"a,b\n1,2\n", "text/csv")})
    assert resp.status_code == 200
    assert "42" in captured["prompt"]


def test_image_upload_goes_through_receipt_path(monkeypatch):
    """Images should bypass classify_and_start and use build_receipt_prompt."""
    async def fake_classify(filename, data, profile_id=None):
        raise AssertionError("classify_and_start must not be called for images")
    monkeypatch.setattr(chat_routes.imports_svc, "classify_and_start", fake_classify)

    async def fake_receipt(message, data, mime):
        return f"receipt_prompt:{message}"
    monkeypatch.setattr(chat_routes, "build_receipt_prompt", fake_receipt)

    captured = {}

    class FakeSession:
        async def run(self, prompt):
            captured["prompt"] = prompt
            yield {"type": "done", "text": "ok", "error": None}

    monkeypatch.setattr(chat_routes.sessions, "get",
                        lambda sid, channel="ui": FakeSession())

    app = FastAPI(); app.include_router(chat_routes.router)
    client = TestClient(app)
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": "check this"},
                       files={"file": ("receipt.jpg", b"\xff\xd8\xff", "image/jpeg")})
    assert resp.status_code == 200
    assert captured["prompt"] == "receipt_prompt:check this"


def test_failed_classify_returns_error_message(monkeypatch):
    """If classify_and_start returns kind=failed, stream an error and stop."""
    async def fake_classify(filename, data, profile_id=None):
        return {"kind": "failed", "import_id": None, "error": "bad format"}
    monkeypatch.setattr(chat_routes.imports_svc, "classify_and_start", fake_classify)

    class FakeSession:
        async def run(self, prompt):
            raise AssertionError("session.run must not be called on classify failure")
            yield  # make it a generator

    monkeypatch.setattr(chat_routes.sessions, "get",
                        lambda sid, channel="ui": FakeSession())

    app = FastAPI(); app.include_router(chat_routes.router)
    client = TestClient(app)
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""},
                       files={"file": ("bank.csv", b"garbage", "text/csv")})
    assert resp.status_code == 200
    assert "CSV" in resp.text or "statement" in resp.text.lower()


def test_no_file_plain_message(monkeypatch):
    """Plain text message with no file should go straight to session.run."""
    async def fake_classify(filename, data, profile_id=None):
        raise AssertionError("classify_and_start must not be called without a file")
    monkeypatch.setattr(chat_routes.imports_svc, "classify_and_start", fake_classify)

    captured = {}

    class FakeSession:
        async def run(self, prompt):
            captured["prompt"] = prompt
            yield {"type": "done", "text": "ok", "error": None}

    monkeypatch.setattr(chat_routes.sessions, "get",
                        lambda sid, channel="ui": FakeSession())

    app = FastAPI(); app.include_router(chat_routes.router)
    client = TestClient(app)
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": "hello"})
    assert resp.status_code == 200
    assert captured["prompt"] == "hello"
