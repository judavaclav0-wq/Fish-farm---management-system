"""
modules/dashboard.py
Live Dashboard — farm-wide overview powered by the Stock Engine.
"""

import html as _html
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta
from core import storage
from core.stock_engine import compute_farm_state, farm_summary


def _status_badge(warnings: list[str]) -> str:
    if not warnings:
        return "🟢"
    if any("CRITICAL" in w for w in warnings):
        return "🔴"
    return "🟡"


def _biomass_bar(tank_states: list[dict]) -> go.Figure:
    names = [s.get("tank_name", "Unnamed tank") for s in tank_states]
    biomass = [s.get("biomass_kg", 0) for s in tank_states]

    fig = go.Figure()
    fig.add_bar(
        name="Biomass (kg)",
        x=names,
        y=biomass,
        text=[f"{v:.1f} kg" for v in biomass],
        textposition="outside",
    )
    fig.update_layout(
        title="Biomass per tank (kg)",
        height=340,
        margin=dict(t=40, b=20, l=0, r=0),
        showlegend=False,
    )
    return fig


def _period_range(period: str) -> tuple[date, date]:
    today = date.today()

    if period == "This week":
        start = today - timedelta(days=today.weekday())
        end = today
        return start, end

    if period == "Last week":
        this_week_start = today - timedelta(days=today.weekday())
        start = this_week_start - timedelta(days=7)
        end = this_week_start - timedelta(days=1)
        return start, end

    if period == "This month":
        start = today.replace(day=1)
        end = today
        return start, end

    if period == "Last month":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        start = last_month_end.replace(day=1)
        end = last_month_end
        return start, end

    return today, today


def _composition_label(comp: list[dict], fallback: str = "") -> str:
    """
    Format a batch_composition list as a short human-readable string.
    Used in the tank status table and card batch line.

    Examples:
        []                        → "Unassigned"
        [Batch A 100%]            → "Batch A"
        [Batch A 80%, Batch B 20%] → "Batch A 80% | Batch B 20%"
    """
    if not comp:
        return fallback or "Unassigned"
    if len(comp) == 1:
        return comp[0].get("batch_name") or fallback or "—"
    parts = [
        f"{b.get('batch_name', '—')} {b.get('percentage', 0):.0f}%"
        for b in comp
    ]
    return " | ".join(parts)


def _render_tank_visual_overview(filtered_states: list[dict]) -> None:
    """Render a circular card grid for every tank in filtered_states.

    All styles are inline so the HTML is built as a single unbroken line.
    Multi-line indented HTML causes Markdown to treat indented lines as code
    blocks and display raw tags — inline styles on one line avoids that entirely.
    """

    st.subheader("Tank visual overview")

    # ── Shared style strings ───────────────────────────────────────────────────
    S_GRID   = ("display:grid;grid-template-columns:repeat(auto-fit,minmax(162px,1fr));"
                "gap:20px;padding:6px 0 20px 0;")
    S_ITEM   = "display:flex;justify-content:center;"
    S_NAME   = ("display:block;font-size:0.78rem;font-weight:700;color:#e2e8f0;"
                "line-height:1.25;margin-bottom:4px;max-width:132px;"
                "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;")
    S_BIO    = "display:block;font-size:0.92rem;font-weight:600;color:#f1f5f9;line-height:1.3;"
    S_DETAIL = "display:block;font-size:0.68rem;color:#94a3b8;line-height:1.45;"
    S_BATCH  = ("display:block;font-size:0.66rem;color:#64748b;margin-top:3px;"
                "max-width:132px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;")

    cards_html = ""

    for s in filtered_states:
        tank_name  = _html.escape(str(s.get("tank_name") or "—"))
        biomass    = float(s.get("biomass_kg",    0) or 0)
        density    = float(s.get("density_kg_m3", 0) or 0)
        avg_weight = float(s.get("avg_weight_g",  0) or 0)
        has_warn   = bool(s.get("warnings"))

        border = "rgba(239,68,68,0.80)"  if has_warn else "rgba(148,163,184,0.35)"
        bg     = "rgba(239,68,68,0.07)"  if has_warn else "rgba(255,255,255,0.05)"

        S_CIRCLE = (
            f"width:158px;height:158px;border-radius:50%;"
            f"border:2px solid {border};background:{bg};"
            "display:flex;flex-direction:column;"
            "align-items:center;justify-content:center;"
            "text-align:center;padding:12px;box-sizing:border-box;"
        )

        # Build batch line(s) from batch_composition.
        # Single batch → "Batch A" ; multi → "A 80%" + "B 20%" (one span each).
        # All spans are concatenated on one line — no newlines allowed here.
        comp = s.get("batch_composition", [])
        if not comp:
            batch_spans = f'<span style="{S_BATCH}">Unassigned</span>'
        elif len(comp) == 1:
            bname = _html.escape(comp[0].get("batch_name") or "—")
            batch_spans = f'<span style="{S_BATCH}">{bname}</span>'
        else:
            batch_spans = "".join(
                f'<span style="{S_BATCH}">{_html.escape(b.get("batch_name","—"))} {b.get("percentage",0):.0f}%</span>'
                for b in comp[:3]   # cap at 3 lines to keep circle readable
            )

        # Each card is a single concatenated string — no newlines, no indentation.
        cards_html += (
            f'<div style="{S_ITEM}">'
              f'<div style="{S_CIRCLE}">'
                f'<span style="{S_NAME}">{tank_name}</span>'
                f'<span style="{S_BIO}">{biomass:.1f} kg</span>'
                f'<span style="{S_DETAIL}">Density: {density:.2f} kg/m³</span>'
                f'<span style="{S_DETAIL}">Avg: {avg_weight:.1f} g</span>'
                f'{batch_spans}'
              f'</div>'
            f'</div>'
        )

    st.markdown(
        f'<div style="{S_GRID}">{cards_html}</div>',
        unsafe_allow_html=True,
    )


