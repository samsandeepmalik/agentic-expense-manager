"""One-off Google Drive cleanup: find duplicate/orphan Expense-Manager files.

Dry-run by default — prints what it WOULD trash. Pass --delete to move them to
the Drive trash (reversible; not a permanent delete).

Run from repo root so it uses your real data/tokens:

    cd api && DATA_DIR=../data poetry run python tools/drive_cleanup.py
    cd api && DATA_DIR=../data poetry run python tools/drive_cleanup.py --delete

What it flags:
- Spreadsheets named "Expense Manager — ..." NOT referenced by any profile's
  spreadsheet_id (duplicates / orphans from old code or test runs).
- Sub-folders under the app root whose name is not a current profile name
  (e.g. a leftover "Business" test folder).

It NEVER touches receipt files, year folders, the app root folder, or the
sheet/folder each profile currently points at.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.db import get_db, get_setting
from app.services import google_client as gc
from app.settings_keys import DRIVE_ROOT_FOLDER_ID, DRIVE_YEAR_FOLDERS


def main(delete: bool) -> None:
    if not gc.is_connected():
        print("Google not connected in this DATA_DIR. Aborting.")
        return

    with get_db() as conn:
        profiles = [dict(r) for r in conn.execute(
            "SELECT id, name, spreadsheet_id, drive_folder_id FROM profiles")]
        root_id = get_setting(conn, DRIVE_ROOT_FOLDER_ID)
        year_folders = get_setting(conn, DRIVE_YEAR_FOLDERS) or {}

    keep_sheet_ids = {p["spreadsheet_id"] for p in profiles if p["spreadsheet_id"]}
    keep_folder_ids = {p["drive_folder_id"] for p in profiles if p["drive_folder_id"]}
    keep_folder_ids |= set(year_folders.values())
    if root_id:
        keep_folder_ids.add(root_id)
    profile_names = {p["name"] for p in profiles}

    drive = gc.drive_service()
    files = drive.files().list(
        q="trashed=false",
        fields="files(id,name,mimeType,parents,createdTime)",
        pageSize=1000, orderBy="createdTime",
    ).execute().get("files", [])

    candidates = []
    for f in files:
        mime, name, fid = f["mimeType"], f["name"], f["id"]
        if "spreadsheet" in mime:
            if name.startswith("Expense Manager") and fid not in keep_sheet_ids:
                candidates.append((f, "orphan/duplicate spreadsheet"))
        elif mime.endswith(".folder"):
            parents = f.get("parents", [])
            if root_id and root_id in parents and name not in profile_names \
                    and fid not in keep_folder_ids:
                candidates.append((f, "folder not matching any profile"))

    if not candidates:
        print("Nothing to clean — no duplicate/orphan files found.")
        return

    print(f"{'TRASHING' if delete else 'WOULD TRASH'} {len(candidates)} item(s):")
    for f, why in candidates:
        print(f"  - {f['name']!r:40} {f['mimeType'].split('.')[-1]:12} "
              f"id={f['id']} created={f.get('createdTime','')[:19]}  ← {why}")

    if not delete:
        print("\nDry run. Re-run with --delete to move these to the Drive trash.")
        return

    for f, _ in candidates:
        drive.files().update(fileId=f["id"], body={"trashed": True}).execute()
    print(f"\nDone — {len(candidates)} item(s) moved to Drive trash (recoverable).")


if __name__ == "__main__":
    main(delete="--delete" in sys.argv)
