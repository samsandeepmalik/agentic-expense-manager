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
  "receipt_link": "<URL if the row contains a receipt/Drive/document link, else omit>"}}
Rules: deposits/credits are income; withdrawals/debits are expense.
Respond with ONLY the JSON array, no prose.

TEXT:
{text}"""


async def parse_with_agent(text: str) -> list[dict]:
    from pi_agent.agent_core import LlmContext, UserMessage
    from pi_agent.pi_ai import complete

    from ..agent.runtime import _claude_model, _registry

    with get_db() as conn:
        category_names = [c["name"] for c in
                          conn.execute("SELECT name FROM categories")]
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


async def start_import(filename: str, data: bytes) -> dict:
    text = extract_text(filename, data)
    with get_db() as conn:
        cursor = conn.execute("INSERT INTO imports(filename) VALUES (?)", (filename,))
        import_id = cursor.lastrowid
    try:
        rows = await parse_with_agent(text)
        with get_db() as conn:
            flags = dedup_svc.flag_duplicates(conn, rows)
            for row, flag in zip(rows, flags):
                row["duplicate"] = flag
                row["skip"] = flag
            conn.execute("UPDATE imports SET status='review', rows=? WHERE id=?",
                         (json.dumps(rows), import_id))
            audit.record(conn, "import_uploaded", channel="import",
                         ref=str(import_id),
                         detail=f"{filename}: {len(rows)} rows parsed")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Import parse failed")
        with get_db() as conn:
            conn.execute("UPDATE imports SET status='failed', error=? WHERE id=?",
                         (str(exc), import_id))
    return get_import(import_id)


def get_import(import_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone()
    if not row:
        raise AppError("import_not_found", "Import not found", 404)
    record = dict(row)
    record["rows"] = json.loads(record["rows"])
    return record


def approve_import(import_id: int, indexes: list[int] | None) -> dict:
    record = get_import(import_id)
    if record["status"] != "review":
        raise AppError("not_reviewable", f"Import status is {record['status']}", 409)
    created = 0
    with get_db() as conn:
        for index, row in enumerate(record["rows"]):
            wanted = indexes is None or index in indexes
            if not wanted or row.get("skip"):
                continue
            txn_svc.create_transaction(conn, row | {
                "source": "import",
                "external_ref": f"import:{import_id}:{index}"}, audit_row=False)
            created += 1
        conn.execute("UPDATE imports SET status='approved' WHERE id=?", (import_id,))
        audit.record(conn, "import_approved", channel="import",
                     ref=str(import_id),
                     detail=f"{record['filename']}: {created} rows approved")
    return {"created": created}
