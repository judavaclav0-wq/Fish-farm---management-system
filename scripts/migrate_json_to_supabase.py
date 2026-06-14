#!/usr/bin/env python3
"""
scripts/migrate_json_to_supabase.py
Migrate existing local JSON / JSONL data to Supabase.

Usage
-----
    # From the growout/ directory:
    python scripts/migrate_json_to_supabase.py

Required environment variables (set before running):
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY   (or SUPABASE_ANON_KEY)

The script reads local data files and upserts every record into Supabase.
Local files are NOT modified or deleted.
Running the script multiple times is safe (upsert by primary key).

Prerequisites
-------------
1. Create the Supabase tables using database/schema.sql.
2. Install supabase-py:  pip install supabase
"""

import sys
import os
import json
import re
import uuid
from pathlib import Path
from datetime import datetime

# ── Path setup ────────────────────────────────────────────────────────────────
# Allow running from scripts/ or from growout/
_SCRIPT_DIR  = Path(__file__).parent
_PROJECT_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_DIR))

_DATA_DIR = _PROJECT_DIR / "data"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _new_id(prefix: str = "") -> str:
    uid = uuid.uuid4().hex[:8]
    return f"{prefix}{uid}" if prefix else uid


def _stable_dl_id(record: dict) -> str:
    """Deterministic ID for a daily log record (date + system_name + tank_id).

    Matches the logic in core/storage._stable_dl_id so that previously
    migrated records get the same ID on re-runs (idempotent upsert).
    """
    raw = (
        f"dl_{record.get('date', '')}"
        f"_{record.get('system_name', '')}"
        f"_{record.get('tank_id', '')}"
    )
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw)


def _load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            print(f"  WARNING: could not parse {path.name}")
            return default


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  WARNING: skipping malformed line {i} in {path.name}")
    return records


def _upsert_batch(client, table: str, rows: list[dict], batch_size: int = 200) -> int:
    """Upsert *rows* in batches.  Returns total rows sent."""
    total = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        client.table(table).upsert(chunk).execute()
        total += len(chunk)
    return total


# ── Migration functions ───────────────────────────────────────────────────────

def migrate_farm(client) -> int:
    path    = _DATA_DIR / "farm_setup.json"
    payload = _load_json(path, {"farm_name": "", "systems": [], "tanks": []})
    client.table("farm_state").upsert({
        "id":         "default",
        "payload":    payload,
        "updated_at": _now(),
    }).execute()
    print(f"  farm_state    → 1 row")
    return 1


def migrate_batches(client) -> int:
    path    = _DATA_DIR / "batches.json"
    batches = _load_json(path, [])
    if not batches:
        print("  batches       → 0 rows (file empty or missing)")
        return 0
    rows = [
        {
            "id":         b["id"],
            "name":       b.get("name", ""),
            "species":    b.get("species", ""),
            "status":     b.get("status", "active"),
            "payload":    b,
            "updated_at": _now(),
        }
        for b in batches
    ]
    n = _upsert_batch(client, "batches", rows)
    print(f"  batches       → {n} rows")
    return n


def migrate_daily_logs(client) -> int:
    path    = _DATA_DIR / "daily_log.jsonl"
    records = _load_jsonl(path)
    if not records:
        print("  daily_logs    → 0 rows (file empty or missing)")
        return 0
    rows = [
        {
            # Use existing id if present; fall back to stable deterministic id.
            # This matches core/storage._stable_dl_id so re-runs are idempotent.
            "id":          r.get("id") or _stable_dl_id(r),
            "date":        r.get("date", ""),
            "farm_name":   r.get("farm_name", ""),
            "system_name": r.get("system_name", ""),
            "tank_id":     r.get("tank_id", ""),
            "tank_name":   r.get("tank_name", ""),
            "operator":    r.get("operator", ""),
            "payload":     r,
            "updated_at":  _now(),
        }
        for r in records
    ]
    n = _upsert_batch(client, "daily_logs", rows)
    print(f"  daily_logs    → {n} rows")
    return n


