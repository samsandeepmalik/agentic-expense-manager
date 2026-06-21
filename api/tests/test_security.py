"""Security hardening tests."""
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def fresh_app_client(db_path):
    """TestClient built from a freshly reloaded app instance (no API key).

    Reloads config first so data_dir matches the db_path fixture's tmp_path,
    then reloads main so routes see the refreshed config singleton."""
    import importlib
    import app.config as cfg_mod
    importlib.reload(cfg_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app, raise_server_exceptions=False)


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
# Use app_with_key (fresh reload per test) instead of the module-level
# `app.main.app` import, which can be left in a stale state when the auth
# tests above call importlib.reload() and then the suite moves on.

def test_import_upload_too_large_returns_413(app_with_key):
    from io import BytesIO
    big_data = b"date,amount,merchant\n" + b"2026-01-01,10.00,A\n" * 1_200_000  # ~21 MB
    resp = app_with_key.post(
        "/api/imports",
        files={"file": ("big.csv", BytesIO(big_data), "text/csv")},
        headers={"X-Api-Key": "test-secret-key"},
    )
    assert resp.status_code == 413


def test_chat_upload_too_large_returns_413(app_with_key):
    from io import BytesIO
    big_data = b"x" * (21 * 1024 * 1024)  # 21 MB
    resp = app_with_key.post(
        "/api/chat/sessions/ui:testsession/messages",
        data={"message": "hi"},
        files={"file": ("big.pdf", BytesIO(big_data), "application/pdf")},
        headers={"X-Api-Key": "test-secret-key"},
    )
    assert resp.status_code == 413


# ---- Drive public permission control ----

def _make_fake_drive(monkeypatch):
    """Wire a fake Drive service and return (gc module, granted_permissions list)."""
    import app.services.google_client as gc
    granted_permissions: list[dict] = []

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
                    return {
                        "id": "fake-id",
                        "webViewLink": "https://drive.google.com/fake",
                        "name": body.get("name", "file"),
                    }
            return _Exec()

        def list(self, q=None, fields=None, pageSize=None):
            class _Exec:
                def execute(self_): return {"files": [{"id": "folder-id"}]}
            return _Exec()

    class FakeDrive:
        def files(self): return FakeFiles()
        def permissions(self): return FakePermissions()

    monkeypatch.setattr(gc, "drive_service", lambda: FakeDrive())
    # DRIVE_YEAR_FOLDERS must return a dict so ensure_year_folder can do
    # cache[key] = folder_id without a TypeError.  Pre-populate the
    # "1:2026" key so the lookup short-circuits immediately and returns the
    # folder id without touching ensure_drive_folder (which needs real OAuth).
    monkeypatch.setattr(
        gc, "_read",
        lambda key: {"1:2026": "cached-folder-id"}
        if key == gc.DRIVE_YEAR_FOLDERS else "cached-value",
    )
    monkeypatch.setattr(gc, "_write", lambda key, val: None)
    return gc, granted_permissions


def test_statement_upload_does_not_grant_public_permission(monkeypatch):
    """public=False (used for statement source files) must NOT call
    permissions().create(type='anyone').

    Bank statements contain sensitive financial data and must never be
    accessible via an anonymous link even if the Drive link leaks.
    """
    gc, granted = _make_fake_drive(monkeypatch)
    result = gc.upload_receipt_image(
        "statement.csv", b"col1,col2\n1,2\n", "text/csv",
        {"id": 1, "name": "Personal"}, "2026-01-01",
        public=False,
    )
    assert result["link"] == "https://drive.google.com/fake"
    assert not any(p.get("type") == "anyone" for p in granted), \
        "public=False must not grant anyone/reader permission"


def test_receipt_upload_grants_public_permission(monkeypatch):
    """public=True (default for receipt images) MUST call
    permissions().create(type='anyone', role='reader') so the Google Sheets
    receipt link is accessible without a Google login.
    """
    gc, granted = _make_fake_drive(monkeypatch)
    result = gc.upload_receipt_image(
        "receipt.jpg", b"\xff\xd8\xff\xe0", "image/jpeg",
        {"id": 1, "name": "Personal"}, "2026-01-01",
        public=True,
    )
    assert result["link"] == "https://drive.google.com/fake"
    assert any(p.get("type") == "anyone" and p.get("role") == "reader"
               for p in granted), \
        "public=True must grant anyone/reader permission for Sheet links"


# ---- image_path path confinement ----

def test_receipt_outside_data_dir_returns_403(conn, fresh_app_client):
    """image_path pointing outside data_dir must return 403, not serve the file."""
    from app.services import categories as cat_svc

    cat = cat_svc.upsert_category(conn, "Other", "expense", 100, True, None, 0, 1)
    cursor = conn.execute(
        "INSERT INTO transactions(date, type, category_id, description, merchant, "
        "amount, tax_breakdown, total, counted, image_path, source, sync_status, profile_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-01-01", "expense", cat["id"], "", "test",
         10.0, "{}", 10.0, 10.0, "/etc/passwd", "ui", "n/a", 1))
    conn.commit()
    txn_id = cursor.lastrowid

    resp = fresh_app_client.get(f"/api/receipts/{txn_id}")
    assert resp.status_code == 403


def test_receipt_inside_data_dir_is_served(conn, db_path, monkeypatch, fresh_app_client):
    """A valid receipt path inside data_dir is served normally.

    Patches data_dir on the config singleton that app.routes.transactions already
    imported (module-level binding) so _confined_file_response checks tmp_path.
    """
    import app.routes.transactions as txns_route
    from app.services import categories as cat_svc

    # tmp_path is db_path.parent — the DATA_DIR set by the db_path fixture.
    test_root = db_path.parent
    monkeypatch.setattr(txns_route.config, "data_dir", test_root)

    receipts_dir = test_root / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt_file = receipts_dir / "test-receipt.jpg"
    receipt_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # minimal JPEG-ish

    cat = cat_svc.upsert_category(conn, "Other", "expense", 100, True, None, 0, 1)
    cursor = conn.execute(
        "INSERT INTO transactions(date, type, category_id, description, merchant, "
        "amount, tax_breakdown, total, counted, image_path, source, sync_status, profile_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-01-01", "expense", cat["id"], "", "test",
         10.0, "{}", 10.0, 10.0, str(receipt_file), "ui", "n/a", 1))
    conn.commit()
    txn_id = cursor.lastrowid

    resp = fresh_app_client.get(f"/api/receipts/{txn_id}")
    assert resp.status_code == 200
