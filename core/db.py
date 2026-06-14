"""
core/db.py
Supabase client for the Growout + PG Management System.

Usage
-----
Call get_client() anywhere you need a Supabase connection.
The client is a module-level singleton — it is created once and reused.

Required environment variables
-------------------------------
    SUPABASE_URL              — your project URL, e.g. https://xyz.supabase.co
    SUPABASE_SERVICE_ROLE_KEY — service role key (preferred for server-side use)
    SUPABASE_ANON_KEY         — anon/public key (fallback if service role not set)

The service role key bypasses Row Level Security (RLS).  Use it only in
server-side code that users cannot access directly (Streamlit backend).
Never expose it in client-side JavaScript.

Set variables before running the app:

    Windows PowerShell:
        $env:SUPABASE_URL="https://xyz.supabase.co"
        $env:SUPABASE_SERVICE_ROLE_KEY="eyJ..."

    Linux / macOS:
        export SUPABASE_URL=https://xyz.supabase.co
        export SUPABASE_SERVICE_ROLE_KEY=eyJ...

    Streamlit Cloud:
        Add secrets in the dashboard under Settings → Secrets:
        SUPABASE_URL = "https://xyz.supabase.co"
        SUPABASE_SERVICE_ROLE_KEY = "eyJ..."
"""

import os

# Module-level singleton — None until first call to get_client()
_client = None


def get_client():
    """
    Return a configured Supabase client.

    Raises RuntimeError with a human-readable message if:
      - supabase-py is not installed
      - SUPABASE_URL is missing
      - Both key environment variables are missing
    """
    global _client
    if _client is not None:
        return _client

    # ── Read config from environment ──────────────────────────────────────────
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.environ.get("SUPABASE_ANON_KEY", "").strip()
    )

    if not url:
        raise RuntimeError(
            "SUPABASE_URL is not set.\n\n"
            "Set it to your Supabase project URL:\n"
            "  Windows:  $env:SUPABASE_URL='https://xyz.supabase.co'\n"
            "  Linux:    export SUPABASE_URL=https://xyz.supabase.co\n"
            "  Streamlit Cloud: add it under Settings → Secrets"
        )

    if not key:
        raise RuntimeError(
            "Neither SUPABASE_SERVICE_ROLE_KEY nor SUPABASE_ANON_KEY is set.\n\n"
            "Set one of them to your Supabase API key:\n"
            "  Windows:  $env:SUPABASE_SERVICE_ROLE_KEY='eyJ...'\n"
            "  Linux:    export SUPABASE_SERVICE_ROLE_KEY=eyJ..."
        )

    # ── Create client ─────────────────────────────────────────────────────────
    try:
        from supabase import create_client
    except ImportError:
        raise RuntimeError(
            "supabase-py is not installed.  Run:\n"
            "  pip install supabase\n"
            "or add it to requirements.txt"
        )

    _client = create_client(url, key)
    return _client


def reset_client() -> None:
    """
    Clear the cached client.  Useful in tests or when credentials change.
    """
    global _client
    _client = None
