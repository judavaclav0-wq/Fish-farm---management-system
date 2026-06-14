"""
modules/user_management.py
User Management — Admin-only page to view, create, edit, disable, and delete users.

All user records live in data/users.json (or Supabase when that backend is active).
Passwords are stored as bcrypt hashes; plain text is never saved or shown.
Disabled users cannot log in but their records are kept for audit history.
Permanently deleted users are fully removed from storage.
"""

import streamlit as st
import pandas as pd
from datetime import datetime
from core import storage
from core.auth import require_role, get_current_user, hash_password, ROLES, invalidate_auth_cache


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def render() -> None:
    if not require_role(["Admin"]):
        st.warning("You do not have permission to access User Management.")
        return

    st.header("User Management")

    current_user     = get_current_user()
    current_username = current_user.get("username", "")

    users = storage.load_users()

    # ── Overview table ────────────────────────────────────────────────────────
    st.subheader("All users")

    if users:
        rows = [
            {
                "Username":     u.get("username", ""),
                "Display name": u.get("display_name", ""),
                "Role":         u.get("role", ""),
                "Status":       u.get("status", "active"),
                "Created":      u.get("created_at", "—"),
                "Last updated": u.get("last_updated_at", "—"),
            }
            for u in users
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No users found.")

    st.divider()

    # ── Add new user ──────────────────────────────────────────────────────────
    with st.expander("Add new user", expanded=False):
        with st.form("add_user", clear_on_submit=True):
            col1, col2 = st.columns(2)
            new_username     = col1.text_input("Username", placeholder="e.g. jsmith")
            new_display_name = col2.text_input("Display name", placeholder="e.g. John Smith")

            col3, col4 = st.columns(2)
            new_password = col3.text_input("Password", type="password")
            new_role     = col4.selectbox("Role", options=ROLES)

            submitted = st.form_submit_button("Create user", type="primary")

            if submitted:
                errors: list[str] = []
                uname_clean = new_username.strip().lower()

                if not uname_clean:
                    errors.append("Username is required.")
                if not new_display_name.strip():
                    errors.append("Display name is required.")
                if not new_password:
                    errors.append("Password is required.")
                if uname_clean and any(u.get("username") == uname_clean for u in users):
                    errors.append(f"Username '{uname_clean}' is already taken.")

                if errors:
                    for err in errors:
                        st.error(err)
                else:
                    now = _now()
                    new_user = {
                        "id":              storage.new_id("user_"),
                        "username":        uname_clean,
                        "display_name":    new_display_name.strip(),
                        "email":           f"{uname_clean}@farm.local",
                        "password_hash":   hash_password(new_password),
                        "role":            new_role,
                        "status":          "active",
                        "created_at":      now,
                        "last_updated_at": now,
                    }
                    users.append(new_user)
                    storage.save_users(users)
                    invalidate_auth_cache()
                    storage.log_activity(
                        module="User Management",
                        action="create",
                        summary=f"Created user {uname_clean} as {new_role}",
                    )
                    st.success(f"User '{new_display_name.strip()}' ({uname_clean}) created.")
                    st.rerun()

    st.divider()

    # ── Per-user edit cards ───────────────────────────────────────────────────
    st.subheader("Edit users")

    for user in users:
        uid          = user.get("id", "")
        username     = user.get("username", "")
        display_name = user.get("display_name", username)
        role         = user.get("role", "Viewer")
        status       = user.get("status", "active")
        is_self      = (username == current_username)
        is_disabled  = (status != "active")

        badge = "🟢 Active" if not is_disabled else "🔴 Disabled"

        with st.expander(f"{display_name} ({username}) — {role} — {badge}", expanded=False):

            # ── Self-edit warning ──────────────────────────────────────────
            if is_self:
                st.info(
                    "This is your own account. "
                    "You cannot disable yourself, change your role away from Admin, "
                    "or delete yourself."
                )

            # ── Edit form ──────────────────────────────────────────────────
            with st.form(f"edit_user_{uid}", clear_on_submit=False):
                col1, col2 = st.columns(2)

                e_display = col1.text_input(
                    "Display name",
                    value=display_name,
                    key=f"edn_{uid}",
                )
                role_idx = ROLES.index(role) if role in ROLES else 0
                e_role   = col2.selectbox(
                    "Role",
                    options=ROLES,
                    index=role_idx,
                    key=f"erl_{uid}",
                    disabled=is_self,   # cannot demote yourself
                )

                st.caption(
                    "Current password cannot be displayed. "
                    "Use the fields below to set a new password. "
                    "Leave blank to keep the existing password."
                )
                col3, col4 = st.columns(2)
                new_pw  = col3.text_input("New password",     type="password", key=f"epw1_{uid}")
                new_pw2 = col4.text_input("Confirm password", type="password", key=f"epw2_{uid}")

                save_btn = st.form_submit_button("💾 Save changes", type="primary")

                if save_btn:
                    form_errors: list[str] = []
                    if not e_display.strip():
                        form_errors.append("Display name is required.")
                    if new_pw and new_pw != new_pw2:
                        form_errors.append("Passwords do not match.")

                    if form_errors:
                        for err in form_errors:
                            st.error(err)
                    else:
                        now     = _now()
                        changes: list[str] = []

                        for u in users:
                            if u.get("id") == uid:
                                if u["display_name"] != e_display.strip():
                                    u["display_name"] = e_display.strip()
                                    changes.append(f"display name → '{e_display.strip()}'")
                                if not is_self and u["role"] != e_role:
                                    u["role"] = e_role
                                    changes.append(f"role → {e_role}")
                                if new_pw:
                                    u["password_hash"] = hash_password(new_pw)
                                    changes.append("password reset")
                                u["last_updated_at"] = now

                        if changes:
                            storage.save_users(users)
                            invalidate_auth_cache()
                            storage.log_activity(
                                module="User Management",
                                action="update",
                                summary=f"Updated user {username}: {', '.join(changes)}",
                            )
                            st.success("User updated.")
                            st.rerun()
                        else:
                            st.info("No changes detected.")

            # ── Disable / Enable ───────────────────────────────────────────
            st.divider()

            if is_self:
                st.caption("⚠️ You cannot disable or delete your own account.")

            else:
                if is_disabled:
                    if st.button(
                        "Enable user",
                        key=f"enable_{uid}",
                        type="secondary",
                        use_container_width=False,
                    ):
                        now = _now()
                        for u in users:
                            if u.get("id") == uid:
                                u["status"]          = "active"
                                u["last_updated_at"] = now
                        storage.save_users(users)
                        invalidate_auth_cache()
                        storage.log_activity(
                            module="User Management",
                            action="update",
                            summary=f"Re-enabled user {username}",
                        )
                        st.rerun()

                else:
                    # Disable confirmation toggle
                    confirm_dis_key = f"confirm_disable_{uid}"
                    if not st.session_state.get(confirm_dis_key):
                        if st.button("Disable user", key=f"disable_{uid}", type="secondary"):
                            st.session_state[confirm_dis_key] = True
                            st.rerun()
                    else:
                        st.warning(
                            f"Disable **{display_name}** ({username})? "
                            "They will not be able to log in until re-enabled."
                        )
                        cd1, cd2, _ = st.columns([2, 2, 4])
                        if cd1.button("Confirm disable", key=f"disable_confirm_{uid}", type="primary"):
                            now = _now()
                            for u in users:
                                if u.get("id") == uid:
                                    u["status"]          = "disabled"
                                    u["last_updated_at"] = now
                            storage.save_users(users)
                            invalidate_auth_cache()
                            storage.log_activity(
                                module="User Management",
                                action="disable",
                                summary=f"Disabled user {username}",
                            )
                            st.session_state.pop(confirm_dis_key, None)
                            st.rerun()
                        if cd2.button("Cancel", key=f"disable_cancel_{uid}"):
                            st.session_state.pop(confirm_dis_key, None)
                            st.rerun()

                # ── Permanent delete ───────────────────────────────────────
                st.divider()

                confirm_del_key  = f"confirm_del_{uid}"
                confirm_text_key = f"del_text_{uid}"

                if not st.session_state.get(confirm_del_key):
                    if st.button(
                        "Delete user permanently",
                        key=f"del_{uid}",
                        type="secondary",
                    ):
                        st.session_state[confirm_del_key] = True
                        st.rerun()
                else:
                    st.error(
                        f"⚠️ **Permanently delete {display_name} ({username})?**  \n"
                        "This will remove the user record completely. "
                        "This action cannot be undone."
                    )
                    st.text_input(
                        'Type **DELETE** to confirm',
                        key=confirm_text_key,
                        placeholder="DELETE",
                    )
                    dd1, dd2, _ = st.columns([2, 2, 4])
                    if dd1.button(
                        "Confirm permanent delete",
                        key=f"del_confirm_{uid}",
                        type="primary",
                    ):
                        typed = st.session_state.get(confirm_text_key, "").strip()
                        if typed != "DELETE":
                            st.error('You must type exactly DELETE (all caps) to confirm.')
                        else:
                            storage.delete_user(uid)
                            invalidate_auth_cache()
                            storage.log_activity(
                                module="User Management",
                                action="delete",
                                summary=f"Permanently deleted user {username}",
                            )
                            st.session_state.pop(confirm_del_key, None)
                            st.session_state.pop(confirm_text_key, None)
                            st.rerun()
                    if dd2.button("Cancel", key=f"del_cancel_{uid}"):
                        st.session_state.pop(confirm_del_key, None)
                        st.session_state.pop(confirm_text_key, None)
                        st.rerun()
