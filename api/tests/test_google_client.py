from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import google.auth.exceptions
import pytest
from google.oauth2.credentials import Credentials

from app.errors import AppError
from app.services import google_client as gc
from app.services.google_client import _creds_to_dict

_FAKE_TOKENS = {
    "token": "access_tok",
    "refresh_token": "refresh_tok",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_id": "cid",
    "client_secret": "csec",
    "scopes": ["https://www.googleapis.com/auth/drive.file"],
    "expiry": None,
}


def test_refresh_error_raises_app_error():
    mock_creds = MagicMock(spec=Credentials)
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh_tok"
    mock_creds.refresh.side_effect = google.auth.exceptions.RefreshError("Token has been expired or revoked.")

    with patch("app.services.google_client._read", return_value=dict(_FAKE_TOKENS)), \
         patch("app.services.google_client.Credentials", return_value=mock_creds):
        with pytest.raises(AppError) as exc_info:
            gc.get_credentials()

    err = exc_info.value
    assert err.code == "google_token_expired"
    assert err.status == 401


def test_expiry_round_trip():
    expiry = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    creds = Credentials(
        token="tok",
        refresh_token="ref",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="cid",
        client_secret="csec",
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    creds.expiry = expiry
    d = _creds_to_dict(creds)
    assert d["expiry"] == expiry.isoformat()

    expiry_str = d.pop("expiry")
    creds2 = Credentials(**d)
    creds2.expiry = datetime.fromisoformat(expiry_str)
    assert creds2.expiry == expiry
