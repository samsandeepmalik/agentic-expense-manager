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


# ---- Upload size limits ----

def test_import_upload_too_large_returns_413(db_path):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    big_data = b"date,amount,merchant\n" + b"2026-01-01,10.00,A\n" * 1_200_000  # ~21 MB
    from io import BytesIO
    resp = client.post(
        "/api/imports",
        files={"file": ("big.csv", BytesIO(big_data), "text/csv")},
    )
    assert resp.status_code == 413


def test_chat_upload_too_large_returns_413(db_path):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    big_data = b"x" * (21 * 1024 * 1024)  # 21 MB
    from io import BytesIO
    resp = client.post(
        "/api/chat/sessions/ui:testsession/messages",
        data={"message": "hi"},
        files={"file": ("big.pdf", BytesIO(big_data), "application/pdf")},
    )
    assert resp.status_code == 413


# ---- Drive public permission removal ----

def test_upload_receipt_does_not_grant_public_permission(monkeypatch):
    """upload_receipt_image must NOT call permissions().create() with type=anyone."""
    granted_permissions = []

    class FakePermissions:
        def create(self, fileId, body):
            granted_permissions.append(body)
            class _Exec:
                def execute(self_): return {}
            return _Exec()

    class FakeFiles:
        def create(self, body, media_body=None, fields=None):
            class _Exec:
                def execute(self_):
                    return {"id": "fake-id", "webViewLink": "https://drive.google.com/fake", "name": "receipt.jpg"}
            return _Exec()

        def list(self, q=None, fields=None, pageSize=None):
            class _Exec:
                def execute(self_): return {"files": [{"id": "folder-id"}]}
            return _Exec()

    class FakeDrive:
        def files(self): return FakeFiles()
        def permissions(self): return FakePermissions()

    import app.services.google_client as gc
    monkeypatch.setattr(gc, "drive_service", lambda: FakeDrive())
    monkeypatch.setattr(gc, "_read", lambda key: "cached-folder-id" if "year" not in str(key) else {"1:2026": "folder-id"})
    monkeypatch.setattr(gc, "_write", lambda key, val: None)

    result = gc.upload_receipt_image("receipt.jpg", b"data", "image/jpeg",
                                      {"id": 1, "name": "Personal"}, "2026-01-01")
    assert result["link"] == "https://drive.google.com/fake"
    assert not any(p.get("type") == "anyone" for p in granted_permissions), \
        "upload_receipt_image must not grant public 'anyone' permission"