def _render_chemical_usage(daily_logs: list[dict], selected_unit: str) -> None:
    """Inventory / Chemical usage section.

    Daily logs store chemical fields per tank row, but bicarbonate and
    treatment are system-level values copied into every tank entry.
    We group by date + system first and take the FIRST value — this prevents
    multiplying amounts by the number of tanks.
    """
    st.subheader("Inventory / Chemical usage")

    if not daily_logs:
        st.info("No log data available yet.")
        return

    df = pd.DataFrame(daily_logs)

    # Filter to the selected unit
    if "system_name" in df.columns:
        df = df[df["system_name"] == selected_unit]

    if df.empty:
        st.info("No log data for this unit.")
        return

    # Fill missing chemical columns — old records won't have them
    for col_name, default in [
        ("bicarbonate_kg",   0.0),
        ("treatment_type",   "None"),
        ("treatment_amount", 0.0),
        ("treatment_unit",   ""),
        ("treatments",       ""),
        ("notes",            ""),
        ("operator",         ""),
    ]:
        if col_name not in df.columns:
            df[col_name] = default
        else:
            df[col_name] = df[col_name].fillna(default)

    df["bicarbonate_kg"]   = pd.to_numeric(df["bicarbonate_kg"],   errors="coerce").fillna(0.0)
    df["treatment_amount"] = pd.to_numeric(df["treatment_amount"], errors="coerce").fillna(0.0)

    if "date" not in df.columns:
        st.info("No date column found in log data.")
        return

    df["_date_obj"] = pd.to_datetime(df["date"], errors="coerce").dt.date

    # ── Period selector ────────────────────────────────────────────────────────
    chem_period = st.selectbox(
        "Usage period",
        ["Today", "This week", "This month", "Last month", "Custom range"],
        key="chem_period_filter",
    )

    if chem_period == "Custom range":
        cc1, cc2 = st.columns(2)
        chem_start = cc1.date_input(
            "Start date",
            value=date.today().replace(day=1),
            key="chem_start_date",
        )
        chem_end = cc2.date_input(
            "End date",
            value=date.today(),
            key="chem_end_date",
        )
    elif chem_period == "Today":
        chem_start = chem_end = date.today()
    else:
        chem_start, chem_end = _period_range(chem_period)

    df_period = df[
        (df["_date_obj"] >= chem_start) &
        (df["_date_obj"] <= chem_end)
    ].copy()

    if df_period.empty:
        st.info("No records for the selected period.")
        return

    # ── Group by date + system: one row per system-day ─────────────────────────
    grouped = (
        df_period.groupby(["date", "system_name"], dropna=False)
        .agg(
            operator=        ("operator",         "first"),
            bicarbonate_kg=  ("bicarbonate_kg",   "first"),
            treatment_type=  ("treatment_type",   "first"),
            treatment_amount=("treatment_amount", "first"),
            treatment_unit=  ("treatment_unit",   "first"),
            treatments=      ("treatments",       "first"),
            notes=           ("notes",            "first"),
        )
        .reset_index()
    )

    # ── Usage metrics ──────────────────────────────────────────────────────────
    total_bicarbonate = float(grouped["bicarbonate_kg"].sum())
    formalin_l  = float(grouped[grouped["treatment_type"] == "Formalin"] ["treatment_amount"].sum())
    wofa_l      = float(grouped[grouped["treatment_type"] == "Wofa"]     ["treatment_amount"].sum())
    halamid_g   = float(grouped[grouped["treatment_type"] == "Halamid"]  ["treatment_amount"].sum())
    vaccine_l   = float(grouped[grouped["treatment_type"] == "Vaccine"]  ["treatment_amount"].sum())
    salt_kg     = float(grouped[grouped["treatment_type"] == "Salt"]     ["treatment_amount"].sum())

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Bicarbonate (kg)", f"{total_bicarbonate:.2f}")
    m2.metric("Formalin (l)",     f"{formalin_l:.2f}")
    m3.metric("Wofa (l)",         f"{wofa_l:.2f}")
    m4.metric("Halamid (g)",      f"{halamid_g:.1f}")
    m5.metric("Vaccine (l)",      f"{vaccine_l:.2f}")
    m6.metric("Salt (kg)",        f"{salt_kg:.2f}")

    # ── Records table ──────────────────────────────────────────────────────────
    display_cols = [
        "date", "system_name", "operator",
        "bicarbonate_kg", "treatment_type", "treatment_amount", "treatment_unit",
        "treatments", "notes",
    ]
    present_cols = [c for c in display_cols if c in grouped.columns]

    st.dataframe(
        grouped[present_cols].sort_values("date", ascending=False),
        use_container_width=True,
        hide_index=True,
    )


