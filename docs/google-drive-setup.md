# Google Drive & Sheets Setup

Expense Manager can mirror your transactions to a Google Sheet and store receipt images in Google Drive. This is **completely optional** — the app works fine without it.

When connected, every transaction write is automatically pushed to Google within a few seconds. The sync is one-way: the app never reads from Google, so deleting the sheet or folder loses nothing.

## What you need

- A Google account (personal Gmail is fine)
- About 10 minutes

## Step-by-step

### 1 — Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com).
2. Click the project dropdown at the top → **New Project**.
3. Give it any name (e.g. "Expense Manager") → **Create**.

### 2 — Enable Google Drive API

1. In the left menu: **APIs & Services → Library**.
2. Search for **Google Drive API** → click it → **Enable**.

### 3 — Enable Google Sheets API

Same steps as above, but search for **Google Sheets API**.

> Both APIs must be enabled. Enabling only Drive will cause Sheets sync to fail with a `SERVICE_DISABLED` error.

### 4 — Configure the OAuth consent screen

1. **APIs & Services → OAuth consent screen**.
2. User type: **External** → **Create**.
   - *Why External?* Personal Gmail accounts are not part of a Google Workspace organization, so "Internal" is unavailable or incorrect.
3. Fill in **App name** and **User support email** (your Gmail). Scroll to **Developer contact information** → your email → **Save and Continue**.
4. On the **Scopes** step: click **Save and Continue** (no scopes to add here).
5. On the **Test users** step: click **Add users** → enter your Gmail address → **Save and Continue**.
   - The app stays in "Testing" mode. That is fine — you only need to access it yourself.

### 5 — Create OAuth credentials

1. **APIs & Services → Credentials → Create credentials → OAuth 2.0 Client ID**.
2. Application type: **Desktop app**.
   - *Why Desktop app?* Desktop app credentials allow any `localhost` redirect URI without pre-registration, which means you don't have to copy-paste URLs between Google Console and the app settings.
3. Name it anything → **Create**.
4. Click **Download JSON** on the confirmation dialog (or click the download icon next to the credential later).

### 6 — Connect in the app

1. Open **Settings → Google sync**.
2. The **JSON key** tab is selected by default.
3. Paste the full contents of the downloaded JSON file into the text area.
4. Click **Connect Google** — you will be redirected to Google's sign-in page.
5. Sign in with the Gmail address you added as a test user in step 4.
6. On the consent screen: Google may show a warning ("unverified app") — click **Continue**.
7. After authorizing, you are redirected back to Settings. You should see **✅ Connected**.

## What gets synced

All synced data lives under **one root folder** in your Drive:

```
Expense Manager/               ← app root (customisable name)
  Personal/                    ← one subfolder per profile
    Expense Manager — Personal ← Google Sheet (lives inside the profile folder)
    2025/                      ← receipts organised by year
    2026/
  Incorporation/
    Expense Manager — Incorporation
    2026/
```

The sheet has one tab per calendar year (`2025`, `2026`, …), current year always on the left.

**Tax columns**: instead of a single `Taxes` column, each tax component gets its own column (e.g. `GST`, `QST`, `HST`). The set of columns is determined dynamically per profile from its active tax profile and any component seen in its transactions. When the column set changes (e.g. you add a new tax component), the entire tab is rewritten on the next reconcile.

The app only accesses files it creates (`drive.file` scope). Your existing Google Drive files are never touched.

## App folder name

Under **Settings → Google sync → App folder name** you can change the root folder name from "Expense Manager" to anything you prefer. The rest of the layout (profile subfolder → sheet + year folders) stays the same.

## Reconnecting / revoking

- To reconnect (e.g. after changing credentials): click **Reconnect Google** in Settings.
- To revoke access: go to [myaccount.google.com/permissions](https://myaccount.google.com/permissions) and remove the app.

## Troubleshooting

### `Error 403: org_internal`
Your OAuth consent screen is set to **Internal** user type. Change it to **External** (step 4 above).

### `Something went wrong` at the callback URL
Check the API logs (`make logs-api`). Common causes:
- **Scope warning raised as error**: fixed in current version (removed `include_granted_scopes` from auth URL).
- **Code already used**: authorization codes expire after one use. Start the flow again from Settings.

### Sync fails with `SERVICE_DISABLED`
The Google Sheets API is not enabled. Go to **APIs & Services → Library → Google Sheets API → Enable** (step 3 above).

### `Invalid grant` / `Missing code verifier`
The PKCE verifier was not persisted correctly between the auth URL and callback. This is fixed in the current version. If it recurs, clear your browser cache and start the OAuth flow fresh.

### Sheet not updating after sync
- Check **Settings → Google sync** for a `Last sync error` message.
- The sync is debounced by 2 seconds — wait a moment and check `GET /api/sync/status`.
- Click **Sync now** to force an immediate reconcile.

### Duplicate sheets or orphan Drive files
If you disconnected and reconnected Google, or changed the app folder name and changed it back, you may end up with duplicate spreadsheets or stale receipt files in Drive. The app avoids creating duplicates by reusing an existing same-named sheet before creating a new one, but pre-existing duplicates from earlier versions may persist.

To clean them up, use the maintenance script:

```bash
# Dry run — shows what would be trashed (safe, no changes made)
cd api && DATA_DIR=../data poetry run python tools/drive_cleanup.py

# Actually move duplicates/orphans to Drive Trash (recoverable via drive.google.com)
cd api && DATA_DIR=../data poetry run python tools/drive_cleanup.py --delete
```

Items moved to Trash can be restored from [drive.google.com/drive/trash](https://drive.google.com/drive/trash) within 30 days.
