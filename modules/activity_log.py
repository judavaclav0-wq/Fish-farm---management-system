"""
modules/activity_log.py
Activity Log — audit trail of user operations across all modules.

Every record includes the authenticated user who performed the action
(user_id, username, user_display_name, user_role).  Old records that
predate this field are shown with "—" in the user columns.
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from core import storage
from core.auth import require_role


def _period_range(period: str) -> tuple[date, date]:
    today = date.today()
    if period == "Today":
        return today, today
    if period == "This week":
        return today - timedelta(days=today.weekday()), today
    if period == "Last week":
        start_this = today - timedelta(days=today.weekday())
        return start_this - timedelta(days=7), start_this - timedelta(days=1)
    if period == "This month":
        return today.replace(day=1), today
    if period == "Last month":
        first_this = today.replace(day=1)
        last_prev  = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    return today, today


def render() -> None:
    if not require_role(["Admin", "Manager"]):
        st.warning("You do not have permission to access Activity Log.")
        return

    st.header("Activity Log")

    activity_logs = storage.load_activity_logs()

    if not activity_logs:
        st.info(
            "No activity recorded yet. "
            "Operations saved in Daily Log, Movements, and Histograms will appear here."
        )
        return

    df = pd.DataFrame(activity_logs)

    # ── Normalize columns ─────────────────────────────────────────────────────
    # Fill missing columns so filters never crash on old/partial records.
    for col_name, default in [
        ("timestamp",        ""),
        ("module",           ""),
        ("action",           ""),
        ("date",             ""),
        ("system_name",      ""),
        ("tank_id",          ""),
        ("tank_name",        ""),
        ("operator",         ""),
        ("summary",          ""),
        ("user_id",          ""),
        ("username",         ""),
        ("user_display_name",""),
        ("user_role",        ""),
    ]:
        if col_name not in df.columns:
            df[col_name] = default
        else:
            df[col_name] = df[col_name].fillna(default)

    # Backward compat: old records stored the user in a 'user_name' field.
    # Bridge: if username is empty but user_name exists, copy it over.
    if "user_name" in df.columns:
        mask = (df["username"] == "") & df["user_name"].astype(str).str.strip().ne("")
        df.loc[mask, "username"] = df.loc[mask, "user_name"]

    # Parse timestamp for period filtering.
    df["_ts_date"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.date

    # ── Filters ───────────────────────────────────────────────────────────────
    st.subheader("Filters")

    fc1, fc2, fc3, fc4 = st.columns(4)

    period = fc1.selectbox(
        "Period",
        ["Today", "This week", "This month", "Last month", "Custom range", "All time"],
        key="al_period",
    )

    if period == "Custom range":
        cr1, cr2 = st.columns(2)
        filter_start = cr1.date_input("Start date", value=date.today().replace(day=1), key="al_start")
        filter_end   = cr2.date_input("End date",   value=date.today(),                key="al_end")
    elif period == "All time":
        filter_start = None
        filter_end   = None
    else:
        filter_start, filter_end = _period_range(period)

    modules_all = sorted([
        m for m in df["module"].dropna().astype(str).str.strip().unique().tolist()
        if m
    ])
    filter_module = fc2.selectbox("Module", ["All"] + modules_all, key="al_module")

    filter_action = fc3.selectbox(
        "Action",
        ["All", "create", "update", "delete", "disable", "archive", "restore"],
        key="al_action",
    )

    systems_all   = sorted(df["system_name"].dropna().astype(str).str.strip().unique().tolist())
    filter_system = fc4.selectbox("System", ["All"] + systems_all, key="al_system")

    # ── User filter (second row) ───────────────────────────────────────────────
    uf1, uf2, _ = st.columns([2, 2, 4])

    # Build user options from current log data; fall back to username if no display name.
    user_options_raw = (
        df[["username", "user_display_name"]]
        .drop_duplicates()
        .sort_values("username")
    )
    user_options = ["All"]
    for _, row in user_options_raw.iterrows():
        uname = str(row["username"]).strip()
        dname = str(row["user_display_name"]).strip()
        if uname:
            label = f"{dname} ({uname})" if dname and dname != uname else uname
            user_options.append(label)
    # De-duplicate while preserving order
    seen: set[str] = set()
    user_options = [x for x in user_options if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]

    filter_user = uf1.selectbox("User", user_options, key="al_user")

    filter_operator = uf2.text_input(
        "Operator search", placeholder="type to filter...", key="al_operator"
    )

    # ── Apply filters ─────────────────────────────────────────────────────────
    df_f = df.copy()

    if filter_start is not None:
        df_f = df_f[df_f["_ts_date"] >= filter_start]
    if filter_end is not None:
        df_f = df_f[df_f["_ts_date"] <= filter_end]
    if filter_module != "All":
        df_f = df_f[df_f["module"] == filter_module]
    if filter_action != "All":
        df_f = df_f[df_f["action"] == filter_action]
    if filter_system != "All":
        df_f = df_f[df_f["system_name"] == filter_system]
    if filter_operator.strip():
        df_f = df_f[
            df_f["operator"].str.contains(
                filter_operator.strip(), case=False, na=False
            )
        ]
    if filter_user != "All":
        # Extract just the username part (before the parenthesis if formatted as "Name (user)")
        uname_part = filter_user.split("(")[-1].rstrip(")").strip() if "(" in filter_user else filter_user
        df_f = df_f[df_f["username"] == uname_part]

    # ── Metrics ───────────────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total operations", len(df_f))
    m2.metric("Create",  int((df_f["action"] == "create").sum()))
    m3.metric("Update",  int((df_f["action"] == "update").sum()))
    m4.metric("Delete",  int((df_f["action"] == "delete").sum()))

    st.divider()

    # ── Table ─────────────────────────────────────────────────────────────────
    if df_f.empty:
        st.info("No records match the selected filters.")
        return

    display_cols = [
        "timestamp",
        "user_display_name",
        "username",
        "user_role",
        "module",
        "action",
        "date",
        "system_name",
        "tank_name",
        "summary",
    ]
    present_cols = [c for c in display_cols if c in df_f.columns]

    display_df = (
        df_f[present_cols]
        .copy()
        .sort_values("timestamp", ascending=False)
    )

    # Replace empty strings with "—" for readability
    for col in ["user_display_name", "username", "user_role"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].replace("", "—")

    # Human-readable column headers
    display_df = display_df.rename(columns={
        "timestamp":         "Time",
        "user_display_name": "User",
        "username":          "Login",
        "user_role":         "Role",
        "module":            "Module",
        "action":            "Action",
        "date":              "Date",
        "system_name":       "System",
        "tank_name":         "Tank",
        "summary":           "Summary",
    })

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    csv_buf = display_df.to_csv(index=False)
    st.download_button(
        "Download CSV",
        csv_buf,
        file_name=f"activity_log_{date.today()}.csv",
        mime="text/csv",
    )
