"""Tests for multi-file attachments on POST /api/chat/sessions/{id}/messages."""

import asyncio
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routes.chat as chat_routes
from app.errors import register_error_handler


def _client(monkeypatch):
    async def fake_classify(filename, data, profile_id=None):
        raise AssertionError("classify_and_start must not be called for multi-file requests")
    monkeypatch.setattr(chat_routes.imports_svc, "classify_and_start", fake_classify)

    captured = {}

    class FakeSession:
        async def run(self, prompt):
            captured["prompt"] = prompt
            yield {"type": "done", "text": "ok", "error": None}

    monkeypatch.setattr(chat_routes.sessions, "get",
                        lambda sid, channel="ui": FakeSession())

    app = FastAPI()
    register_error_handler(app)
    app.include_router(chat_routes.router)
    return TestClient(app), captured


def test_more_than_ten_files_returns_400_too_many_files(monkeypatch):
    client, _ = _client(monkeypatch)
    files = [("files", (f"r{i}.jpg", b"\xff\xd8\xff", "image/jpeg")) for i in range(11)]
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": "receipts"}, files=files)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "too_many_files"


def test_ten_files_is_allowed(monkeypatch):
    async def fake_ocr(data, content_type, **kwargs):
        return {"filename": "r.jpg", "image_path": "/tmp/r.jpg",
               "ocr_text": "text", "ocr_error": ""}
    monkeypatch.setattr(chat_routes, "save_and_ocr", fake_ocr)
    monkeypatch.setattr(chat_routes, "compose_batch_prompt",
                        lambda user_text, results: f"batch_prompt:{len(results)}")

    client, captured = _client(monkeypatch)
    files = [("files", (f"r{i}.jpg", b"\xff\xd8\xff", "image/jpeg")) for i in range(10)]
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": "receipts"}, files=files)
    assert resp.status_code == 200
    assert captured["prompt"] == "batch_prompt:10"


def test_multi_file_emits_per_file_progress_status(monkeypatch):
    """Each file's OCR step reports its own progress ('Reading receipt N of
    M…'), not one static message for the whole batch — the wait for several
    receipts can run 30-60s+ and a static message reads as hung."""
    async def fake_ocr(data, content_type, **kwargs):
        return {"filename": "r.jpg", "image_path": "/tmp/r.jpg",
               "ocr_text": "text", "ocr_error": ""}
    monkeypatch.setattr(chat_routes, "save_and_ocr", fake_ocr)
    monkeypatch.setattr(chat_routes, "compose_batch_prompt",
                        lambda user_text, results: "prompt")

    client, _ = _client(monkeypatch)
    files = [("files", (f"r{i}.jpg", b"\xff\xd8\xff", "image/jpeg")) for i in range(3)]
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": "receipts"}, files=files)
    assert resp.status_code == 200
    assert "Reading receipt 1 of 3" in resp.text
    assert "Reading receipt 2 of 3" in resp.text
    assert "Reading receipt 3 of 3" in resp.text


def test_mixed_statement_and_receipt_returns_400(monkeypatch):
    client, _ = _client(monkeypatch)
    files = [
        ("files", ("bank.csv", b"a,b\n1,2\n", "text/csv")),
        ("files", ("r1.jpg", b"\xff\xd8\xff", "image/jpeg")),
    ]
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""}, files=files)
    assert resp.status_code == 400
    assert "statement" in resp.json()["error"]["message"].lower()


def test_two_xlsx_files_mixed_returns_400(monkeypatch):
    """Even two statement files together (no receipts) are rejected once len > 1."""
    client, _ = _client(monkeypatch)
    files = [
        ("files", ("bank1.xlsx", b"fake", "application/vnd.ms-excel")),
        ("files", ("bank2.xls", b"fake", "application/vnd.ms-excel")),
    ]
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""}, files=files)
    assert resp.status_code == 400


