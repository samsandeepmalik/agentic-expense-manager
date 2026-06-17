"""Drive folder: base name setting, ensure_drive_folder, ensure_year_folder, folder-name route."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import register_error_handler
from app.services import google_client as gc


class _FakeCreateResult:
    def execute(self):
        return {"id": "new-folder-id"}


class _FakeListResult:
    def execute(self):
        return {"files": []}


class FakeFiles:
    def create(self, body=None, fields=None, **kwargs):
        return _FakeCreateResult()
    def list(self, **kwargs):
        return _FakeListResult()


class FakeDrive:
    def files(self):
        return FakeFiles()


# ---------------------------------------------------------------------------
# Fake Drive for year-folder tests
# ---------------------------------------------------------------------------

class _FakeListCall:
    def __init__(self, result):
        self._result = result
    def execute(self):
        return {"files": self._result}


class _FakeCreateCall:
    def __init__(self, result_id):
        self._id = result_id
    def execute(self):
        return {"id": self._id}


class _FakeYearFiles:
    def __init__(self, list_result, create_id):
        self._list_result = list_result
        self._create_id = create_id

    def list(self, **kwargs):
        return _FakeListCall(self._list_result)

    def create(self, body=None, fields=None, **kwargs):
        return _FakeCreateCall(self._create_id)


class FakeDriveYear:
    def __init__(self, list_result=None, create_id="new-year-id"):
        self._list_result = list_result or []
        self._create_id = create_id

    def files(self):
        return _FakeYearFiles(self._list_result, self._create_id)


# ---------------------------------------------------------------------------
# get_folder_base_name / set_folder_base_name
# ---------------------------------------------------------------------------

def test_folder_base_name_default(db_path):
    assert gc.get_folder_base_name() == "Expense Manager"


def test_folder_base_name_persists(db_path):
    gc.set_folder_base_name("My Receipts")
    assert gc.get_folder_base_name() == "My Receipts"


def test_set_folder_base_name_rejects_blank(db_path):
    from app.errors import AppError
    with pytest.raises(AppError):
        gc.set_folder_base_name("   ")


# ---------------------------------------------------------------------------
# ensure_drive_folder uses base name from settings
# ---------------------------------------------------------------------------

def test_ensure_app_folder_creates_root(monkeypatch, db_path):
    gc.set_folder_base_name("Work Expenses")
    monkeypatch.setattr(gc, "drive_service", lambda: FakeDriveYear())
    folder_id = gc.ensure_app_folder()
    assert folder_id == "new-year-id"


def test_ensure_app_folder_always_calls_drive_even_when_cached(monkeypatch, db_path):
    from app.db import get_db, set_setting
    from app.settings_keys import DRIVE_ROOT_FOLDER_ID as KEY

    with get_db() as conn:
        set_setting(conn, KEY, "cached-root-id")

    call_count = [0]
    def counting_drive():
        call_count[0] += 1
        return FakeDriveYear(list_result=[{"id": "cached-root-id"}])
    monkeypatch.setattr(gc, "drive_service", counting_drive)

    folder_id = gc.ensure_app_folder()
    assert call_count[0] > 0          # Drive was called despite cached setting
    assert folder_id == "cached-root-id"


def test_ensure_drive_folder_uses_base_name(monkeypatch, db_path):
    from app.db import get_db
    from app.services import profiles as prof_svc

    gc.set_folder_base_name("Work Expenses")
    monkeypatch.setattr(gc, "drive_service", lambda: FakeDrive())
    monkeypatch.setattr(gc, "ensure_app_folder", lambda: "fake-app-folder")

    with get_db() as conn:
        profile = prof_svc.get_profile(conn, prof_svc.active_id(conn))

    folder_id = gc.ensure_drive_folder(profile)
    assert folder_id == "new-folder-id"


def test_ensure_drive_folder_always_calls_drive_even_when_cached(monkeypatch, db_path):
    profile = {"id": 1, "name": "Personal", "drive_folder_id": "existing-id"}
    monkeypatch.setattr(gc, "ensure_app_folder", lambda: "fake-root")
    monkeypatch.setattr(gc, "drive_service",
                        lambda: FakeDriveYear(list_result=[{"id": "existing-id"}]))
    result = gc.ensure_drive_folder(profile)
    assert result == "existing-id"   # existing found, not created


# ---------------------------------------------------------------------------
# /api/google/folder-name route
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(db_path):
    from app.routes import google_auth
    app = FastAPI()
    register_error_handler(app)
    app.include_router(google_auth.router)
    return TestClient(app, raise_server_exceptions=False)


def test_folder_name_route_saves_and_returns(client):
    resp = client.post("/api/google/folder-name", json={"name": "Invoices"})
    assert resp.status_code == 200
    assert resp.json() == {"folder_name": "Invoices"}
    assert gc.get_folder_base_name() == "Invoices"


def test_folder_name_route_rejects_blank(client):
    resp = client.post("/api/google/folder-name", json={"name": "   "})
    assert resp.status_code == 422


def test_old_folders_endpoint_gone(client):
    assert client.get("/api/google/folders").status_code == 404


def test_old_folder_endpoint_gone(client):
    assert client.post("/api/google/folder", json={"folder": "x"}).status_code == 404


# ---------------------------------------------------------------------------
# ensure_year_folder
# ---------------------------------------------------------------------------

def test_ensure_year_folder_creates_when_missing(monkeypatch, db_path):
    profile = {"id": 1, "name": "Personal", "drive_folder_id": "root-id"}
    monkeypatch.setattr(gc, "drive_service", lambda: FakeDriveYear())
    folder_id = gc.ensure_year_folder(profile, 2026)
    assert folder_id == "new-year-id"


def test_ensure_year_folder_reuses_existing_drive_folder(monkeypatch, db_path):
    profile = {"id": 1, "name": "Personal", "drive_folder_id": "root-id"}
    monkeypatch.setattr(gc, "drive_service",
                        lambda: FakeDriveYear(list_result=[{"id": "existing-2025"}]))
    folder_id = gc.ensure_year_folder(profile, 2025)
    assert folder_id == "existing-2025"


def test_ensure_year_folder_caches_in_settings(monkeypatch, db_path):
    from app.db import get_db, get_setting
    from app.settings_keys import DRIVE_YEAR_FOLDERS

    profile = {"id": 1, "name": "Personal", "drive_folder_id": "root-id"}
    monkeypatch.setattr(gc, "drive_service", lambda: FakeDriveYear())
    gc.ensure_year_folder(profile, 2026)

    with get_db() as conn:
        cache = get_setting(conn, DRIVE_YEAR_FOLDERS)
    assert cache["1:2026"] == "new-year-id"


def test_ensure_year_folder_cache_hit_skips_drive(monkeypatch, db_path):
    from app.db import get_db, set_setting
    from app.settings_keys import DRIVE_YEAR_FOLDERS

    profile = {"id": 1, "name": "Personal", "drive_folder_id": "root-id"}
    # Pre-populate cache
    with get_db() as conn:
        set_setting(conn, DRIVE_YEAR_FOLDERS, {"1:2026": "cached-id"})

    call_count = [0]
    def bad_drive():
        call_count[0] += 1
        return FakeDriveYear()

    monkeypatch.setattr(gc, "drive_service", bad_drive)
    folder_id = gc.ensure_year_folder(profile, 2026)
    assert folder_id == "cached-id"
    assert call_count[0] == 0  # Drive was never called
