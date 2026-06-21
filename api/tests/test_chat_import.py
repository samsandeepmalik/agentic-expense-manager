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


# ---------------------------------------------------------------------------
# _try_upload_import_source
# ---------------------------------------------------------------------------

import asyncio as _asyncio

DRIVE_LINK = "https://drive.google.com/file/d/UPLOAD_ID/view"


def _statement_client(monkeypatch, import_id=42):
    """Returns a TestClient wired so classify_and_start returns kind=statement."""
    async def fake_classify(filename, data, profile_id=None):
        return {"kind": "statement", "import_id": import_id}
    monkeypatch.setattr(chat_routes.imports_svc, "classify_and_start", fake_classify)

    class FakeSession:
        async def run(self, prompt):
            yield {"type": "done", "text": "ok", "error": None}

    monkeypatch.setattr(chat_routes.sessions, "get",
                        lambda sid, channel="ui": FakeSession())
    app = FastAPI(); app.include_router(chat_routes.router)
    return TestClient(app)


def test_drive_upload_success_stores_source_link(monkeypatch):
    """When Drive upload succeeds, set_source_link is called with the returned URL."""
    stored = {}

    def fake_upload(filename, data, mime, *, profile, date, public=True):
        return {"link": DRIVE_LINK}

    def fake_set_source_link(import_id, link):
        stored["import_id"] = import_id
        stored["link"] = link

    monkeypatch.setattr(chat_routes.gc, "upload_receipt_image", fake_upload)
    monkeypatch.setattr(chat_routes.imports_svc, "set_source_link", fake_set_source_link)
    # get_import is called inside _upload to read profile_id
    monkeypatch.setattr(chat_routes.imports_svc, "get_import",
                        lambda iid: {"profile_id": 1})
    monkeypatch.setattr(chat_routes.prof_svc, "get_profile",
                        lambda conn, pid: {"id": pid, "name": "Personal"})

    client = _statement_client(monkeypatch, import_id=42)
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""},
                       files={"file": ("bank.csv", b"a,b\n1,2", "text/csv")})
    assert resp.status_code == 200
    assert stored.get("import_id") == 42
    assert stored.get("link") == DRIVE_LINK


def test_drive_upload_failure_does_not_break_stream(monkeypatch):
    """If Drive upload raises, the SSE stream still completes normally."""
    def exploding_upload(filename, data, mime, *, profile, date, public=True):
        raise RuntimeError("Drive unavailable")

    set_called = {}

    monkeypatch.setattr(chat_routes.gc, "upload_receipt_image", exploding_upload)
    monkeypatch.setattr(chat_routes.imports_svc, "set_source_link",
                        lambda iid, link: set_called.update({"called": True}))
    monkeypatch.setattr(chat_routes.imports_svc, "get_import",
                        lambda iid: {"profile_id": 1})
    monkeypatch.setattr(chat_routes.prof_svc, "get_profile",
                        lambda conn, pid: {"id": pid, "name": "Personal"})

    client = _statement_client(monkeypatch, import_id=7)
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""},
                       files={"file": ("bank.csv", b"a,b\n1,2", "text/csv")})
    assert resp.status_code == 200
    # Stream must end with a done event, not an error
    assert '"type": "done"' in resp.text
    # set_source_link must NOT have been called
    assert not set_called


def test_drive_upload_content_type_none_falls_back_to_guess(monkeypatch):
    """When content_type is None, mimetypes.guess_type is used for the mime."""
    captured_mime = {}

    def fake_upload(filename, data, mime, *, profile, date, public=True):
        captured_mime["mime"] = mime
        return {"link": DRIVE_LINK}

    monkeypatch.setattr(chat_routes.gc, "upload_receipt_image", fake_upload)
    monkeypatch.setattr(chat_routes.imports_svc, "set_source_link", lambda iid, l: None)
    monkeypatch.setattr(chat_routes.imports_svc, "get_import",
                        lambda iid: {"profile_id": 1})
    monkeypatch.setattr(chat_routes.prof_svc, "get_profile",
                        lambda conn, pid: {"id": pid, "name": "Personal"})

    client = _statement_client(monkeypatch, import_id=5)
    # Send without content_type (TestClient passes None when omitted from files tuple)
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""},
                       files={"file": ("report.csv", b"a,b\n1,2")})
    assert resp.status_code == 200
    # mimetypes.guess_type("report.csv") → "text/csv"
    assert captured_mime.get("mime") == "text/csv"


def test_drive_upload_emits_uploading_status_event(monkeypatch):
    """A 'Uploading to Drive…' status SSE event is emitted before the upload."""
    monkeypatch.setattr(chat_routes.gc, "upload_receipt_image",
                        lambda *a, **kw: {"link": DRIVE_LINK})
    monkeypatch.setattr(chat_routes.imports_svc, "set_source_link", lambda iid, l: None)
    monkeypatch.setattr(chat_routes.imports_svc, "get_import",
                        lambda iid: {"profile_id": 1})
    monkeypatch.setattr(chat_routes.prof_svc, "get_profile",
                        lambda conn, pid: {"id": pid, "name": "Personal"})

    client = _statement_client(monkeypatch, import_id=3)
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""},
                       files={"file": ("bank.csv", b"a,b\n1,2", "text/csv")})
    assert resp.status_code == 200
    assert "Uploading to Drive" in resp.text