def migrate_movements(client) -> int:
    path    = _DATA_DIR / "movements.jsonl"
    records = _load_jsonl(path)
    if not records:
        print("  movements     → 0 rows (file empty or missing)")
        return 0
    rows = [
        {
            "id":            r.get("id", _new_id("mov_")),
            "date":          r.get("date", ""),
            "movement_type": r.get("movement_type", r.get("type", "")),
            "from_tank_id":  r.get("from_tank_id", ""),
            "to_tank_id":    r.get("to_tank_id", ""),
            "payload":       r,
            "updated_at":    _now(),
        }
        for r in records
    ]
    n = _upsert_batch(client, "movements", rows)
    print(f"  movements     → {n} rows")
    return n


def migrate_grading_logs(client) -> int:
    path    = _DATA_DIR / "grading_logs.jsonl"
    records = _load_jsonl(path)
    if not records:
        print("  grading_logs  → 0 rows (file empty or missing)")
        return 0
    rows = [
        {
            "id":          r.get("id", _new_id("grd_")),
            "date":        r.get("date", ""),
            "tank_id":     r.get("tank_id", ""),
            "tank_name":   r.get("tank_name", ""),
            "system_name": r.get("system_name", ""),
            "payload":     r,
            "updated_at":  _now(),
        }
        for r in records
    ]
    n = _upsert_batch(client, "grading_logs", rows)
    print(f"  grading_logs  → {n} rows")
    return n


def migrate_activity_logs(client) -> int:
    path    = _DATA_DIR / "activity_log.jsonl"
    records = _load_jsonl(path)
    if not records:
        print("  activity_logs → 0 rows (file empty or missing)")
        return 0
    rows = [
        {
            "id":        r.get("id", _new_id("act_")),
            "timestamp": r.get("timestamp", _now()),
            "module":    r.get("module", ""),
            "action":    r.get("action", ""),
            "summary":   r.get("summary", ""),
            "user_name": r.get("user_name", ""),
            "payload":   r,
        }
        for r in records
    ]
    n = _upsert_batch(client, "activity_logs", rows)
    print(f"  activity_logs → {n} rows")
    return n


def migrate_users(client) -> int:
    path  = _DATA_DIR / "users.json"
    users = _load_json(path, [])
    if not users:
        print("  users         → 0 rows (file empty or missing)")
        return 0
    rows = [
        {
            "id":           u["id"],
            "username":     u.get("username", ""),
            "display_name": u.get("display_name", ""),
            "role":         u.get("role", "Viewer"),
            "status":       u.get("status", "active"),
            "payload":      u,   # password_hash lives inside payload
            "updated_at":   _now(),
        }
        for u in users
    ]
    n = _upsert_batch(client, "users", rows)
    print(f"  users         → {n} rows")
    return n


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("Growout → Supabase migration")
    print("=" * 60)

    # ── Check environment ──────────────────────────────────────────────────────
    missing = []
    if not os.environ.get("SUPABASE_URL"):
        missing.append("SUPABASE_URL")
    if not (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
    ):
        missing.append("SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY)")

    if missing:
        print("\nERROR: Missing required environment variables:")
        for v in missing:
            print(f"  {v}")
        print("\nSet them before running this script.  See docs/database_migration.md")
        sys.exit(1)

    # ── Connect ────────────────────────────────────────────────────────────────
    print(f"\nConnecting to: {os.environ.get('SUPABASE_URL')}")
    try:
        from core.db import get_client
        client = get_client()
        print("Connected.\n")
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    # ── Run migrations ─────────────────────────────────────────────────────────
    print("Migrating tables:")
    total  = 0
    errors = []

    for name, fn in [
        ("farm",          migrate_farm),
        ("batches",       migrate_batches),
        ("daily_logs",    migrate_daily_logs),
        ("movements",     migrate_movements),
        ("grading_logs",  migrate_grading_logs),
        ("activity_logs", migrate_activity_logs),
        ("users",         migrate_users),
    ]:
        try:
            total += fn(client)
        except Exception as exc:
            errors.append((name, str(exc)))
            print(f"  ERROR in {name}: {exc}")

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"Done.  {total} rows upserted.")
    if errors:
        print(f"\n{len(errors)} table(s) had errors:")
        for name, msg in errors:
            print(f"  {name}: {msg}")
        print("\nCheck the errors above and rerun after fixing.")
    else:
        print("\nAll tables migrated successfully.")
        print("\nNext step:")
        print("  Set APP_STORAGE_BACKEND=supabase and restart the app.")
    print("=" * 60)


if __name__ == "__main__":
    main()
