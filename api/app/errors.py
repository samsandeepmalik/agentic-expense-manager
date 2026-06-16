"""Typed errors and the API error contract: {"error": {code, message}}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400,
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def register_error_handler(app: FastAPI) -> None:
    # Imported lazily: google_client imports this module (avoid a cycle).
    from .services.google_client import GoogleNotConnectedError

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        payload = {"code": exc.code, "message": exc.message}
        if exc.details is not None:
            payload["details"] = exc.details
        return JSONResponse(status_code=exc.status, content={"error": payload})

    @app.exception_handler(GoogleNotConnectedError)
    async def _google_not_connected(request: Request, exc: GoogleNotConnectedError):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "google_not_connected", "message": str(exc)}},
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal", "message": "Something went wrong."}},
        )
