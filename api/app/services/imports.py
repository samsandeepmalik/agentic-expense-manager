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
from . import categories as cat_svc
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
                    channel: str = "import", source_link: str | None = None) -> int:
    """Persist parsed rows as a review-ready import. Flags duplicates and
    initialises per-row skip. Does NOT commit — the caller's get_db() context
    manager owns the transaction boundary. Returns the new import id."""
    flags = dedup_svc.flag_duplicates(conn, rows, profile_id=profile_id)
    for row, flag in zip(rows, flags):
        row["duplicate"] = flag
        row["skip"] = flag
    cursor = conn.execute(
        "INSERT INTO imports(filename, profile_id, channel, status, rows, source_link) "
        "VALUES (?,?,?,'review',?,?)",
        (filename, profile_id, channel, json.dumps(rows), source_link))
    import_id = cursor.lastrowid
    audit.record(conn, "import_uploaded", channel=channel, ref=str(import_id),
                 detail=f"{filename}: {len(rows)} rows parsed",
                 profile_id=profile_id)
    return import_id


def set_source_link(import_id: int, link: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE imports SET source_link=? WHERE id=?", (link, import_id))


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


def _is_spreadsheet(filename: str) -> bool:
    return filename.lower().endswith((".csv", ".xlsx", ".xls"))


async def classify_and_start(filename: str, data: bytes,
                             profile_id: int | None = None) -> dict:
    """Decide receipt vs statement for a chat upload.

    CSV/XLSX/XLS → always statement (delegate to start_import channel="chat").
    PDF → extract_text raises AppError (scanned/unreadable) → receipt;
          else parse_with_agent: >=2 rows → statement, else → receipt.
    Anything else (images, etc.) → receipt.

    Return shapes:
      {"kind": "statement", "import_id": int}
      {"kind": "receipt",   "import_id": None}
      {"kind": "failed",    "import_id": int, "error": str}
    """
    with get_db() as conn:
        pid = profile_id if profile_id is not None else prof_svc.active_id(conn)

    if _is_spreadsheet(filename):
        record = await start_import(filename, data, pid, channel="chat")
        kind = "failed" if record["status"] == "failed" else "statement"
        return {"kind": kind, "import_id": record["id"],
                "error": record.get("error")}

    if filename.lower().endswith(".pdf"):
        try:
            text = extract_text(filename, data)
        except AppError:
            return {"kind": "receipt", "import_id": None}
        rows = await parse_with_agent(text, pid)
        if len(rows) >= 2:
            with get_db() as conn:
                import_id = _persist_import(conn, filename, rows, pid, "chat")
            return {"kind": "statement", "import_id": import_id}
        return {"kind": "receipt", "import_id": None}

    return {"kind": "receipt", "import_id": None}


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
    # Keys the review grid is permitted to supply. profile_id, source, and
    # external_ref are always taken from the import record and the loop index
    # below — client-supplied values for these fields are silently ignored to
    # prevent cross-profile data injection and audit channel spoofing.
    _ALLOWED_ROW_KEYS = frozenset({
        "date", "type", "category", "category_id", "merchant",
        "description", "notes", "total", "loan", "receipt_link", "skip",
        "duplicate",
    })
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
                # Whitelist: only carry forward user-editable fields; server
                # controls profile_id, source, and external_ref.
                row_data = {k: v for k, v in row.items() if k in _ALLOWED_ROW_KEYS}
                if not row_data.get("receipt_link") and record.get("source_link"):
                    row_data["receipt_link"] = record["source_link"]
                txn_svc.create_transaction(conn, row_data | {
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


def _resolve_label(conn, row: dict, pid: int):
    """Best-effort category id for a parsed row; None if unresolved/ambiguous."""
    if row.get("category_id"):
        return int(row["category_id"])
    name = (row.get("category") or "").strip()
    if not name:
        return None
    try:
        cat = cat_svc.find_category_by_name(conn, name, profile_id=pid)
    except AppError:        # ambiguous_category
        return None
    return cat["id"] if cat else None


def _matches(rule_match: dict, index: int, row: dict) -> bool:
    """Return True when *row* (at *index*) satisfies the rule_match predicate.

    Supported keys (first that hits wins):
      "index"    — exact row position (0-based)
      "merchant" — exact merchant name match (case-insensitive, stripped)
      "contains" — substring of merchant (case-insensitive)
    """
    if "index" in rule_match:
        return index == int(rule_match["index"])
    merchant = (row.get("merchant") or "")
    if "merchant" in rule_match:
        return merchant.strip().lower() == str(rule_match["merchant"]).strip().lower()
    if "contains" in rule_match:
        return str(rule_match["contains"]).lower() in merchant.lower()
    return False


def remap_import(conn, import_id: int, mapping: list[dict]) -> dict:
    """Apply a deterministic {match → category_id} mapping to a stored import.

    For each rule in *mapping*, every row whose merchant/index satisfies the
    rule's ``match`` predicate is reassigned to ``category_id`` (first matching
    rule wins per row).  Duplicate flags are then recomputed and the rows are
    persisted.  Returns a fresh ``import_summary``.

    Connection / commit decision
    ----------------------------
    ``get_import`` opens its own ``get_db()`` connection (separate from *conn*),
    so it can only see committed data.  We call ``conn.commit()`` here after the
    UPDATE so that ``import_summary`` → ``get_import`` reads the freshly mapped
    rows.  This is acceptable because:
    * In production the agent calls ``remap_import`` inside its own
      ``with get_db() as conn`` block — committing here is equivalent to that
      block exiting cleanly, just a little earlier.
    * In tests the ``conn`` fixture rolls back at teardown, so the mid-function
      commit is visible to the fresh connection but the fixture still controls
      the overall lifecycle.
    """
    record = get_import(import_id)
    pid = record["profile_id"]
    rows = record["rows"]
    for index, row in enumerate(rows):
        for rule in mapping:
            if _matches(rule.get("match", {}), index, row):
                cat = cat_svc.get_category(conn, int(rule["category_id"]))
                if cat["profile_id"] != pid:
                    raise AppError("category_not_found", "Unknown category", 404)
                row["category_id"] = cat["id"]
                row["category"] = cat["name"]
                break
    flags = dedup_svc.flag_duplicates(conn, rows, profile_id=pid)
    for row, flag in zip(rows, flags):
        row["duplicate"] = flag
        row["skip"] = flag
    conn.execute("UPDATE imports SET rows=? WHERE id=?",
                 (json.dumps(rows), import_id))
    conn.commit()  # needed so get_import's separate connection sees the update
    return import_summary(conn, import_id)


def import_summary(conn, import_id: int, *, sample_cap: int = 10,
                   unresolved_cap: int = 15) -> dict:
    record = get_import(import_id)
    pid = record["profile_id"]
    rows = record["rows"]
    buckets: dict[str, dict] = {}
    unresolved: list[dict] = []
    duplicates = 0
    for index, row in enumerate(rows):
        if row.get("duplicate"):
            duplicates += 1
        label = (row.get("category") or "(none)").strip() or "(none)"
        resolved = _resolve_label(conn, row, pid)
        bucket = buckets.setdefault(
            label, {"label": label, "count": 0, "resolved_category_id": resolved})
        bucket["count"] += 1
        if resolved is None and len(unresolved) < unresolved_cap:
            unresolved.append({"index": index,
                               "merchant": row.get("merchant", ""),
                               "total": row.get("total"),
                               "guessed": label})
    return {
        "import_id": import_id,
        "profile": prof_svc.get_profile(conn, pid)["name"],
        "total_rows": len(rows),
        "duplicates": duplicates,
        "to_record": sum(1 for r in rows if not r.get("skip")),
        "parsed_categories": list(buckets.values()),
        "unresolved": unresolved,
        "sample": [{"date": r.get("date"), "merchant": r.get("merchant"),
                    "total": r.get("total"), "category": r.get("category")}
                   for r in rows[:sample_cap]],
    }
