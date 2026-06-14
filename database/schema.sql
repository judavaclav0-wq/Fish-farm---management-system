-- ============================================================================
-- Growout + PG Management System — Supabase / PostgreSQL schema
-- ============================================================================
--
-- Design principle: key columns for querying + payload JSONB for flexibility.
--
--   • Key columns let you filter and sort without parsing JSON.
--   • payload stores the full original Python dict so the app can read it
--     back as-is without any mapping logic.
--   • Adding a new field to a record never requires an ALTER TABLE.
--
-- Run this file in the Supabase SQL editor:
--   Dashboard → SQL Editor → New query → paste → Run
--
-- Running multiple times is safe: all statements use CREATE TABLE IF NOT EXISTS
-- and CREATE INDEX IF NOT EXISTS.
-- ============================================================================


-- ── Farm state ────────────────────────────────────────────────────────────────
-- Single-row table; the app always reads/writes the row with id = 'default'.

CREATE TABLE IF NOT EXISTS farm_state (
    id         text        PRIMARY KEY DEFAULT 'default',
    payload    jsonb       NOT NULL    DEFAULT '{}',
    updated_at timestamptz             DEFAULT now()
);


-- ── Batches ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS batches (
    id         text        PRIMARY KEY,
    name       text        NOT NULL DEFAULT '',
    species    text                 DEFAULT '',
    status     text        NOT NULL DEFAULT 'active',  -- active | archived
    payload    jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz          DEFAULT now(),
    updated_at timestamptz          DEFAULT now()
);

CREATE INDEX IF NOT EXISTS batches_status_idx ON batches (status);


-- ── Daily logs ────────────────────────────────────────────────────────────────
-- One row per tank per day.
-- The UNIQUE constraint mirrors the upsert behaviour of the JSON backend:
-- (date + system_name + tank_id) is the natural key.

CREATE TABLE IF NOT EXISTS daily_logs (
    id          text        PRIMARY KEY,
    date        date        NOT NULL,
    farm_name   text                 DEFAULT '',
    system_name text        NOT NULL DEFAULT '',
    tank_id     text        NOT NULL DEFAULT '',
    tank_name   text                 DEFAULT '',
    operator    text                 DEFAULT '',
    payload     jsonb       NOT NULL DEFAULT '{}',
    created_at  timestamptz          DEFAULT now(),
    updated_at  timestamptz          DEFAULT now(),

    CONSTRAINT daily_logs_date_system_tank_unique
        UNIQUE (date, system_name, tank_id)
);

CREATE INDEX IF NOT EXISTS daily_logs_date_idx        ON daily_logs (date);
CREATE INDEX IF NOT EXISTS daily_logs_system_name_idx ON daily_logs (system_name);
CREATE INDEX IF NOT EXISTS daily_logs_tank_id_idx     ON daily_logs (tank_id);


-- ── Movements ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS movements (
    id            text        PRIMARY KEY,
    date          date        NOT NULL,
    movement_type text                 DEFAULT '',   -- transfer | split | cull | etc.
    from_tank_id  text                 DEFAULT '',
    to_tank_id    text                 DEFAULT '',
    payload       jsonb       NOT NULL DEFAULT '{}',
    created_at    timestamptz          DEFAULT now(),
    updated_at    timestamptz          DEFAULT now()
);

CREATE INDEX IF NOT EXISTS movements_date_idx           ON movements (date);
CREATE INDEX IF NOT EXISTS movements_movement_type_idx  ON movements (movement_type);
CREATE INDEX IF NOT EXISTS movements_from_tank_id_idx   ON movements (from_tank_id);
CREATE INDEX IF NOT EXISTS movements_to_tank_id_idx     ON movements (to_tank_id);


-- ── Grading logs (histograms) ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS grading_logs (
    id          text        PRIMARY KEY,
    date        date        NOT NULL,
    tank_id     text                 DEFAULT '',
    tank_name   text                 DEFAULT '',
    system_name text                 DEFAULT '',
    payload     jsonb       NOT NULL DEFAULT '{}',
    created_at  timestamptz          DEFAULT now(),
    updated_at  timestamptz          DEFAULT now()
);

CREATE INDEX IF NOT EXISTS grading_logs_date_idx    ON grading_logs (date);
CREATE INDEX IF NOT EXISTS grading_logs_tank_id_idx ON grading_logs (tank_id);


-- ── Activity log ──────────────────────────────────────────────────────────────
-- Append-only audit trail.  Rows are never updated or deleted in normal use.

CREATE TABLE IF NOT EXISTS activity_logs (
    id                text        PRIMARY KEY,
    timestamp         timestamptz NOT NULL DEFAULT now(),
    module            text                 DEFAULT '',
    action            text                 DEFAULT '',   -- create | update | delete | archive | …
    summary           text                 DEFAULT '',
    user_name         text                 DEFAULT '',   -- legacy column (= username); kept for backward compat
    user_id           text                 DEFAULT '',
    username          text                 DEFAULT '',
    user_display_name text                 DEFAULT '',
    user_role         text                 DEFAULT '',
    payload           jsonb       NOT NULL DEFAULT '{}',
    created_at        timestamptz          DEFAULT now()
);

CREATE INDEX IF NOT EXISTS activity_logs_timestamp_idx ON activity_logs (timestamp DESC);
CREATE INDEX IF NOT EXISTS activity_logs_module_idx    ON activity_logs (module);
CREATE INDEX IF NOT EXISTS activity_logs_user_name_idx ON activity_logs (user_name);


-- ── Users ─────────────────────────────────────────────────────────────────────
-- password_hash is stored inside payload only — never in a queryable column.

CREATE TABLE IF NOT EXISTS users (
    id           text        PRIMARY KEY,
    username     text        UNIQUE NOT NULL,
    display_name text                DEFAULT '',
    role         text        NOT NULL DEFAULT 'Viewer',  -- Admin|Manager|Operator|Viewer
    status       text        NOT NULL DEFAULT 'active',  -- active | disabled
    payload      jsonb       NOT NULL DEFAULT '{}',      -- contains password_hash
    created_at   timestamptz          DEFAULT now(),
    updated_at   timestamptz          DEFAULT now()
);

CREATE INDEX IF NOT EXISTS users_username_idx ON users (username);
CREATE INDEX IF NOT EXISTS users_status_idx   ON users (status);


-- ============================================================================
-- Row Level Security (RLS) — recommended for production
-- ============================================================================
--
-- The app uses the SERVICE_ROLE key which bypasses RLS by default.
-- Enabling RLS blocks anonymous/public-key access from the browser.
--
-- Uncomment and run the block below when you are ready to lock down access.

/*
ALTER TABLE farm_state    ENABLE ROW LEVEL SECURITY;
ALTER TABLE batches       ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_logs    ENABLE ROW LEVEL SECURITY;
ALTER TABLE movements     ENABLE ROW LEVEL SECURITY;
ALTER TABLE grading_logs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE users         ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "service_role_all" ON farm_state    FOR ALL USING (true);
CREATE POLICY "service_role_all" ON batches       FOR ALL USING (true);
CREATE POLICY "service_role_all" ON daily_logs    FOR ALL USING (true);
CREATE POLICY "service_role_all" ON movements     FOR ALL USING (true);
CREATE POLICY "service_role_all" ON grading_logs  FOR ALL USING (true);
CREATE POLICY "service_role_all" ON activity_logs FOR ALL USING (true);
CREATE POLICY "service_role_all" ON users         FOR ALL USING (true);
*/
