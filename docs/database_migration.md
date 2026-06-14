# Database Migration Guide — Local JSON → Supabase

This guide walks through migrating the Growout + Pregrow Management System from
local JSON/JSONL file storage to Supabase (PostgreSQL).  The migration is
non-destructive: local files are not modified or deleted.  You can switch back
to JSON storage at any time by changing one environment variable.

---

## Overview

The app supports two storage backends:

| Backend | When to use |
|---------|-------------|
| `json` (default) | Local development, single-user, no database needed |
| `supabase` | Multi-user production, persistent cloud storage, concurrent access |

Backend is controlled by the `APP_STORAGE_BACKEND` environment variable.

---

## Step 1 — Create a Supabase project

1. Go to [https://supabase.com](https://supabase.com) and sign in or create an account.
2. Click **New project**.
3. Choose an organization, set a project name (e.g. `growout-farm`), set a
   database password, and choose a region close to your users.
4. Wait for the project to be provisioned (about 1 minute).

---

## Step 2 — Run the schema

1. In the Supabase dashboard, open **SQL Editor** (left sidebar).
2. Click **New query**.
3. Open `database/schema.sql` from this project and paste the entire contents
   into the editor.
4. Click **Run** (or press Cmd/Ctrl + Enter).
5. Verify the tables were created: go to **Table Editor** and confirm you see:
   - `farm_state`
   - `batches`
   - `daily_logs`
   - `movements`
   - `grading_logs`
   - `activity_logs`
   - `users`

Running the schema a second time is safe — all statements use `CREATE TABLE IF NOT EXISTS`.

---

## Step 3 — Get your API credentials

In the Supabase dashboard, go to **Settings → API**.

You need two values:

| Value | Where to find it |
|-------|-----------------|
| `SUPABASE_URL` | "Project URL" — looks like `https://abcdefgh.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | "Project API keys" → `service_role` key |

> **Security note:** the `service_role` key bypasses Row Level Security (RLS)
> and has full database access.  Keep it secret.  Never commit it to git or
> share it publicly.  It is safe to use from a server-side Streamlit app that
> users cannot directly inspect.

---

## Step 4 — Install the Supabase Python package

```bash
pip install supabase
```

Or add it to your virtual environment after updating `requirements.txt` (already
done — see the `supabase` entry) and run:

```bash
pip install -r requirements.txt
```

---

## Step 5 — Set environment variables

Set the variables in your shell before running the migration script or the app.

**Windows PowerShell:**
```powershell
$env:SUPABASE_URL = "https://abcdefgh.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY = "eyJ..."
```

**Linux / macOS:**
```bash
export SUPABASE_URL="https://abcdefgh.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="eyJ..."
```

**Streamlit Cloud (recommended for production):**
In the app dashboard, go to **Settings → Secrets** and add:
```toml
SUPABASE_URL = "https://abcdefgh.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "eyJ..."
```

---

## Step 6 — Run the migration script

From the `growout/` directory:

```bash
python scripts/migrate_json_to_supabase.py
```

The script will:

1. Read each local data file (`data/*.json`, `data/*.jsonl`).
2. Upsert every record into the corresponding Supabase table.
3. Print a summary of migrated rows.
4. **Not** delete or modify local files.

Running the script multiple times is safe — it uses upsert (insert or update by
primary key), so re-running after a partial failure just fills in the gaps.

Example output:
```
============================================================
Growout → Supabase migration
============================================================

Connecting to: https://abcdefgh.supabase.co
Connected.

Migrating tables:
  farm_state   → 1 row (farm_setup.json)
  batches      → 12 rows
  daily_logs   → 348 rows
  movements    → 47 rows
  grading_logs → 8 rows
  activity_logs → 201 rows
  users        → 3 rows

============================================================
Done.  620 rows upserted.

All tables migrated successfully.

Next step:
  Set APP_STORAGE_BACKEND=supabase and restart the app.
============================================================
```

---

## Step 7 — Switch the app to Supabase

Set `APP_STORAGE_BACKEND=supabase` **in addition to** the two Supabase
credentials from Step 5:

**Windows PowerShell:**
```powershell
$env:APP_STORAGE_BACKEND = "supabase"
$env:SUPABASE_URL = "https://abcdefgh.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY = "eyJ..."
streamlit run app.py
```

**Linux / macOS:**
```bash
APP_STORAGE_BACKEND=supabase \
SUPABASE_URL=https://abcdefgh.supabase.co \
SUPABASE_SERVICE_ROLE_KEY=eyJ... \
streamlit run app.py
```

**Streamlit Cloud:** add `APP_STORAGE_BACKEND = "supabase"` to Settings → Secrets
alongside the URL and key.

Restart the app after changing the variable.  There is no warm-up step —
the app reads from Supabase on the first request.

---

## Switching back to JSON

Set `APP_STORAGE_BACKEND=json` (or unset the variable entirely) and restart:

**Windows PowerShell:**
```powershell
$env:APP_STORAGE_BACKEND = "json"
streamlit run app.py
```

**Linux / macOS:**
```bash
APP_STORAGE_BACKEND=json streamlit run app.py
```

The app will read from local `data/` files again.  Any changes made while on
Supabase will **not** be reflected in local files.  If you need to sync changes
back, export them from Supabase (Table Editor → Export CSV) or write a custom
script.

---

## Verifying the migration

After switching to Supabase, open the app and check:

- [ ] Dashboard loads with correct farm/tank data
- [ ] Batch Management shows all active batches
- [ ] Daily Log history shows previous reports
- [ ] Movements history loads
- [ ] Activity Log shows previous entries
- [ ] User Management shows all users (Admin only)
- [ ] Login works for all active users

In the Supabase Table Editor you can browse each table to spot-check row counts
and content.

---

## Troubleshooting

**"SUPABASE_URL is not set"** — Set the environment variable before running
the migration script or the app.  See Step 5.

**"supabase-py is not installed"** — Run `pip install supabase`.  See Step 4.

**Foreign key or constraint errors in schema.sql** — The schema uses
`CREATE TABLE IF NOT EXISTS`, so this usually means a table already exists
with a conflicting definition.  Drop the table in the SQL editor and re-run the
schema:
```sql
DROP TABLE IF EXISTS <table_name>;
```

**Migration script partially succeeds** — Rerun it.  Upsert is idempotent;
successfully migrated rows will be overwritten with the same data.

**Duplicate key errors on daily_logs** — The `UNIQUE(date, system_name, tank_id)`
constraint is enforced.  If local JSONL data has duplicate entries for the same
date+system+tank (shouldn't happen normally), the migration will fail for that
table.  Inspect the file for duplicates and keep only the latest entry per key.

---

## Row Level Security (RLS)

The schema includes commented-out RLS policies.  RLS is **optional for Phase 1**
because the app uses the service role key which bypasses RLS by default.

To enable RLS (recommended before exposing the project to a public URL):
1. Uncomment the RLS section at the bottom of `database/schema.sql`.
2. Re-run those statements in the SQL editor.

With RLS enabled, the `service_role` key still has full access (the policies use
`FOR ALL USING (true)` so all rows are visible to the service role).  The
difference is that the `anon` key (which any browser could obtain) would be
blocked from reading data.