def render() -> None:
    st.header("Live Dashboard")

    farm         = storage.load_farm()
    batches      = storage.load_batches()
    daily_logs   = storage.load_daily_logs()
    movements    = storage.load_movements()
    grading_logs = storage.load_grading_logs()

    # ── View mode ───────────────────────────────────────────
    view_mode = st.radio(
        "View mode",
        ["Current", "Historical"],
        horizontal=True,
        key="dashboard_view_mode",
    )

    as_of_date = None

    if view_mode == "Historical":
        selected_date = st.date_input(
            "Select date",
            value=date.today(),
            key="dashboard_hist_date",
        )
        as_of_date = str(selected_date)

    # ── Compute state ───────────────────────────────────────
    tank_states = compute_farm_state(
        farm,
        batches,
        daily_logs,
        movements,
        as_of_date=as_of_date,
        grading_logs=grading_logs,
    )

    if not tank_states:
        st.info("No tanks configured. Start in **Farm Setup**.")
        return

    # ── Filters ─────────────────────────────────────────────
    st.subheader("Filters")

    farm_name = farm.get("name") or farm.get("farm_name") or "Farm"

    col_farm, col_unit = st.columns(2)

    selected_farm = col_farm.selectbox(
        "Farm",
        options=[farm_name],
        index=0,
        key="dashboard_farm_filter",
    )

    unit_names = sorted({s.get("system_name", "—") for s in tank_states})
    selected_unit = col_unit.selectbox(
        "Unit",
        options=unit_names,
        index=0,
        key="dashboard_unit_filter",
    )

    filtered_states = [
        s for s in tank_states
        if s.get("system_name", "—") == selected_unit
    ]

    if not filtered_states:
        st.warning("No tanks found for selected unit.")
        return

    # ── Dashboard daily mortality alerts ─────────────────────
    for s in filtered_states:
        daily_mortality_pct = float(s.get("daily_mortality_pct", 0) or 0)
        max_mortality_pct   = float(s.get("max_mortality_percent_day", 0) or 0)

        if max_mortality_pct > 0 and daily_mortality_pct > max_mortality_pct:
            warning_text = (
                f"Daily mortality above limit: {daily_mortality_pct:.2f}% "
                f"(limit {max_mortality_pct:.2f}%)"
            )
            warnings = list(s.get("warnings", []))
            if warning_text not in warnings:
                warnings.append(warning_text)
            s["warnings"] = warnings

    summary = farm_summary(filtered_states)

    # ── Mortality KPI values for selected unit ───────────────
    today_str    = str(date.today())
    month_prefix = today_str[:7]
    unit_tank_names_for_kpi = {s.get("tank_name") for s in filtered_states}

    mortality_today = sum(
        int(log.get("mortality_fish", 0) or 0)
        for log in daily_logs
        if log.get("date") == today_str
        and log.get("tank_name") in unit_tank_names_for_kpi
    )

    mortality_this_month = sum(
        int(log.get("mortality_fish", 0) or 0)
        for log in daily_logs
        if str(log.get("date", "")).startswith(month_prefix)
        and log.get("tank_name") in unit_tank_names_for_kpi
    )

    st.divider()

    # ── KPI row ─────────────────────────────────────────────
    title_suffix = f"{selected_farm} · {selected_unit}"
    if as_of_date:
        title_suffix += f" · {as_of_date}"

    st.subheader(title_suffix)

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Total fish",           f"{summary.get('total_fish', 0):,}")
    k2.metric("Total biomass",        f"{summary.get('total_biomass_kg', 0):,.1f} kg")
    k3.metric("Total feed",           f"{summary.get('total_feed_kg', 0):,.1f} kg")
    k4.metric("Mortality today",      f"{mortality_today:,}")
    k5.metric("Mortality this month", f"{mortality_this_month:,}")
    k6.metric("Tanks with alerts",    f"{summary.get('tanks_with_warnings', 0)}")

    st.divider()

    # ── Tank visual overview ────────────────────────────────
    _render_tank_visual_overview(filtered_states)

    st.divider()

    # ── Tank table ─────────────────────────────────────────
    st.subheader("Tank status")

    rows = []

    for s in filtered_states:
        last_log = s.get("last_log") or {}

        rows.append({
            "Status":                _status_badge(s.get("warnings", [])),
            "Tank":                  s.get("tank_name", "—"),
            "Batch":                 _composition_label(
                                         s.get("batch_composition", []),
                                         s.get("batch_name") or "",
                                     ),
            "Grading":               s.get("grading_status", "—"),
            "Fish count":            s.get("fish_count", 0),
            "Avg weight (g)":        s.get("avg_weight_g", 0),
            "Biomass (kg)":          s.get("biomass_kg", 0),
            "Density (kg/m³)":       s.get("density_kg_m3", 0),
            "Feed kg/day":           s.get("feed_kg_day", 0),
            "Daily mortality":       s.get("daily_mortality_fish", 0),
            "Daily mortality %":     s.get("daily_mortality_pct", 0),
            "Cumulative mortality":  s.get("total_mortality", 0),
            "Cumulative mortality %": s.get("cumulative_mortality_pct", 0),
            "O2 last":               last_log.get("oxygen", "—"),
            "Last log":              last_log.get("date", "—"),
            "Grading date":          s.get("grading_date") or "—",
            "Grading samples":       s.get("grading_sample_count") or "—",
            "Grading avg wt (g)":    s.get("grading_avg_weight_g") or "—",
            "Small %":               s.get("small_pct")         if s.get("grading_date") else "—",
            "Medium %":              s.get("medium_pct")        if s.get("grading_date") else "—",
            "Harvest ready %":       s.get("harvest_ready_pct") if s.get("grading_date") else "—",
            "Alerts":                " | ".join(s.get("warnings", [])) if s.get("warnings") else "—",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()

    # ── INVENTORY / CHEMICAL USAGE ─────────────────────────
    _render_chemical_usage(daily_logs, selected_unit)

    st.divider()

    # ── HARVEST OVERVIEW ───────────────────────────────────
    st.subheader("Harvest overview")

    df_movements = pd.DataFrame(movements)

    if not df_movements.empty and "movement_type" in df_movements.columns:
        df_harvest = df_movements[df_movements["movement_type"] == "harvest"].copy()

        if not df_harvest.empty:
            if "from_system_name" in df_harvest.columns:
                df_harvest = df_harvest[df_harvest["from_system_name"] == selected_unit]

            if "date" in df_harvest.columns:
                df_harvest["date"] = pd.to_datetime(df_harvest["date"]).dt.date

            period = st.selectbox(
                "Harvest period",
                ["This week", "Last week", "This month", "Last month", "Custom range"],
                key="harvest_period_filter",
            )

            if period == "Custom range":
                col_start, col_end = st.columns(2)
                start_date = col_start.date_input(
                    "Start date",
                    value=date.today().replace(day=1),
                    key="harvest_start_date",
                )
                end_date = col_end.date_input(
                    "End date",
                    value=date.today(),
                    key="harvest_end_date",
                )
            else:
                start_date, end_date = _period_range(period)

            df_harvest_period = df_harvest[
                (df_harvest["date"] >= start_date)
                & (df_harvest["date"] <= end_date)
            ]

            total_harvested_fish = int(df_harvest_period.get("quantity_fish", pd.Series(dtype=float)).sum())
            total_harvested_kg   = float(df_harvest_period.get("quantity_kg", pd.Series(dtype=float)).sum())
            harvest_events       = len(df_harvest_period)

            avg_weight = 0.0
            if total_harvested_fish > 0:
                avg_weight = total_harvested_kg * 1000 / total_harvested_fish

            h1, h2, h3, h4 = st.columns(4)
            h1.metric("Harvested fish",    f"{total_harvested_fish:,}")
            h2.metric("Harvested biomass", f"{total_harvested_kg:,.1f} kg")
            h3.metric("Harvest events",    f"{harvest_events}")
            h4.metric("Avg weight",        f"{avg_weight:.1f} g")

            if not df_harvest_period.empty:
                st.markdown("### Harvest records")

                harvest_display_cols = [
                    "date", "from_system_name", "from_tank_name",
                    "quantity_fish", "quantity_kg", "avg_weight_g",
                    "reason", "operator", "notes",
                ]
                present_harvest_cols = [
                    col for col in harvest_display_cols
                    if col in df_harvest_period.columns
                ]

                st.dataframe(
                    df_harvest_period[present_harvest_cols].sort_values("date", ascending=False),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No harvest records for the selected period.")
        else:
            st.info("No harvest records yet.")
    else:
        st.info("No harvest records yet.")

    st.divider()

    # ── ANALYTICS ──────────────────────────────────────────
    st.subheader("Analytics")

    unit_tank_names = {s.get("tank_name") for s in filtered_states}

    df_logs = pd.DataFrame(daily_logs)

    if not df_logs.empty:

        df_logs = df_logs[df_logs["tank_name"].isin(unit_tank_names)]

        if "date" in df_logs.columns:
            df_logs["date"] = pd.to_datetime(df_logs["date"])

        # ── Parameter graph ───────────────────────────────
        st.markdown("### Water parameters")

        param_options = [
            col for col in ["temperature", "oxygen", "ph", "nh4", "no2", "no3"]
            if col in df_logs.columns
        ]

        if param_options:
            selected_param = st.selectbox("Parameter", param_options)

            fig = go.Figure()

            grouped_params = df_logs.groupby("date")[selected_param].mean().reset_index()

            fig.add_scatter(
                x=grouped_params["date"],
                y=grouped_params[selected_param],
                mode="lines+markers",
                name=selected_unit,
            )

            st.plotly_chart(
                fig,
                use_container_width=True,
                key=f"param_chart_{selected_param}_{selected_unit}",
            )

        # ── Mortality graph ───────────────────────────────
        if "mortality_fish" in df_logs.columns:

            st.markdown("### Mortality per tank")

            selected_tank = st.selectbox(
                "Select tank",
                sorted(unit_tank_names),
                key="mortality_tank_select",
            )

            df_tank = df_logs[df_logs["tank_name"] == selected_tank].copy()

            df_tank_daily = (
                df_tank
                .groupby("date")["mortality_fish"]
                .sum()
                .reset_index()
                .sort_values("date")
            )

            fig2 = go.Figure()

            fig2.add_scatter(
                x=df_tank_daily["date"],
                y=df_tank_daily["mortality_fish"],
                mode="lines+markers",
                name="Mortality",
            )

            st.plotly_chart(
                fig2,
                use_container_width=True,
                key=f"mortality_chart_{selected_tank}",
            )

    else:
        st.info("No log data available yet.")

    st.divider()

    if st.checkbox("Show debug stock engine data"):
        st.json(filtered_states)
