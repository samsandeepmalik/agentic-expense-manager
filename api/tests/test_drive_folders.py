"""Drive folder helpers: URL → id extraction, list/set with a fake service."""
import pytest

from app.services import google_client as gc


@pytest.mark.parametrize("url,expected", [
    ("https://drive.google.com/drive/folders/1AbC_dEf-9", "1AbC_dEf-9"),
    ("https://drive.google.com/drive/u/0/folders/1AbC?usp=share", "1AbC"),
    ("1RawFolderId_-x", "1RawFolderId_-x"),
])
def test_extract_folder_id(url, expected):
    assert gc.extract_folder_id(url) == expected


def test_extract_folder_id_rejects_garbage():
    with pytest.raises(Exception):
        gc.extract_folder_id("https://example.com/nothing")


class FakeFiles:
    def __init__(self, listing):
        self._listing = listing
    def list(self, **kw):
        self.q = kw.get("q", "")
        return self
    def get(self, fileId=None, fields=None):
        self.fileId = fileId
        return self
    def execute(self):
        if hasattr(self, "fileId"):
            return {"id": self.fileId, "name": f"name-of-{self.fileId}",
                    "mimeType": "application/vnd.google-apps.folder"}
        return {"files": self._listing}


class FakeDrive:
    def __init__(self, listing):
        self._files = FakeFiles(listing)
    def files(self):
        return self._files


def test_list_folders_uses_parent(monkeypatch, db_path):
    fake = FakeDrive([{"id": "f1", "name": "Receipts"}])
    monkeypatch.setattr(gc, "drive_service", lambda: fake)
    out = gc.list_folders("parent123")
    assert out == [{"id": "f1", "name": "Receipts"}]
    assert "'parent123' in parents" in fake.files().q


def test_set_drive_folder_validates_and_persists(monkeypatch, db_path):
    fake = FakeDrive([])
    monkeypatch.setattr(gc, "drive_service", lambda: fake)
    info = gc.set_drive_folder("https://drive.google.com/drive/folders/1Target")
    assert info["id"] == "1Target"
    assert gc._read(gc.DRIVE_FOLDER_ID) == "1Target"


def test_list_folders_rejects_invalid_parent(monkeypatch, db_path):
    fake = FakeDrive([])
    monkeypatch.setattr(gc, "drive_service", lambda: fake)
    with pytest.raises(Exception):
        gc.list_folders("abc' or '1'='1")


def test_folder_routes(monkeypatch, db_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.errors import register_error_handler
    from app.routes import google_auth

    fake = FakeDrive([{"id": "f1", "name": "Receipts"}])
    monkeypatch.setattr(gc, "drive_service", lambda: fake)
    app = FastAPI()
    register_error_handler(app)
    app.include_router(google_auth.router)
    client = TestClient(app)

    assert client.get("/api/google/folders").json() == {
        "folders": [{"id": "f1", "name": "Receipts"}]}
    picked = client.post("/api/google/folder",
                         json={"folder": "https://drive.google.com/drive/folders/1X"}).json()
    assert picked["id"] == "1X"
    assert client.post("/api/google/folder",
                       json={"folder": "https://nope.example/x"}).status_code == 422


def test_folder_routes_not_connected(monkeypatch, db_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.errors import register_error_handler
    from app.routes import google_auth

    def boom():
        raise gc.GoogleNotConnectedError()
    monkeypatch.setattr(gc, "drive_service", boom)
    app = FastAPI()
    register_error_handler(app)
    app.include_router(google_auth.router)
    client = TestClient(app)
    response = client.get("/api/google/folders")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "google_not_connected"
