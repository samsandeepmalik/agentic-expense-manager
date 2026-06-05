"""Environment configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from repo root or api/ dir, whichever exists.
_HERE = Path(__file__).resolve().parent.parent
for candidate in (_HERE.parent / ".env", _HERE / ".env"):
    if candidate.exists():
        load_dotenv(candidate)
        break


class Config:
    port: int = int(os.getenv("PORT", "8000"))
    web_origin: str = os.getenv("WEB_ORIGIN", "http://localhost:5173")

    claude_oauth_token: str = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
    anthropic_base_url: str = os.getenv(
        "ANTHROPIC_BASE_URL", "https://api.anthropic.com"
    )

    nvidia_api_key: str = os.getenv("NVIDIA_API_KEY", "")
    nvidia_ocr_model: str = os.getenv("NVIDIA_OCR_MODEL", "baidu/paddleocr")

    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_redirect_uri: str = os.getenv(
        "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/google/callback"
    )
    google_spreadsheet_id: str = os.getenv("GOOGLE_SPREADSHEET_ID", "")
    google_drive_folder_id: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

    # Runtime state: google tokens, whatsapp session, settings
    data_dir: Path = Path(os.getenv("DATA_DIR", str(_HERE / "data")))


config = Config()
config.data_dir.mkdir(parents=True, exist_ok=True)
