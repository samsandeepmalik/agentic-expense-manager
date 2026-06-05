import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import db
    db.DB_PATH = path
    db.init_db()
    return path


@pytest.fixture()
def conn(db_path):
    from app import db
    with db.get_db() as connection:
        yield connection
