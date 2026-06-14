# Database Migration Plan

## Current State

All data is persisted as local JSON / JSONL files in `growout/data/`:

| File | Format | Content |
|---|---|---|
| `farm_setup.json` | JSON | Farm, systems, tanks |
| `batches.json` | JSON | Fish batches |
| `daily_log.jsonl` | JSONL append-log | Daily per-tank measurements |
| `movements.jsonl` | JSONL append-log | Fish transfer events |
| `grading_logs.jsonl` | JSONL append-log | Grading / histogram events |
| `activity_log.jsonl` | JSONL append-log | Audit trail |
| `data/users.yaml` | YAML | User credentials and roles |

## Storage Abstraction Principle

**The public API of `core/storage.py` must remain stable throughout the migration.**

All modules (`dashboard`, `daily_log`, `movements`, etc.) call only these functions:

```python
# Farm
storage.load_farm() -> dict
storage.save_farm(data: dict) -> None

# Batches
storage.load_batches() -> list[dict]
storage.save_batches(data: list[dict]) -> None

# Daily logs
storage.load_daily_logs() -> list[dict]
storage.save_daily_logs(records: list[dict]) -> None
storage.append_daily_log(record: dict) -> None

# Movements
storage.load_movements() -> list[dict]
storage.save_movements(records: list[dict]) -> None
storage.append_movement(record: dict) -> None

# Grading
storage.load_grading_logs() -> list[dict]
storage.save_grading_logs(records: list[dict]) -> None
storage.append_grading_log(record: dict) -> None

# Activity log
storage.load_activity_logs() -> list[dict]
storage.log_activity(module, action, summary, ...) -> None

# Utilities
storage.new_id(prefix) -> str
```

When the backend switches to a database, only the *implementations* inside
`core/storage.py` change — no module code changes.

---

## Recommended Database Schema

Target: **PostgreSQL** (Supabase, Neon, Railway, or self-hosted).

