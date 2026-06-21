"""Security hardening tests."""
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_with_key(db_path, monkeypatch):
    """TestClient with API_KEY enforcement enabled."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    # Re-import app after env var is set so Config picks it up.
    import importlib
    import app.config as cfg_mod
    importlib.reload(cfg_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    from fastapi.testclient import TestClient
    client = TestClient(main_mod.app, raise_server_exceptions=False)
    yield client
    # Restore
    monkeypatch.delenv("API_KEY", raising=False)
    importlib.reload(cfg_mod)
    importlib.reload(main_mod)


def test_unauthenticated_request_returns_401(app_with_key):
    resp = app_with_key.get("/api/transactions")
    assert resp.status_code == 401


def test_wrong_key_returns_401(app_with_key):
    resp = app_with_key.get("/api/transactions",
                             headers={"X-Api-Key": "wrong-key"})
    assert resp.status_code == 401


def test_correct_key_is_accepted(app_with_key):
    resp = app_with_key.get("/api/transactions",
                             headers={"X-Api-Key": "test-secret-key"})
    assert resp.status_code == 200


def test_health_endpoint_bypasses_auth(app_with_key):
    resp = app_with_key.get("/api/health")
    assert resp.status_code == 200


def test_no_api_key_configured_allows_all(db_path, monkeypatch):
    """When API_KEY is empty, the server runs open (dev mode)."""
    monkeypatch.delenv("API_KEY", raising=False)
    import importlib
    import app.config as cfg_mod
    importlib.reload(cfg_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    client = TestClient(main_mod.app)
    resp = client.get("/api/transactions")
    assert resp.status_code == 200
