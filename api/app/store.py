"""Tiny JSON file store for runtime settings."""

from __future__ import annotations

import json
import threading
from typing import Any

from .config import config

_LOCK = threading.Lock()
_FILE = config.data_dir / "settings.json"

_DEFAULTS: dict[str, Any] = {
    "spreadsheet_id": "",
    "drive_folder_id": "",
    "google_tokens": None,
}


def read_settings() -> dict[str, Any]:
    with _LOCK:
        try:
            data = json.loads(_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
    return {**_DEFAULTS, **data}


def write_settings(**updates: Any) -> dict[str, Any]:
    with _LOCK:
        try:
            data = json.loads(_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        data.update(updates)
        _FILE.write_text(json.dumps(data, indent=2))
    return {**_DEFAULTS, **data}
