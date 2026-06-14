"""
End-to-end verification: Google Drive folder structure + Sheet contents.

Run inside the container:
  docker exec expense_management-api-1 python3 /app/tests/verify_drive_e2e.py

Or locally (if Google is connected in api/data/expense.db):
  cd api && poetry run python3 tests/verify_drive_e2e.py
"""

from __future__ import annotations

import json
import sys
import os

# allow running from api/ dir
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db import get_db
from app.services import google_client as gc

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"

errors: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    status = PASS if ok else FAIL
    print(f"  {status} {label}" + (f": {detail}" if detail else ""))
    if not ok:
        errors.append(label)


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def verify_folder_contains_file(drive, folder_id: str, file_id: str,
                                  label: str) -> bool:
    """Return True if file_id is a child of folder_id."""
    results = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id,name,mimeType)",
        pageSize=50,
    ).execute().get("files", [])
    ids = {f["id"] for f in results}
    names = {f["id"]: f["name"] for f in results}
    if file_id in ids:
        return True
    print(f"    Folder children: {[names[i] for i in ids] or '(empty)'}")
    return False


def run() -> None:
    section("Google connection")
    check("is_configured", gc.is_configured())
    check("is_connected", gc.is_connected())
    if not gc.is_connected():
        print("\n  Google not connected — connect via Settings first.")
        sys.exit(1)

    scope = gc.get_scope_version()
    check("scope_version set", scope is not None, scope or "None")
    check("using sandboxed scope (drive.file)", scope == "sandboxed",
          f"actual: {scope}")

    section("Drive: root app folder")
    drive = gc.drive_service()
    sheets_svc = gc.sheets_service()

    with get_db() as conn:
        profiles = [dict(r) for r in conn.execute(
            "SELECT id,name,spreadsheet_id,drive_folder_id,sheet_in_drive FROM profiles"
        )]

    root_folder_id = gc._read(gc.DRIVE_ROOT_FOLDER_ID if hasattr(gc, 'DRIVE_ROOT_FOLDER_ID')
                               else 'drive_root_folder_id')
    check("DRIVE_ROOT_FOLDER_ID cached", bool(root_folder_id), root_folder_id or "missing")

    if root_folder_id:
        try:
            meta = drive.files().get(fileId=root_folder_id,
                                      fields="id,name,trashed").execute()
            check("root folder exists in Drive", not meta.get("trashed", True),
                  meta.get("name", ""))
            check("root folder name correct",
                  meta.get("name") == gc.get_folder_base_name(),
                  f"'{meta.get('name')}' vs '{gc.get_folder_base_name()}'")
        except Exception as e:
            check("root folder accessible", False, str(e))

    section("Per-profile verification")
    for profile in profiles:
        pname = profile["name"]
        print(f"\n  Profile: {pname}")

        sheet_id = profile["spreadsheet_id"]
        folder_id = profile["drive_folder_id"]
        sheet_in_drive = profile["sheet_in_drive"]

        check(f"[{pname}] has spreadsheet_id", bool(sheet_id), sheet_id or "None")
        check(f"[{pname}] has drive_folder_id", bool(folder_id), folder_id or "None")
        check(f"[{pname}] sheet_in_drive flag", bool(sheet_in_drive))

        if not (sheet_id and folder_id):
            print(f"    Skipping Drive checks — IDs missing")
            continue

        # Verify profile folder exists inside root
        if root_folder_id:
            profile_in_root = verify_folder_contains_file(
                drive, root_folder_id, folder_id,
                f"profile folder inside app root"
            )
            check(f"[{pname}] profile folder inside app root", profile_in_root)

        # Verify sheet inside profile folder
        sheet_in_folder = verify_folder_contains_file(
            drive, folder_id, sheet_id,
            f"sheet inside profile folder"
        )
        check(f"[{pname}] sheet inside profile folder", sheet_in_folder)

        # Verify sheet is accessible and has correct structure
        try:
            ss = sheets_svc.spreadsheets().get(
                spreadsheetId=sheet_id,
                fields="properties.title,sheets.properties.title"
            ).execute()
            title = ss.get("properties", {}).get("title", "")
            check(f"[{pname}] sheet accessible", True, title)
            tabs = [s["properties"]["title"] for s in ss.get("sheets", [])]
            check(f"[{pname}] has year tabs", bool(tabs), str(tabs))
            has_legacy = "Transactions" in tabs
            has_year = any(t.isdigit() and len(t) == 4 for t in tabs)
            if has_legacy and not has_year:
                print(f"    {WARN} legacy 'Transactions' tab — existing sheet, backward-compat mode (OK)")
            else:
                check(f"[{pname}] year-based tabs", has_year, f"tabs={tabs}")

            # Check headers in first tab
            first_tab = tabs[0] if tabs else None
            if first_tab:
                headers = sheets_svc.spreadsheets().values().get(
                    spreadsheetId=sheet_id,
                    range=f"'{first_tab}'!A1:M1"
                ).execute().get("values", [[]])
                if headers:
                    check(f"[{pname}][{first_tab}] headers present",
                          headers[0][0] == "ID",
                          str(headers[0][:4]))
                    # Check row count
                    all_rows = sheets_svc.spreadsheets().values().get(
                        spreadsheetId=sheet_id,
                        range=f"'{first_tab}'!A2:A"
                    ).execute().get("values", [])
                    print(f"    Rows in sheet tab '{first_tab}': {len(all_rows)}"
                          + (" (profile has no transactions yet)" if len(all_rows) == 0 else ""))
        except Exception as e:
            check(f"[{pname}] sheet accessible", False, str(e))

        # Verify year receipt folders inside profile folder
        try:
            children = drive.files().list(
                q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id,name)",
            ).execute().get("files", [])
            year_folders = [f["name"] for f in children if f["name"].isdigit() and len(f["name"]) == 4]
            print(f"    Year folders in Drive: {year_folders or '(none yet)'}")
            # Year folders only appear after first receipt upload — not required
        except Exception as e:
            print(f"    {WARN} Could not list year folders: {e}")

    section("Sync: create test transaction and verify round-trip")
    import requests

    # Use active profile
    active = next((p for p in profiles if p.get("active", False)), profiles[0] if profiles else None)
    # Actually profiles from DB don't have 'active' — need to check separately
    with get_db() as conn:
        active_id = conn.execute(
            "SELECT value FROM settings WHERE key='active_profile_id'"
        ).fetchone()
        active_id = int(active_id["value"]) if active_id else 1
        active_profile = next((p for p in profiles if p["id"] == active_id), profiles[0])

    print(f"\n  Active profile: {active_profile['name']} (id={active_id})")

    # Check pending before
    status_before = requests.get("http://localhost:8000/api/sync/status").json()
    pending_before = status_before.get("pending", 0)
    print(f"  Pending before: {pending_before}")

    # Create test transaction
    resp = requests.post("http://localhost:8000/api/transactions", json={
        "date": "2026-06-13",
        "type": "expense",
        "category": "Groceries",
        "description": "E2E test transaction — safe to delete",
        "merchant": "E2E Test",
        "total": 1.00,
    })
    check("create test transaction (HTTP 200)", resp.status_code == 200,
          str(resp.status_code))
    if resp.status_code != 200:
        print(f"    Error: {resp.text}")
    else:
        txn = resp.json()
        txn_id = txn["id"]
        print(f"    Created txn id={txn_id}, sync_status={txn.get('sync_status')}")

        # Check pending increased
        status_mid = requests.get("http://localhost:8000/api/sync/status").json()
        check("pending increased after create",
              status_mid.get("pending", 0) > pending_before,
              f"pending={status_mid.get('pending')}")

        # Trigger sync
        sync_resp = requests.post("http://localhost:8000/api/sync/now")
        check("sync now (HTTP 200)", sync_resp.status_code == 200,
              str(sync_resp.status_code))
        sync_result = sync_resp.json()
        print(f"    Sync result: {sync_result}")

        # Verify pending back to 0
        status_after = requests.get("http://localhost:8000/api/sync/status").json()
        check("pending=0 after sync",
              status_after.get("pending", -1) == 0,
              f"pending={status_after.get('pending')}")
        check("no sync error after sync",
              not status_after.get("last_error"),
              status_after.get("last_error") or "none")

        # Verify transaction in Sheet
        sheet_id = active_profile.get("spreadsheet_id")
        if sheet_id:
            try:
                ss = sheets_svc.spreadsheets().get(
                    spreadsheetId=sheet_id,
                    fields="sheets.properties.title"
                ).execute()
                tabs = [s["properties"]["title"] for s in ss.get("sheets", [])]
                # Look for txn_id in the sheet
                found = False
                for tab in tabs:
                    ids_col = sheets_svc.spreadsheets().values().get(
                        spreadsheetId=sheet_id,
                        range=f"'{tab}'!A2:A"
                    ).execute().get("values", [])
                    if any(row and str(row[0]) == str(txn_id) for row in ids_col):
                        found = True
                        print(f"    Found txn {txn_id} in tab '{tab}'")
                        break
                check(f"test txn {txn_id} appears in sheet", found)
            except Exception as e:
                check("verify txn in sheet", False, str(e))

        # Clean up test transaction
        del_resp = requests.delete(f"http://localhost:8000/api/transactions/{txn_id}")
        check("cleanup test transaction", del_resp.status_code == 200,
              str(del_resp.status_code))
        print(f"    Deleted txn {txn_id}")

    section("Summary")
    if errors:
        print(f"\n  {FAIL} {len(errors)} check(s) failed:")
        for e in errors:
            print(f"    • {e}")
        sys.exit(1)
    else:
        print(f"\n  {PASS} All checks passed — Google Drive sync is working correctly.")
        print(f"  Drive structure: Expense Manager/ → Profile/ → Sheet + Year folders")


if __name__ == "__main__":
    run()
