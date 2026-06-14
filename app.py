"""
Growout + Pregrow Management System

Run with:
    streamlit run app.py
"""
from dotenv import load_dotenv

load_dotenv()
import os
import streamlit as st

st.sidebar.write("Backend:", os.getenv("APP_STORAGE_BACKEND"))

import sys
import os

# Allow imports from the growout package root (core/, modules/)
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st

st.set_page_config(
    page_title="Growout + Pregrow Management System",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

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
    "Dashboard":        "dashboard",
    "Farm Setup":       "farm_setup",
    "Batch Management": "batches",
    "Daily Log":        "daily_log",
    "Movements":        "movements",
    "Histograms":       "grading",
    "Activity Log":     "activity_log",
    "User Management":  "user_management",
}

PAGE_ICONS: dict[str, str] = {
    "Dashboard":        "",
    "Farm Setup":       "",
    "Batch Management": "",
    "Daily Log":        "",
    "Movements":        "",
    "Histograms":       "",
    "Activity Log":     "",
    "User Management":  "",
}

# ── Sidebar ───────────────────────────────────────────────────────────────────
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

    # Role-filtered navigation
    visible = allowed_pages()
    visible_page_labels = [p for p in PAGES if p in visible]

    page_label = st.radio(
        "Navigation",
        visible_page_labels,
        format_func=lambda p: f"{PAGE_ICONS[p]}  {p}",
        label_visibility="collapsed",
    )

    st.divider()
    st.caption("Data stored locally in `/data`")

# ── Render selected page ──────────────────────────────────────────────────────
# Guard: show warning if somehow a page outside the user's access is requested.
if page_label not in allowed_pages():
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
