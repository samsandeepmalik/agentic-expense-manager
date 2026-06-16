"""Statement/sheet imports: extract text, agent structures rows, dedup, approve."""

from __future__ import annotations

import csv
import io
import json
import logging
import re

from ..db import get_db
from ..errors import AppError
from . import audit
from . import dedup as dedup_svc
from . import profiles as prof_svc
from . import transactions as txn_svc

logger = logging.getLogger(__name__)


def extract_text(filename: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".csv"):
        return data.decode("utf-8", errors="replace")
    if lower.endswith((".xlsx", ".xls")):
        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                writer.writerow(["" if v is None else v for v in row])
        return buffer.getvalue()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if len(text.strip()) < 40:
            raise AppError("pdf_unreadable",
                           "Couldn't extract text from this PDF (scanned image?). "
                           "Try a CSV export instead.", 422)
        return text
    raise AppError("unsupported_format", "Upload CSV, XLSX or PDF", 415)


PARSE_PROMPT = """Below is the text of a bank statement or expense sheet.
Extract every transaction as a JSON array. Each item:
{{"date": "YYYY-MM-DD", "type": "income"|"expense", "category": "<best guess from: {categories}>",
  "merchant": "...", "description": "...", "total": <number, positive>,
  "loan": <true ONLY if the row clearly marks a loan, else false>,
  "notes": "<any note/memo text on the row, e.g. '20% of house rent (2409 CAD)', else omit>",
  "receipt_link": "<URL if the row contains a receipt/Drive/document link, else omit>"}}
Rules: deposits/credits are income; withdrawals/debits are expense.
Preserve any explanatory note/memo column verbatim in "notes".
Respond with ONLY the JSON array, no prose.

TEXT:
{text}"""


async def parse_with_agent(text: str, profile_id: int) -> list[dict]:
    from pi_agent.agent_core import LlmContext, UserMessage
    from pi_agent.pi_ai import complete

    from ..agent.runtime import _claude_model, _registry

    with get_db() as conn:
        category_names = [c["name"] for c in conn.execute(
            "SELECT name FROM categories WHERE profile_id=?", (profile_id,))]
    prompt = PARSE_PROMPT.format(categories=", ".join(category_names),
                                 text=text[:60000])
    message = await complete(
        model=_claude_model(),
        context=LlmContext(messages=[UserMessage(content=prompt)]),
        registry=_registry)
    raw = "".join(getattr(block, "text", "") for block in message.content)
    match = re.search(r"\[.*\]", raw, re.S)
    if not match:
        raise AppError("parse_failed", "Couldn't structure the file contents", 422)
    return json.loads(match.group(0))


def _persist_import(conn, filename: str, rows: list[dict], profile_id: int,
                    channel: str = "import") -> int:
    """Persist parsed rows as a review-ready import. Flags duplicates and
    initialises per-row skip. Does NOT commit — the caller's get_db() context
    manager owns the transaction boundary. Returns the new import id."""
    flags = dedup_svc.flag_duplicates(conn, rows, profile_id=profile_id)
    for row, flag in zip(rows, flags):
        row["duplicate"] = flag
        row["skip"] = flag
    cursor = conn.execute(
        "INSERT INTO imports(filename, profile_id, channel, status, rows) "
        "VALUES (?,?,?,'review',?)",
        (filename, profile_id, channel, json.dumps(rows)))
    import_id = cursor.lastrowid
    audit.record(conn, "import_uploaded", channel=channel, ref=str(import_id),
                 detail=f"{filename}: {len(rows)} rows parsed",
                 profile_id=profile_id)
    return import_id


async def start_import(filename: str, data: bytes,
                       profile_id: int | None = None,
                       channel: str = "import") -> dict:
    text = extract_text(filename, data)
    with get_db() as conn:
        # Explicit profile (chosen in the import popup) wins; else active book.
        # Validate up front so a bad id fails fast, before parsing/LLM cost.
        if profile_id is None:
            profile_id = prof_svc.active_id(conn)
        else:
            prof_svc.get_profile(conn, profile_id)  # raises profile_not_found
    import_id: int | None = None
    try:
        rows = await parse_with_agent(text, profile_id)
        with get_db() as conn:
            import_id = _persist_import(conn, filename, rows, profile_id, channel)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Import parse failed")
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO imports(filename, profile_id, channel, status, error, rows) "
                "VALUES (?,?,?,'failed',?,?)",
                (filename, profile_id, channel, str(exc), json.dumps([])))
            import_id = cursor.lastrowid
    if import_id is None:
        raise AppError("import_failed", "Import could not be recorded", 500)
    return get_import(import_id)


def get_import(import_id: int) -> dict:
    # Addressed by id; the import carries its own profile_id (chosen at upload).
    # Don't scope to the active profile — a user may import into a non-active
    # book without switching to it first.
    with get_db() as conn:
        row = conn.execute("SELECT * FROM imports WHERE id=?",
                           (import_id,)).fetchone()
    if not row:
        raise AppError("import_not_found", "Import not found", 404)
    record = dict(row)
    record["rows"] = json.loads(record["rows"])
    return record


def approve_import(import_id: int, indexes: list[int] | None,
                   rows: list[dict] | None = None) -> dict:
    record = get_import(import_id)
    if record["status"] != "review":
        raise AppError("not_reviewable", f"Import status is {record['status']}", 409)
    # The review grid lets the user edit rows (category by id, sub-category,
    # loan, notes, total…) before approving. When edited rows come back they
    # replace the parsed ones — same length/order, validated and persisted so
    # the import record reflects exactly what was approved.
    if rows is not None:
        if len(rows) != len(record["rows"]):
            raise AppError("rows_mismatch",
                           "Edited rows don't match the import", 409)
        record["rows"] = rows
        with get_db() as conn:
            conn.execute("UPDATE imports SET rows=? WHERE id=?",
                         (json.dumps(rows), import_id))
    created = 0
    failed: list[dict] = []
    with get_db() as conn:
        for index, row in enumerate(record["rows"]):
            wanted = indexes is None or index in indexes
            if not wanted or row.get("skip"):
                continue
            # Per-row fault tolerance: a single bad row (e.g. an unresolvable /
            # ambiguous category) must not roll back the whole batch. Skip it,
            # report it, and let the good rows import.
            try:
                txn_svc.create_transaction(conn, row | {
                    "source": "import",
                    "profile_id": record["profile_id"],
                    "external_ref": f"import:{import_id}:{index}"}, audit_row=False)
                created += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"index": index,
                               "category": row.get("category"),
                               "error": str(exc)})
        conn.execute("UPDATE imports SET status='approved' WHERE id=?", (import_id,))
        detail = f"{record['filename']}: {created} rows approved"
        if failed:
            detail += f", {len(failed)} skipped (unresolved category)"
        audit.record(conn, "import_approved", channel="import",
                     ref=str(import_id), detail=detail,
                     profile_id=record["profile_id"])
    return {"created": created, "failed": failed}
