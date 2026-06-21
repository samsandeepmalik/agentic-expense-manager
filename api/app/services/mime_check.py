"""Server-side MIME validation for file uploads.

Guards the import and chat upload endpoints against mismatched or disallowed
content types. The browser-supplied Content-Type is validated against both the
declared MIME and the file extension — but extension is the authoritative
check for statement imports (a .csv labelled 'application/octet-stream' is
still acceptable).
"""

from __future__ import annotations

from ..errors import AppError

# Allowed MIME types for statement imports
_STATEMENT_MIMES = frozenset({
    "text/csv",
    "text/plain",
    "application/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",  # common fallback from many browsers
    "application/pdf",
})

# Allowed MIME types for receipt image / chat file uploads
_RECEIPT_MIMES = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
    "image/heif",
    "application/pdf",
})

# Allowed extensions for statement files (lower-case)
_STATEMENT_EXTS = frozenset({".csv", ".xlsx", ".xls", ".pdf"})

# Allowed extensions for receipt files (lower-case)
_RECEIPT_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic",
                            ".heif", ".pdf"})


def _ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def check_statement(filename: str, content_type: str | None) -> None:
    """Raise AppError(415) if the file is not an allowed statement format.

    Extension is authoritative; content_type is validated as a secondary
    check but only when it is non-empty and not a generic fallback.
    """
    ext = _ext(filename)
    if ext not in _STATEMENT_EXTS:
        raise AppError(
            "unsupported_format",
            f"Statement must be CSV, XLSX, XLS or PDF (got '{ext or filename}')",
            415,
        )
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime and mime not in _STATEMENT_MIMES:
            raise AppError(
                "unsupported_mime",
                f"Content-Type '{mime}' is not allowed for statement uploads",
                415,
            )


def check_receipt(filename: str, content_type: str | None) -> None:
    """Raise AppError(415) if the file is not an allowed receipt format."""
    ext = _ext(filename)
    if ext not in _RECEIPT_EXTS:
        raise AppError(
            "unsupported_format",
            f"Receipt must be an image or PDF (got '{ext or filename}')",
            415,
        )
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime and mime not in _RECEIPT_MIMES:
            raise AppError(
                "unsupported_mime",
                f"Content-Type '{mime}' is not allowed for receipt uploads",
                415,
            )
