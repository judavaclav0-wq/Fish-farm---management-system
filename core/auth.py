"""
core/auth.py
Authentication and user-context helpers for the Growout + PG Management System.

User store
----------
Source of truth is data/users.json — a plain JSON list of user records:
    { id, username, display_name, email, password_hash, role, status,
      created_at, last_updated_at }

data/users.yaml keeps only the cookie configuration (name, key, expiry_days).
On first run, existing users.yaml credentials are migrated automatically.

Roles: Admin | Manager | Operator | Viewer
Status: active | disabled   — disabled users cannot log in.
"""

from __future__ import annotations

import streamlit as st
import yaml
import bcrypt
from pathlib import Path
from datetime import datetime

import streamlit_authenticator as stauth

# ── Paths ──────────────────────────────────────────────────────────────────────

_DATA_DIR  = Path(__file__).parent.parent / "data"
_YAML_FILE = _DATA_DIR / "users.yaml"   # cookie config only


# ── Role / page definitions ────────────────────────────────────────────────────

ROLES = ["Admin", "Manager", "Operator", "Viewer"]

# Pages visible to each role
PAGE_ACCESS: dict[str, list[str]] = {
    "Dashboard":        ["Admin", "Manager", "Operator", "Viewer"],
    "Farm Setup":       ["Admin", "Manager"],
    "Batch Management": ["Admin", "Manager", "Viewer"],
    "Daily Log":        ["Admin", "Manager", "Operator"],
    "Movements":        ["Admin", "Manager", "Operator"],
    "Histograms":       ["Admin", "Manager", "Operator"],
    "Activity Log":     ["Admin", "Manager"],
    "User Management":  ["Admin"],
}

# Pages where a role can make writes
WRITE_ACCESS: dict[str, list[str]] = {
    "Dashboard":        ["Admin", "Manager", "Operator"],
    "Farm Setup":       ["Admin", "Manager"],
    "Batch Management": ["Admin", "Manager"],
    "Daily Log":        ["Admin", "Manager", "Operator"],
    "Movements":        ["Admin", "Manager", "Operator"],
    "Histograms":       ["Admin", "Manager", "Operator"],
    "Activity Log":     ["Admin"],
    "User Management":  ["Admin"],
}


# ── Password hashing ───────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*. Safe to store in users.json."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


# ── Cookie config ──────────────────────────────────────────────────────────────

_DEFAULT_COOKIE = {
    "name":         "growout_session",
    "key":          "CHANGE_ME_IN_PRODUCTION_SECRET_KEY_32",
    "expiry_days":  1,
}