### `farms`
```sql
CREATE TABLE farms (
    id          TEXT PRIMARY KEY,
    farm_name   TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

### `systems`
```sql
CREATE TABLE systems (
    id          TEXT PRIMARY KEY,
    farm_id     TEXT REFERENCES farms(id),
    name        TEXT NOT NULL,
    thresholds  JSONB DEFAULT '{}'
);
```

### `tanks`
```sql
CREATE TABLE tanks (
    id                        TEXT PRIMARY KEY,
    system_id                 TEXT REFERENCES systems(id),
    farm_id                   TEXT REFERENCES farms(id),
    name                      TEXT NOT NULL,
    fish_count                INTEGER DEFAULT 0,
    avg_weight_g              NUMERIC DEFAULT 0,
    feed_kg_day               NUMERIC DEFAULT 0,
    tank_volume_m3            NUMERIC DEFAULT 10,
    batch_id                  TEXT,
    batch_name                TEXT,
    initial_batch_composition JSONB DEFAULT '[]',
    grading_status            TEXT DEFAULT 'Not graded',
    grading_notes             TEXT DEFAULT ''
);
```

### `batches`
```sql
CREATE TABLE batches (
    id                   TEXT PRIMARY KEY,
    farm_id              TEXT REFERENCES farms(id),
    name                 TEXT NOT NULL,
    species              TEXT,
    start_date           DATE,
    hatch_date           DATE,
    initial_fish_count   INTEGER DEFAULT 0,
    initial_avg_weight_g NUMERIC DEFAULT 0,
    initial_biomass_kg   NUMERIC DEFAULT 0,
    status               TEXT DEFAULT 'active',
    vaccination_schedule JSONB DEFAULT '[]',
    notes                TEXT DEFAULT ''
);
```

### `daily_logs`
```sql
CREATE TABLE daily_logs (
    id               TEXT PRIMARY KEY,
    farm_id          TEXT REFERENCES farms(id),
    date             DATE NOT NULL,
    system_name      TEXT NOT NULL,
    tank_id          TEXT REFERENCES tanks(id),
    tank_name        TEXT,
    operator         TEXT,
    feed_kg          NUMERIC DEFAULT 0,
    oxygen           NUMERIC DEFAULT 0,
    mortality_fish   INTEGER DEFAULT 0,
    bicarbonate_kg   NUMERIC DEFAULT 0,
    treatment_type   TEXT DEFAULT 'None',
    treatment_amount NUMERIC DEFAULT 0,
    treatment_unit   TEXT DEFAULT '',
    treatments       TEXT DEFAULT '',
    notes            TEXT DEFAULT '',
    measurements     JSONB DEFAULT '{}',   -- WQ params (pH, CO2, etc.)
    user_name        TEXT DEFAULT '',
    created_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (date, system_name, tank_id)    -- enforces one row per tank per day
);
```

### `movements`
```sql
CREATE TABLE movements (
    id             TEXT PRIMARY KEY,
    farm_id        TEXT REFERENCES farms(id),
    date           DATE NOT NULL,
    from_tank_id   TEXT REFERENCES tanks(id),
    to_tank_id     TEXT REFERENCES tanks(id),
    fish_count     INTEGER NOT NULL,
    batch_id       TEXT,
    notes          TEXT DEFAULT '',
    user_name      TEXT DEFAULT '',
    created_at     TIMESTAMPTZ DEFAULT now()
);
```

### `grading_logs`
```sql
CREATE TABLE grading_logs (
    id          TEXT PRIMARY KEY,
    farm_id     TEXT REFERENCES farms(id),
    date        DATE NOT NULL,
    tank_id     TEXT REFERENCES tanks(id),
    tank_name   TEXT,
    system_name TEXT,
    data        JSONB DEFAULT '{}',   -- histogram bins, averages, etc.
    user_name   TEXT DEFAULT '',
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

### `activity_logs`
```sql
CREATE TABLE activity_logs (
    id               TEXT PRIMARY KEY,
    timestamp        TIMESTAMPTZ DEFAULT now(),
    module           TEXT NOT NULL,
    action           TEXT NOT NULL,
    summary          TEXT,
    date             DATE,
    system_name      TEXT,
    tank_id          TEXT,
    tank_name        TEXT,
    operator         TEXT,
    details          JSONB DEFAULT '{}',
    user_name        TEXT DEFAULT '',
    user_display_name TEXT DEFAULT '',
    user_role        TEXT DEFAULT ''
);
```

### `users`
```sql
CREATE TABLE users (
    id           TEXT PRIMARY KEY,
    username     TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    email        TEXT,
    password_hash TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'Viewer',
    is_active    BOOLEAN DEFAULT true,
    created_at   TIMESTAMPTZ DEFAULT now()
);
```

---

## Migration Approach

### Phase 1 — Current (JSON/JSONL)
- `core/storage.py` reads/writes local files
- `data/users.yaml` for auth
- Works on a single server; no concurrent writes

### Phase 2 — Supabase / PostgreSQL
1. Provision database (Supabase free tier is sufficient for small farms)
2. Run schema above
3. Write a one-time migration script: `scripts/migrate_json_to_db.py`
   - Reads all JSONL/JSON files
   - Inserts rows into PostgreSQL (upsert by ID)
4. Replace implementations in `core/storage.py` to use `psycopg2` / `supabase-py`
   - All function signatures stay the same
   - Return types stay the same (list[dict], dict)
5. Move auth to Supabase Auth or keep streamlit-authenticator with DB-backed users
6. Keep `data/` folder as a local backup / fallback

### Phase 3 — Multi-farm / SaaS
- Add `farm_id` scoping to all queries (already in schema)
- Add row-level security (RLS) in Supabase per farm
- Separate Streamlit Cloud deployment per farm, or a single app with farm selector

---

## Environment Variables (Phase 2+)

```
DATABASE_URL=postgresql://user:pass@host:5432/growout
SUPABASE_URL=https://xyz.supabase.co
SUPABASE_KEY=eyJ...
SECRET_COOKIE_KEY=<random 32+ char string>
```

Store via Streamlit Secrets (`secrets.toml` or Streamlit Cloud secrets UI), never committed to git.

---

## Notes

- The JSONL append-log pattern maps naturally to an INSERT-only table with a UNIQUE constraint on `(date, system_name, tank_id)` for daily logs — an upsert replaces the entire row.
- `measurements` (WQ params like pH, CO2) are stored as a JSONB column to preserve the dynamic-parameter design without schema changes per parameter.
- `initial_batch_composition` is JSONB to keep the multi-batch list structure intact.
- Activity log rows are never deleted; they are append-only in both JSON and DB.
