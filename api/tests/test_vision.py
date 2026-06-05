import pytest


@pytest.mark.asyncio
async def test_dispatch_respects_setting(db_path, monkeypatch):
    from app.services import vision

    calls = []

    async def fake(name):
        async def run(image_bytes, mime_type):
            calls.append(name)
            return f"text-from-{name}"
        return run

    monkeypatch.setattr(vision, "_nvidia_extract", await fake("nvidia"))
    monkeypatch.setattr(vision, "_claude_extract", await fake("claude"))
    monkeypatch.setattr(vision, "_openai_extract", await fake("openai"))

    # default → nvidia
    assert await vision.extract_text(b"img", "image/jpeg") == "text-from-nvidia"

    from app.db import get_db, set_setting
    with get_db() as conn:
        set_setting(conn, "ocr_provider", "claude")
    assert await vision.extract_text(b"img", "image/jpeg") == "text-from-claude"

    with get_db() as conn:
        set_setting(conn, "ocr_provider", "openai")
    assert await vision.extract_text(b"img", "image/jpeg") == "text-from-openai"


def test_ocr_settings_api(db_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.errors import register_error_handler
    from app.routes import settings as settings_routes

    app = FastAPI()
    register_error_handler(app)
    app.include_router(settings_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    state = client.get("/api/settings/ocr").json()
    assert state["provider"] == "nvidia"
    assert set(state["available"]) == {"nvidia", "claude", "openai"}

    assert client.post("/api/settings/ocr",
                       json={"provider": "claude"}).json()["provider"] == "claude"
    assert client.get("/api/settings/ocr").json()["provider"] == "claude"

    bad = client.post("/api/settings/ocr", json={"provider": "tesseract"})
    assert bad.status_code == 400