def _load_cookie_config() -> dict:
    """Return cookie settings from users.yaml, or fall back to defaults."""
    if _YAML_FILE.exists():
        try:
            with open(_YAML_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            if "cookie" in cfg:
                return cfg["cookie"]
        except Exception:
            pass
    return _DEFAULT_COOKIE.copy()


def _save_cookie_config(cookie_cfg: dict) -> None:
    """Persist cookie config to users.yaml."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_YAML_FILE, "w", encoding="utf-8") as f:
        yaml.dump({"cookie": cookie_cfg}, f, default_flow_style=False)


# ── Default / migration user setup ────────────────────────────────────────────

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _create_default_users() -> list[dict]:
    """
    Return four seeded users with bcrypt-hashed passwords.
    Default passwords — change immediately in production:
        admin    → admin123
        manager  → manager123
        operator → operator123
        viewer   → viewer123
    """
    from core import storage
    defaults = [
        ("admin",    "Admin User",     "admin123",    "Admin"),
        ("manager",  "Farm Manager",   "manager123",  "Manager"),
        ("operator", "Daily Operator", "operator123", "Operator"),
        ("viewer",   "Report Viewer",  "viewer123",   "Viewer"),
    ]
    now = _now()
    return [
        {
            "id":               storage.new_id("user_"),
            "username":         uname,
            "display_name":     dname,
            "email":            f"{uname}@farm.local",
            "password_hash":    hash_password(pw),
            "role":             role,
            "status":           "active",
            "created_at":       now,
            "last_updated_at":  now,
        }
        for uname, dname, pw, role in defaults
    ]


def _migrate_from_yaml() -> list[dict]:
    """
    Read legacy credentials from users.yaml and return as users.json records.
    Returns empty list if users.yaml does not exist or has no credentials.
    """
    if not _YAML_FILE.exists():
        return []
    try:
        with open(_YAML_FILE, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return []

    usernames = cfg.get("credentials", {}).get("usernames", {})
    if not usernames:
        return []

    from core import storage
    now = _now()
    return [
        {
            "id":               storage.new_id("user_"),
            "username":         uname,
            "display_name":     data.get("name", uname),
            "email":            data.get("email", f"{uname}@farm.local"),
            "password_hash":    data.get("password", ""),
            "role":             data.get("role", "Viewer"),
            "status":           "active",
            "created_at":       now,
            "last_updated_at":  now,
        }
        for uname, data in usernames.items()
    ]


def _ensure_users_initialized() -> None:
    """
    Guarantee that data/users.json is populated before any auth check.
    Runs once per Streamlit session via session_state flag.
    """
    if st.session_state.get("_users_initialized"):
        return

    from core import storage
    users = storage.load_users()

    if not users:
        users = _migrate_from_yaml()
        if not users:
            users = _create_default_users()
        storage.save_users(users)

    # Keep cookie config in YAML (separate from user records)
    if not _YAML_FILE.exists():
        _save_cookie_config(_DEFAULT_COOKIE.copy())

    st.session_state["_users_initialized"] = True


# ── Authenticator ──────────────────────────────────────────────────────────────

def _build_stauth_credentials() -> dict:
    """
    Build the credentials dict stauth expects, using only *active* users.
    Disabled users are excluded so they cannot log in.
    """
    from core import storage
    result: dict[str, dict] = {}
    for user in storage.load_users():
        if user.get("status", "active") == "active":
            result[user["username"]] = {
                "email":    user.get("email", f"{user['username']}@farm.local"),
                "name":     user.get("display_name", user["username"]),
                "password": user.get("password_hash", ""),
            }
    return {"usernames": result}


def _get_authenticator() -> stauth.Authenticate:
    """Return the cached stauth.Authenticate object, rebuilding if invalidated."""
    if "_stauth_authenticator" not in st.session_state:
        cookie_cfg  = _load_cookie_config()
        credentials = _build_stauth_credentials()
        auth = stauth.Authenticate(
            credentials,
            cookie_cfg["name"],
            cookie_cfg["key"],
            cookie_cfg["expiry_days"],
            auto_hash=False,
        )
        st.session_state["_stauth_authenticator"] = auth
    return st.session_state["_stauth_authenticator"]


def invalidate_auth_cache() -> None:
    """
    Drop the cached authenticator so it is rebuilt with fresh user data on the
    next request.  Call this after creating, editing, or disabling a user.
    """
    st.session_state.pop("_stauth_authenticator", None)


# ── User context ───────────────────────────────────────────────────────────────

def _sync_current_user() -> None:
    """
    Populate st.session_state['current_user'] from users.json.
    Called every time we confirm authentication_status is True.
    """
    username = st.session_state.get("username") or ""

    from core import storage
    record = storage.get_user_by_username(username)

    role         = record.get("role", "Viewer")        if record else "Viewer"
    display_name = record.get("display_name", username) if record else username

    st.session_state["current_user"] = {
        "id":           record.get("id", "") if record else "",
        "username":     username,
        "display_name": display_name,
        "role":         role,
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def ensure_authenticated() -> bool:
    """
    Gate function — call at the top of app.py before rendering anything else.
    Returns True when the user is authenticated and active.
    When False, a login form (or error) has already been rendered; call st.stop().
    """
    _ensure_users_initialized()

    # Fast path: already authenticated this session
    if st.session_state.get("authentication_status") is True:
        # Mid-session disable check: verify user is still active in users.json
        username = st.session_state.get("username", "")
        from core import storage
        record = storage.get_user_by_username(username)
        if record and record.get("status", "active") != "active":
            logout()
            st.warning("Your account has been disabled. Contact an administrator.")
            return False
        _sync_current_user()
        return True

    # Not authenticated — render login UI
    col_center = st.columns([1, 2, 1])[1]
    with col_center:
        st.markdown("## Growout + Pregrow Management System")
        st.caption("Please log in to continue.")
        st.divider()

        auth = _get_authenticator()
        try:
            auth.login()
        except Exception as exc:
            st.error(f"Login error: {exc}")

    status = st.session_state.get("authentication_status")

    if status is True:
        _sync_current_user()
        return True

    if status is False:
        with col_center:
            st.error("Incorrect username or password.")

    return False


def logout() -> None:
    """
    Log out the current user — clears the auth cookie and session state.
    Call st.rerun() immediately after.
    """
    auth = st.session_state.get("_stauth_authenticator")
    if auth is not None:
        try:
            auth.logout(location="unrendered")
        except Exception:
            pass

    for key in [
        "authentication_status", "username", "name",
        "current_user", "_stauth_authenticator",
        "_users_initialized",
    ]:
        st.session_state.pop(key, None)


def get_current_user() -> dict:
    """Return {username, display_name, role} for the authenticated user."""
    return st.session_state.get("current_user", {
        "username":     "unknown",
        "display_name": "Unknown",
        "role":         "Viewer",
    })


def require_role(allowed_roles: list[str]) -> bool:
    """Return True if the current user's role is in *allowed_roles*."""
    return get_current_user().get("role") in allowed_roles


def can_write(module_name: str) -> bool:
    """Return True if the current user can make writes in *module_name*."""
    role = get_current_user().get("role", "Viewer")
    return role in WRITE_ACCESS.get(module_name, [])


def is_read_only() -> bool:
    """Return True if the current user is a Viewer."""
    return get_current_user().get("role") == "Viewer"


def allowed_pages() -> list[str]:
    """Return page names visible to the current user."""
    role = get_current_user().get("role", "Viewer")
    return [page for page, roles in PAGE_ACCESS.items() if role in roles]
