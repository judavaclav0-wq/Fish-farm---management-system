"""
core/storage.py
Persistence layer for the Growout + PG Management System.

Backend selection
-----------------
Set the environment variable APP_STORAGE_BACKEND to choose where data lives:

    APP_STORAGE_BACKEND=json       (default — local JSON/JSONL files)
    APP_STORAGE_BACKEND=supabase   (PostgreSQL via Supabase)

When no variable is set the app runs exactly as before using local files.

Public API is identical for both backends.  No module outside core/ needs
to know which backend is active.

Supabase data model (see database/schema.sql)
---------------------------------------------
Every table has key columns for querying + a `payload jsonb` column that
stores the full original record.  This means:
  • All existing fields are preserved without schema changes.
  • Queries on key columns are fast (indexed).
  • Adding new fields to a record never requires a schema migration.

Supabase save strategy
----------------------
All "save_*" functions (not append_*) must replicate the JSON behaviour of
rewriting the whole file.  Rather than delete-all + insert (which opens an
empty-table window), we use: upsert all → delete rows not in the new list.
This is safe for typical farm-scale record counts (< 10 000 rows per table).

Daily log IDs
-------------
JSONL daily-log records created before this Supabase backend was added may
lack an `id` field.  We derive a stable deterministic ID from the natural
key (date + system_name + tank_id) so repeated saves always map to the same
Supabase row without collisions.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# ID generation  (defined early — referenced by Supabase helpers below)
# ---------------------------------------------------------------------------

def new_id(prefix: str = "") -> str:
    """Generate a short random hex ID, optionally prefixed."""
    uid = uuid.uuid4().hex[:8]
    return f"{prefix}{uid}" if prefix else uid


# ---------------------------------------------------------------------------
# Backend selector
# ---------------------------------------------------------------------------

def _use_supabase() -> bool:
    """Return True when the Supabase backend is active."""
    return os.environ.get("APP_STORAGE_BACKEND", "json").lower().strip() == "supabase"


def _get_backend() -> str:
    """Return 'supabase' or 'json'.  Kept for internal clarity."""
    return "supabase" if _use_supabase() else "json"


# ---------------------------------------------------------------------------
# Local-file helpers  (JSON / JSONL)
# ---------------------------------------------------------------------------

_BASE = Path(__file__).parent.parent / "data"


def _ensure_dir() -> None:
    _BASE.mkdir(parents=True, exist_ok=True)


def _path(filename: str) -> Path:
    _ensure_dir()
    return _BASE / filename


def load_json(filename: str, default: Any = None) -> Any:
    """Load a JSON file.  Returns *default* if the file does not exist."""
    p = _path(filename)
    if not p.exists():
        return default if default is not None else {}
    with open(p, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default if default is not None else {}


def save_json(filename: str, data: Any) -> None:
    """Overwrite a JSON file with *data*."""
    with open(_path(filename), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def load_jsonl(filename: str) -> list[dict]:
    """Load all records from a JSONL file."""
    p = _path(filename)
    if not p.exists():
        return []
    records = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def append_jsonl(filename: str, record: dict) -> None:
    """Append a single record to a JSONL file."""
    with open(_path(filename), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def save_jsonl(filename: str, records: list[dict]) -> None:
    """Overwrite a JSONL file with *records* (used when editing/deleting entries)."""
    with open(_path(filename), "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# Supabase client shortcut
# ---------------------------------------------------------------------------

def _sb():
    """Return the configured Supabase client.  Raises RuntimeError if not set up."""
    from core.db import get_client
    return get_client()


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Supabase low-level helpers
# ---------------------------------------------------------------------------

def _sb_upsert_batch(table: str, rows: list[dict], batch_size: int = 500) -> None:
    """Upsert *rows* into *table* in batches to stay under the REST API size limit."""
    client = _sb()
    for i in range(0, len(rows), batch_size):
        client.table(table).upsert(rows[i : i + batch_size]).execute()


def _sb_delete_by_ids(table: str, ids: list[str], batch_size: int = 100) -> None:
    """Delete specific rows by primary-key list."""
    client = _sb()
    for i in range(0, len(ids), batch_size):
        client.table(table).delete().in_("id", ids[i : i + batch_size]).execute()


def _sb_delete_all(table: str) -> None:
    """Delete every row from *table* (used when the replacement list is empty)."""
    # neq("id", "") satisfies supabase-py's filter requirement while matching all rows.
    _sb().table(table).delete().neq("id", "").execute()


def _sb_get_existing_ids(table: str) -> set[str]:
    """Return the set of all `id` values currently in *table*."""
    result = _sb().table(table).select("id").execute()
    return {row["id"] for row in (result.data or [])}


def _stable_dl_id(record: dict) -> str:
    """Deterministic daily-log ID derived from its natural key (date + system + tank).

    JSONL records written before this backend was added have no `id` field.
    Using a stable ID ensures the same record always maps to the same Supabase
    row so repeated calls to save_daily_logs are idempotent.
    """
    raw = (
        f"dl_{record.get('date', '')}"
        f"_{record.get('system_name', '')}"
        f"_{record.get('tank_id', '')}"
    )
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw)


def _dl_row(r: dict) -> dict:
    """Build a daily_logs table row from a daily log record dict."""
    return {
        "id":          r.get("id") or _stable_dl_id(r),
        "date":        r.get("date", ""),
        "farm_name":   r.get("farm_name", ""),
        "system_name": r.get("system_name", ""),
        "tank_id":     r.get("tank_id", ""),
        "tank_name":   r.get("tank_name", ""),
        "operator":    r.get("operator", ""),
        "payload":     r,
        "updated_at":  _now_iso(),
    }


def _mov_row(r: dict) -> dict:
    """Build a movements table row from a movement record dict."""
    return {
        "id":            r.get("id", new_id("mov_")),
        "date":          r.get("date", ""),
        "movement_type": r.get("movement_type", r.get("type", "")),
        "from_tank_id":  r.get("from_tank_id", ""),
        "to_tank_id":    r.get("to_tank_id", ""),
        "payload":       r,
        "updated_at":    _now_iso(),
    }


def _grd_row(r: dict) -> dict:
    """Build a grading_logs table row from a grading record dict."""
    return {
        "id":          r.get("id", new_id("grd_")),
        "date":        r.get("date", ""),
        "tank_id":     r.get("tank_id", ""),
        "tank_name":   r.get("tank_name", ""),
        "system_name": r.get("system_name", ""),
        "payload":     r,
        "updated_at":  _now_iso(),
    }


def _adj_row(r: dict) -> dict:
    """Build a stock_adjustments table row from a count-reconciliation record."""
    return {
        "id":          r.get("id", new_id("adj_")),
        "date":        r.get("date", ""),
        "tank_id":     r.get("tank_id", ""),
        "tank_name":   r.get("tank_name", ""),
        "system_name": r.get("system_name", ""),
        "payload":     r,
        "updated_at":  _now_iso(),
    }


def _sb_load_adjustments() -> list[dict]:
    result = _sb().table("stock_adjustments").select("payload").order("date").execute()
    return [row["payload"] for row in (result.data or [])]


def _sb_append_adjustment(record: dict) -> None:
    _sb().table("stock_adjustments").upsert(_adj_row(record)).execute()


def _sb_save_adjustments(records: list[dict]) -> None:
    _sb_replace("stock_adjustments", [_adj_row(r) for r in records])


def _sb_replace(table: str, rows: list[dict]) -> None:
    """Full-replacement upsert for any table that supports it.

    Strategy:
      1. Upsert all rows in the new list  (no empty-table window).
      2. Fetch existing IDs and delete any not in the new list.

    This matches the semantics of overwriting a JSON file: after the call,
    the table contains exactly the records that were passed in.
    """
    if not rows:
        _sb_delete_all(table)
        return
    _sb_upsert_batch(table, rows)
    new_ids = {row["id"] for row in rows}
    existing_ids = _sb_get_existing_ids(table)
    ids_to_delete = list(existing_ids - new_ids)
    if ids_to_delete:
        _sb_delete_by_ids(table, ids_to_delete)


# ---------------------------------------------------------------------------
# Supabase: farm state
# ---------------------------------------------------------------------------

def _sb_load_farm() -> dict:
    result = _sb().table("farm_state").select("payload").eq("id", "default").execute()
    if result.data:
        return result.data[0]["payload"]
    return {"farm_name": "", "systems": [], "tanks": []}


def _sb_save_farm(data: dict) -> None:
    _sb().table("farm_state").upsert({
        "id":         "default",
        "payload":    data,
        "updated_at": _now_iso(),
    }).execute()


# ---------------------------------------------------------------------------
# Supabase: batches
# ---------------------------------------------------------------------------

def _sb_load_batches() -> list[dict]:
    result = _sb().table("batches").select("payload").execute()
    return [row["payload"] for row in (result.data or [])]


def _sb_save_batches(batches: list[dict]) -> None:
    rows = [
        {
            "id":         b["id"],
            "name":       b.get("name", ""),
            "species":    b.get("species", ""),
            "status":     b.get("status", "active"),
            "payload":    b,
            "updated_at": _now_iso(),
        }
        for b in batches
    ]
    _sb_replace("batches", rows)


def _sb_append_batch(batch: dict) -> None:
    _sb().table("batches").upsert({
        "id":         batch["id"],
        "name":       batch.get("name", ""),
        "species":    batch.get("species", ""),
        "status":     batch.get("status", "active"),
        "payload":    batch,
        "updated_at": _now_iso(),
    }).execute()


# ---------------------------------------------------------------------------
# Supabase: daily logs
# ---------------------------------------------------------------------------

def _sb_load_daily_logs() -> list[dict]:
    result = _sb().table("daily_logs").select("payload").order("date").execute()
    return [row["payload"] for row in (result.data or [])]


def _sb_append_daily_log(record: dict) -> None:
    _sb().table("daily_logs").upsert(_dl_row(record)).execute()


def _sb_save_daily_logs(records: list[dict]) -> None:
    # Full replacement: mirrors the JSON behavior of rewriting the whole file.
    # Stable IDs (_stable_dl_id) make the upsert idempotent for old JSONL
    # records that were created before this backend and have no `id` field.
    _sb_replace("daily_logs", [_dl_row(r) for r in records])


# ---------------------------------------------------------------------------
# Supabase: movements
# ---------------------------------------------------------------------------

def _sb_load_movements() -> list[dict]:
    result = _sb().table("movements").select("payload").order("date").execute()
    return [row["payload"] for row in (result.data or [])]


def _sb_append_movement(record: dict) -> None:
    _sb().table("movements").upsert(_mov_row(record)).execute()


def _sb_save_movements(records: list[dict]) -> None:
    _sb_replace("movements", [_mov_row(r) for r in records])


# ---------------------------------------------------------------------------
# Supabase: grading logs (histograms)
# ---------------------------------------------------------------------------

def _sb_load_grading_logs() -> list[dict]:
    result = _sb().table("grading_logs").select("payload").order("date").execute()
    return [row["payload"] for row in (result.data or [])]


def _sb_append_grading_log(record: dict) -> None:
    _sb().table("grading_logs").upsert(_grd_row(record)).execute()


def _sb_save_grading_logs(records: list[dict]) -> None:
    _sb_replace("grading_logs", [_grd_row(r) for r in records])


# ---------------------------------------------------------------------------
# Supabase: activity log
# ---------------------------------------------------------------------------

def _sb_load_activity_logs() -> list[dict]:
    result = (
        _sb()
        .table("activity_logs")
        .select("payload")
        .order("timestamp", desc=True)
        .execute()
    )
    return [row["payload"] for row in (result.data or [])]


def _act_row(r: dict) -> dict:
    """Build an activity_logs table row from an activity record dict."""
    return {
        "id":                r.get("id", new_id("act_")),
        "timestamp":         r.get("timestamp", _now_iso()),
        "module":            r.get("module", ""),
        "action":            r.get("action", ""),
        "summary":           r.get("summary", ""),
        # user_name: legacy column kept for backward compat with old DB rows
        "user_name":         r.get("username", r.get("user_name", "")),
        "user_id":           r.get("user_id", ""),
        "username":          r.get("username", r.get("user_name", "")),
        "user_display_name": r.get("user_display_name", ""),
        "user_role":         r.get("user_role", ""),
        "payload":           r,
    }


def _sb_append_activity_log(record: dict) -> None:
    # Activity logs are append-only: use insert so we never silently overwrite
    # an existing entry.  id collisions are essentially impossible with new_id().
    _sb().table("activity_logs").insert(_act_row(record)).execute()


def _sb_save_activity_logs(records: list[dict]) -> None:
    # Safety-net bulk write (e.g. restore from backup).  Uses upsert only —
    # never deletes rows — to preserve the append-only guarantee.
    if not records:
        return
    _sb_upsert_batch("activity_logs", [_act_row(r) for r in records])


# ---------------------------------------------------------------------------
# Supabase: users
# ---------------------------------------------------------------------------

def _sb_load_users() -> list[dict]:
    result = _sb().table("users").select("payload").execute()
    return [row["payload"] for row in (result.data or [])]


def _sb_save_users(users: list[dict]) -> None:
    rows = [
        {
            "id":           u["id"],
            "username":     u.get("username", ""),
            "display_name": u.get("display_name", ""),
            "role":         u.get("role", "Viewer"),
            "status":       u.get("status", "active"),
            "payload":      u,   # password_hash lives inside payload only
            "updated_at":   _now_iso(),
        }
        for u in users
    ]
    _sb_replace("users", rows)


def _sb_get_user_by_username(username: str) -> dict | None:
    result = (
        _sb()
        .table("users")
        .select("payload")
        .eq("username", username)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["payload"]
    return None


# ===========================================================================
# Public API — identical signatures for both backends
# ===========================================================================

# ── Farm ──────────────────────────────────────────────────────────────────────

def load_farm() -> dict:
    if _use_supabase():
        return _sb_load_farm()
    return load_json("farm_setup.json", {"farm_name": "", "systems": [], "tanks": []})


def save_farm(data: dict) -> None:
    if _use_supabase():
        _sb_save_farm(data)
        return
    save_json("farm_setup.json", data)


# ── Batches ───────────────────────────────────────────────────────────────────

def load_batches() -> list[dict]:
    if _use_supabase():
        return _sb_load_batches()
    return load_json("batches.json", [])


def save_batches(data: list[dict]) -> None:
    if _use_supabase():
        _sb_save_batches(data)
        return
    save_json("batches.json", data)


def append_batch(batch: dict) -> None:
    if _use_supabase():
        _sb_append_batch(batch)
        return
    batches = load_batches()
    batches.append(batch)
    save_json("batches.json", batches)


# ── Daily logs ────────────────────────────────────────────────────────────────

def load_daily_logs() -> list[dict]:
    if _use_supabase():
        return _sb_load_daily_logs()
    return load_jsonl("daily_log.jsonl")


def append_daily_log(record: dict) -> None:
    if _use_supabase():
        _sb_append_daily_log(record)
        return
    append_jsonl("daily_log.jsonl", record)


def save_daily_logs(records: list[dict]) -> None:
    if _use_supabase():
        _sb_save_daily_logs(records)
        return
    save_jsonl("daily_log.jsonl", records)


# ── Movements ─────────────────────────────────────────────────────────────────

def load_movements() -> list[dict]:
    if _use_supabase():
        return _sb_load_movements()
    return load_jsonl("movements.jsonl")


def append_movement(record: dict) -> None:
    if _use_supabase():
        _sb_append_movement(record)
        return
    append_jsonl("movements.jsonl", record)


def save_movements(records: list[dict]) -> None:
    if _use_supabase():
        _sb_save_movements(records)
        return
    save_jsonl("movements.jsonl", records)


# ── Stock adjustments (count reconciliation) ──────────────────────────────────

def load_adjustments() -> list[dict]:
    """Return all count-reconciliation adjustment records."""
    if _use_supabase():
        return _sb_load_adjustments()
    return load_jsonl("adjustments.jsonl")


def append_adjustment(record: dict) -> None:
    if _use_supabase():
        _sb_append_adjustment(record)
        return
    append_jsonl("adjustments.jsonl", record)


def save_adjustments(records: list[dict]) -> None:
    if _use_supabase():
        _sb_save_adjustments(records)
        return
    save_jsonl("adjustments.jsonl", records)


# ── Grading / histograms ──────────────────────────────────────────────────────

def load_grading_logs() -> list[dict]:
    if _use_supabase():
        return _sb_load_grading_logs()
    return load_jsonl("grading_logs.jsonl")


def append_grading_log(record: dict) -> None:
    if _use_supabase():
        _sb_append_grading_log(record)
        return
    append_jsonl("grading_logs.jsonl", record)


def save_grading_logs(records: list[dict]) -> None:
    if _use_supabase():
        _sb_save_grading_logs(records)
        return
    save_jsonl("grading_logs.jsonl", records)


# ── Activity log ──────────────────────────────────────────────────────────────

def load_activity_logs() -> list[dict]:
    if _use_supabase():
        return _sb_load_activity_logs()
    return load_jsonl("activity_log.jsonl")


def append_activity_log(record: dict) -> None:
    if _use_supabase():
        _sb_append_activity_log(record)
        return
    append_jsonl("activity_log.jsonl", record)


def save_activity_logs(records: list[dict]) -> None:
    if _use_supabase():
        _sb_save_activity_logs(records)
        return
    save_jsonl("activity_log.jsonl", records)


def log_activity(
    module: str,
    action: str,
    summary: str,
    date: str = "",
    system_name: str = "",
    tank_id: str = "",
    tank_name: str = "",
    operator: str = "",
    details: dict | None = None,
) -> None:
    """Build and append one activity record.

    User fields are pulled from st.session_state automatically.
    Fails gracefully when called outside a Streamlit session (tests, scripts).
    """
    try:
        import streamlit as st
        current_user: dict = st.session_state.get("current_user", {})
    except Exception:
        current_user = {}

    record = {
        "id":                new_id("act_"),
        "timestamp":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "module":            module,
        "action":            action,
        "date":              date or "",
        "system_name":       system_name,
        "tank_id":           tank_id,
        "tank_name":         tank_name,
        "operator":          operator,
        "summary":           summary,
        "details":           details or {},
        "user_id":           current_user.get("id", ""),
        "username":          current_user.get("username", ""),
        "user_display_name": current_user.get("display_name", ""),
        "user_role":         current_user.get("role", ""),
    }
    append_activity_log(record)


# ── Users ─────────────────────────────────────────────────────────────────────

def load_users() -> list[dict]:
    if _use_supabase():
        return _sb_load_users()
    return load_json("users.json", [])


def save_users(users: list[dict]) -> None:
    if _use_supabase():
        _sb_save_users(users)
        return
    save_json("users.json", users)


def get_user_by_username(username: str) -> dict | None:
    if _use_supabase():
        return _sb_get_user_by_username(username)
    return next(
        (u for u in load_json("users.json", []) if u.get("username") == username),
        None,
    )


def delete_user(user_id: str) -> None:
    """Permanently remove a user record by id.

    For the Supabase backend this issues a targeted DELETE on the `users` table
    row whose primary key matches *user_id*.  For the JSON backend the record
    is filtered out of users.json and the file is rewritten.

    The caller is responsible for invalidating the auth cache afterwards.
    """
    if _use_supabase():
        _sb_delete_by_ids("users", [user_id])
        return
    users = load_json("users.json", [])
    save_json("users.json", [u for u in users if u.get("id") != user_id])
