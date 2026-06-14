"""OAuth client credentials: UI-saved (settings table) with env fallback."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import AppError, register_error_handler
from app.services import google_client as gc


@pytest.fixture()
def client(db_path):
    from app.routes import google_auth as google_routes

    app = FastAPI()
    register_error_handler(app)
    app.include_router(google_routes.router)
    return TestClient(app, raise_server_exceptions=False)


def _blank_env(monkeypatch):
    monkeypatch.setattr(gc.config, "google_client_id", "")
    monkeypatch.setattr(gc.config, "google_client_secret", "")


def test_save_and_read_creds(db_path, monkeypatch):
    _blank_env(monkeypatch)
    assert gc.is_configured() is False
    gc.save_client_creds("id123.apps.googleusercontent.com", "  sec456  ")
    assert gc.client_creds() == ("id123.apps.googleusercontent.com", "sec456")
    assert gc.is_configured() is True
    # flows into the OAuth client config
    web = gc._client_config()["web"]
    assert web["client_id"] == "id123.apps.googleusercontent.com"
    assert web["client_secret"] == "sec456"


def test_env_fallback(db_path, monkeypatch):
    monkeypatch.setattr(gc.config, "google_client_id", "env-id")
    monkeypatch.setattr(gc.config, "google_client_secret", "env-secret")
    assert gc.client_creds() == ("env-id", "env-secret")
    assert gc.is_configured() is True


def test_save_rejects_blank(db_path):
    with pytest.raises(AppError) as exc:
        gc.save_client_creds("", "secret")
    assert exc.value.status == 422
    with pytest.raises(AppError):
        gc.save_client_creds("id", "   ")


def test_credentials_route_flips_configured(client, monkeypatch):
    _blank_env(monkeypatch)
    before = client.get("/api/google/status").json()
    assert before["configured"] is False
    assert before["redirect_uri"].endswith("/api/google/callback")
    assert before["folder_name"] == "Expense Manager"
    assert before["scope_version"] is None

    resp = client.post("/api/google/credentials", json={
        "client_id": "id123", "client_secret": "sec456"})
    assert resp.status_code == 200
    assert resp.json() == {"configured": True}

    after = client.get("/api/google/status").json()
    assert after["configured"] is True
    # secret never echoed back
    assert "sec456" not in resp.text + str(after)


def test_credentials_route_validates(client, monkeypatch):
    _blank_env(monkeypatch)
    resp = client.post("/api/google/credentials", json={
        "client_id": "  ", "client_secret": "x"})
    assert resp.status_code == 422