def test_multiple_images_go_through_batch_receipt_pipeline(monkeypatch):
    captured_call = {"ocr_calls": []}

    async def fake_ocr(data, content_type, **kwargs):
        captured_call["ocr_calls"].append((data, content_type))
        return {"filename": "r.jpg", "image_path": "/tmp/r.jpg",
               "ocr_text": "text", "ocr_error": ""}
    monkeypatch.setattr(chat_routes, "save_and_ocr", fake_ocr)

    def fake_compose(user_text, results):
        captured_call["user_text"] = user_text
        captured_call["results"] = results
        return "batch prompt text"
    monkeypatch.setattr(chat_routes, "compose_batch_prompt", fake_compose)

    client, captured = _client(monkeypatch)
    files = [
        ("files", ("r1.jpg", b"\xff\xd8\xff", "image/jpeg")),
        ("files", ("r2.png", b"\x89PNG", "image/png")),
    ]
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": "two receipts"}, files=files)
    assert resp.status_code == 200
    assert captured["prompt"] == "batch prompt text"
    assert captured_call["user_text"] == "two receipts"
    assert len(captured_call["results"]) == 2
    # Files OCR concurrently now, so call order isn't guaranteed — only that
    # both were actually called (see test_multi_file_results_stay_in_attachment_order
    # for the invariant that actually matters: final result ordering).
    assert set(captured_call["ocr_calls"]) == {
        (b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png")}


def test_pdf_in_multi_file_set_treated_as_receipt_not_statement(monkeypatch):
    """A PDF alongside another file in a multi-file request must NOT be
    classified as a statement (classify_and_start is asserted un-called by
    the fixture) — it goes through the batch receipt pipeline instead."""
    async def fake_ocr(data, content_type, **kwargs):
        return {"filename": "receipt.pdf", "image_path": "/tmp/receipt.pdf",
               "ocr_text": "text", "ocr_error": ""}
    monkeypatch.setattr(chat_routes, "save_and_ocr", fake_ocr)
    monkeypatch.setattr(chat_routes, "compose_batch_prompt",
                        lambda user_text, results: "batch prompt text")

    client, captured = _client(monkeypatch)
    files = [
        ("files", ("receipt.pdf", b"%PDF-1.4", "application/pdf")),
        ("files", ("r1.jpg", b"\xff\xd8\xff", "image/jpeg")),
    ]
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""}, files=files)
    assert resp.status_code == 200
    assert captured["prompt"] == "batch prompt text"


def test_per_file_size_limit_still_applies_in_multi_file_request(monkeypatch):
    client, _ = _client(monkeypatch)
    big = b"x" * (21 * 1024 * 1024)
    files = [
        ("files", ("small.jpg", b"\xff\xd8\xff", "image/jpeg")),
        ("files", ("big.jpg", big, "image/jpeg")),
    ]
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""}, files=files)
    assert resp.status_code == 413


def test_single_statement_file_unaffected_by_multi_file_changes(monkeypatch):
    """len(files) == 1 with a statement extension still goes through
    classify_and_start exactly as today."""
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

    app = FastAPI()
    register_error_handler(app)
    app.include_router(chat_routes.router)
    client = TestClient(app)

    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""},
                       files={"files": ("bank.csv", b"a,b\n1,2\n", "text/csv")})
    assert resp.status_code == 200
    assert "42" in captured["prompt"]


def test_multi_file_ocr_runs_concurrently_across_files(monkeypatch):
    """Files used to OCR one at a time — 10 receipts could take minutes.
    They must run concurrently so wall time stays close to one file's
    latency, not the sum of all of them."""
    async def slow_ocr(data, content_type, **kwargs):
        await asyncio.sleep(0.2)
        return {"filename": "r.jpg", "image_path": "/tmp/r.jpg",
               "ocr_text": "text", "ocr_error": ""}
    monkeypatch.setattr(chat_routes, "save_and_ocr", slow_ocr)
    monkeypatch.setattr(chat_routes, "compose_batch_prompt",
                        lambda user_text, results: "prompt")

    client, _ = _client(monkeypatch)
    files = [("files", (f"r{i}.jpg", b"\xff\xd8\xff", "image/jpeg")) for i in range(6)]

    start = time.monotonic()
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": "receipts"}, files=files)
    elapsed = time.monotonic() - start

    assert resp.status_code == 200
    # Sequential would take ~1.2s (6 x 0.2s); concurrent stays well under that.
    assert elapsed < 0.8


def test_multi_file_results_stay_in_attachment_order_despite_concurrency(monkeypatch):
    """Files complete OCR out of order (fast file finishes before a slow
    earlier one) but compose_batch_prompt must still see them in the order
    they were attached — "receipt 2" must always mean the 2nd file picked,
    not the 2nd one to finish."""
    async def ordered_slow_ocr(data, content_type, **kwargs):
        # First file is the slowest, so it would finish LAST if truly
        # concurrent — proving completion order isn't used for the result list.
        delay = 0.15 if data == b"first" else 0.01
        await asyncio.sleep(delay)
        return {"filename": data.decode(), "image_path": f"/tmp/{data.decode()}",
               "ocr_text": data.decode(), "ocr_error": ""}
    monkeypatch.setattr(chat_routes, "save_and_ocr", ordered_slow_ocr)

    captured_call = {}

    def fake_compose(user_text, results):
        captured_call["results"] = results
        return "prompt"
    monkeypatch.setattr(chat_routes, "compose_batch_prompt", fake_compose)

    client, _ = _client(monkeypatch)
    files = [
        ("files", ("r1.jpg", b"first", "image/jpeg")),
        ("files", ("r2.jpg", b"second", "image/jpeg")),
        ("files", ("r3.jpg", b"third", "image/jpeg")),
    ]
    resp = client.post("/api/chat/sessions/s1/messages",
                       data={"message": ""}, files=files)
    assert resp.status_code == 200
    assert [r["ocr_text"] for r in captured_call["results"]] == ["first", "second", "third"]
