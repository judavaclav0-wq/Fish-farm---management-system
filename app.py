"""
Growout + Pregrow Management System

Run with:
    streamlit run app.py
"""
from dotenv import load_dotenv

load_dotenv()
import os
import sys

import streamlit as st

# Allow imports from the growout package root (core/, modules/)
sys.path.insert(0, os.path.dirname(__file__))

st.set_page_config(
    page_title="Growout + Pregrow Management System",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Form-data preservation ────────────────────────────────────────────────────
# Session state keys that must NOT be stashed (auth internals + stash itself).
_PRESERVE_SKIP = frozenset({
    "authentication_status",
    "username",
    "name",
    "current_user",
    "_stauth_authenticator",
    "_users_initialized",
    "_preserved_form_data",
    "_preserved_page",
    "_form_data_restored",
})


def _preserve_form_data_if_needed() -> None:
    """
    Called BEFORE the auth check.  When auth is about to block the user,
    stash every non-auth session_state value so it survives the login → rerun
    cycle within the same WebSocket session.

    In Streamlit, widgets outside st.form sync on every interaction; widgets
    inside st.form sync on submit — both paths populate session_state before
    the script's auth gate runs.  Stashing here lets us restore entered data
    after the user re-authenticates.

    A prior stash is never overwritten so data persists across multiple
    consecutive failed login attempts.
    """
    if st.session_state.get("authentication_status") is True:
        return  # Still authenticated — nothing to preserve
    if "_preserved_form_data" in st.session_state:
        return  # Already stashed from a previous failed-auth run

    stash: dict = {}
    for key, value in list(st.session_state.items()):
        if key in _PRESERVE_SKIP:
            continue
        if key.startswith("_"):
            continue
        if key.startswith("FormSubmitter:"):
            # Don't stash submit-button states; user must re-submit explicitly.
            continue
        if value is None:
            continue
        stash[key] = value

    if stash:
        st.session_state["_preserved_form_data"] = stash
        # Remember which page the user was on so we can restore navigation.
        page_key = st.session_state.get("_nav_page")
        if page_key:
            st.session_state["_preserved_page"] = page_key


def _restore_form_data_if_needed(visible_page_labels: list[str]) -> None:
    """
    Called AFTER successful auth.  Writes stashed values back into
    session_state so every form widget picks them up on its first render.
    The nav radio is restored only if the page is still accessible.
    """
    stash = st.session_state.pop("_preserved_form_data", None)
    if not stash:
        return

    for key, value in stash.items():
        # Never overwrite a key that already has a value in the new session
        # (widget that the user explicitly changed after re-login).
        if key not in st.session_state:
            st.session_state[key] = value

    # Restore page navigation if the page is still accessible to this role.
    preserved_page = st.session_state.pop("_preserved_page", None)
    if preserved_page and preserved_page in visible_page_labels:
        st.session_state["_nav_page"] = preserved_page

    st.session_state["_form_data_restored"] = True

# ── Global styles ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
    [data-testid="stSidebar"] { min-width: 220px; max-width: 220px; }
    .block-container { padding-top: 1.5rem; }
    div[data-testid="metric-container"] > div { font-size: 0.82rem; }
</style>
""", unsafe_allow_html=True)

# ── Authentication gate ───────────────────────────────────────────────────────
# Must happen before any sidebar or page content is rendered.
from core.auth import ensure_authenticated, logout, get_current_user, allowed_pages, PAGE_ACCESS

# Stash any session_state form data BEFORE the auth gate can stop execution.
# This preserves entered values when the session expires mid-interaction.
_preserve_form_data_if_needed()

if not ensure_authenticated():
    st.stop()

# From here the user is authenticated.
user = get_current_user()

# ── Lazy imports (avoid import errors if a module has a bug) ──────────────────
def _import(mod_name: str):
    import importlib
    return importlib.import_module(f"modules.{mod_name}")


# ── Page registry ─────────────────────────────────────────────────────────────
PAGES: dict[str, str] = {
    "Dashboard":         "dashboard",
    "Farm Setup":        "farm_setup",
    "Batch Management":  "batches",
    "Daily Log":         "daily_log",
    "Movements":         "movements",
    "Water Consumption": "water_consumption",
    "Histograms":        "grading",
    "Activity Log":      "activity_log",
    "User Management":   "user_management",
    "System Test / QA":  "system_qa",
}

PAGE_ICONS: dict[str, str] = {
    "Dashboard":         "",
    "Farm Setup":        "",
    "Batch Management":  "",
    "Daily Log":         "",
    "Movements":         "",
    "Water Consumption": "",
    "Histograms":        "",
    "Activity Log":      "",
    "User Management":   "",
    "System Test / QA":  "",
}

# ── Sidebar ───────────────────────────────────────────────────────────────────

# Role-filtered page list must be computed before restore so we can validate the
# preserved page name against the current user's access rights.
visible         = allowed_pages()
visible_page_labels = [p for p in PAGES if p in visible]

# Restore stashed form data now that auth has passed and page list is known.
_restore_form_data_if_needed(visible_page_labels)

with st.sidebar:
    st.markdown("## Growout + Pregrow Management System")
    st.caption("v1.0")
    st.divider()

    # Farm name
    try:
        from core import storage as _storage
        _farm = _storage.load_farm()
        if _farm.get("farm_name"):
            st.caption(f"Farm: **{_farm['farm_name']}**")
    except Exception:
        pass

    # Logged-in user
    st.caption(f"User: **{user['display_name']}**")
    st.caption(f"Role: {user['role']}")

    if st.button("Logout", use_container_width=True):
        logout()
        st.rerun()

    st.divider()

    page_label = st.radio(
        "Navigation",
        visible_page_labels,
        format_func=lambda p: f"{PAGE_ICONS[p]}  {p}",
        label_visibility="collapsed",
        key="_nav_page",
    )

    st.divider()
    st.caption("Data stored locally in `/data`")

# ── Session-restored notice ───────────────────────────────────────────────────
# Shown once after re-login when form data was stashed during a session expiry.
if st.session_state.pop("_form_data_restored", False):
    st.info(
        "**Session restored.** Your previously entered data has been recovered — "
        "please review and submit again.",
        icon="ℹ️",
    )

# ── Render selected page ──────────────────────────────────────────────────────
# Guard: show warning if somehow a page outside the user's access is requested.
if page_label not in visible_page_labels:
    st.warning("You do not have permission to access this page.")
    st.stop()

mod_name = PAGES[page_label]
try:
    module = _import(mod_name)
    module.render()
except ModuleNotFoundError as exc:
    st.error(f"Module not found: {exc}")
except Exception as exc:
    st.exception(exc)
