"""
modules/water_consumption.py
Water meter readings and normalized daily consumption tracking.

Departments: Rafz 2 | Rafz 1 | Rafz 2 Fish
Each department maintains its own independent reading sequence.
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta
from io import BytesIO

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

from core import storage
from core.storage import new_id
from core.auth import can_write

DEPARTMENTS = ["Rafz 2", "Rafz 1", "Rafz 2 Fish"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sf(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_ts(r: dict) -> datetime | None:
    """Parse a record's combined date+time into a datetime, return None on failure."""
    try:
        return datetime.fromisoformat(str(r.get("timestamp", "")))
    except Exception:
        try:
            d = str(r.get("date", ""))
            t = str(r.get("time", "00:00"))
            return datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
        except Exception:
            return None


def _prev_reading(records: list[dict], department: str, before: datetime) -> dict | None:
    """Return the most-recent record for *department* strictly before *before*."""
    best_ts: datetime | None = None
    best_rec: dict | None = None
    for r in records:
        if r.get("department") != department:
            continue
        ts = _parse_ts(r)
        if ts is None or ts >= before:
            continue
        if best_ts is None or ts > best_ts:
            best_ts = ts
            best_rec = r
    return best_rec


def _fmt(v, fmt: str = ".1f") -> str:
    if v is None:
        return "—"
    try:
        return format(float(v), fmt)
    except (TypeError, ValueError):
        return "—"


def _in_period(r: dict, start: date, end: date) -> bool:
    """Return True if the record's date falls within [start, end] inclusive."""
    try:
        d = date.fromisoformat(str(r.get("date", "")))
        return start <= d <= end
    except Exception:
        return False


def _default_period(all_records: list[dict]) -> tuple[date, date]:
    """Return a sensible default (start, end) date pair for filters."""
    end = date.today()
    if all_records:
        dates = []
        for r in all_records:
            try:
                dates.append(date.fromisoformat(str(r.get("date", ""))))
            except Exception:
                pass
        start = min(dates) if dates else end - timedelta(days=30)
    else:
        start = end - timedelta(days=30)
    return start, end


# ---------------------------------------------------------------------------
# Entry form  (unchanged from original)
# ---------------------------------------------------------------------------

def _render_entry_form(all_records: list[dict]) -> None:
    if not can_write("Water Consumption"):
        st.error("Viewers have read-only access.")
        return

    st.subheader("New Meter Reading")

    with st.form("wc_new_reading"):
        c1, c2, c3 = st.columns(3)
        entry_date = c1.date_input("Date", value=date.today(), key="wc_date")
        entry_time = c2.time_input(
            "Time",
            value=datetime.now().replace(second=0, microsecond=0).time(),
            key="wc_time",
        )
        department = c3.selectbox("Department", DEPARTMENTS, key="wc_dept")

        meter_m3 = st.number_input(
            "Meter reading (m³)",
            min_value=0.0,
            value=0.0,
            step=10.0,
            format="%.1f",
            key="wc_meter",
        )

        submitted = st.form_submit_button("Save Reading", type="primary")

    if not submitted:
        return

    time_str = entry_time.strftime("%H:%M")
    ts = datetime.combine(entry_date, entry_time).replace(second=0, microsecond=0)

    prev = _prev_reading(all_records, department, ts)

    raw_consumption_m3:   float | None = None
    hours_since_previous: float | None = None
    m3_per_day:           float | None = None
    previous_meter_m3:    float | None = None
    is_first = prev is None

    if prev is not None:
        previous_meter_m3    = _sf(prev.get("meter_m3"))
        prev_ts              = _parse_ts(prev)
        hours_since_previous = (ts - prev_ts).total_seconds() / 3600.0

        if hours_since_previous <= 0:
            st.error(
                f"This timestamp ({entry_date} {time_str}) is not after the previous "
                f"reading for **{department}** "
                f"({prev.get('date')} {prev.get('time')}). "
                "Correct the date / time and try again."
            )
            return

        raw_consumption_m3 = meter_m3 - previous_meter_m3

        if raw_consumption_m3 < 0:
            st.warning(
                f"Meter reading ({meter_m3:,.1f} m³) is **lower** than the previous "
                f"reading ({previous_meter_m3:,.1f} m³) for **{department}**. "
                "This may indicate a meter reset or data entry error. "
                "Saving without normalized m³/day."
            )
        else:
            m3_per_day = raw_consumption_m3 / hours_since_previous * 24.0

    record: dict = {
        "id":                   new_id("wc_"),
        "date":                 entry_date.isoformat(),
        "time":                 time_str,
        "timestamp":            ts.isoformat(),
        "department":           department,
        "meter_m3":             meter_m3,
        "previous_meter_m3":    previous_meter_m3,
        "raw_consumption_m3":   raw_consumption_m3,
        "hours_since_previous": hours_since_previous,
        "m3_per_day":           m3_per_day,
        "is_first_reading":     is_first,
        "created_at":           datetime.now().isoformat(),
    }

    storage.append_water_consumption(record)

    if is_first:
        st.success(f"First reading for **{department}** saved — {meter_m3:,.1f} m³")
    elif m3_per_day is not None:
        st.success(
            f"Reading saved — **{m3_per_day:.1f} m³/day** "
            f"({raw_consumption_m3:.1f} m³ over {hours_since_previous:.1f} h)"
        )
    else:
        st.success("Reading saved.")

    st.rerun()


# ---------------------------------------------------------------------------
# Overview  (date-filtered + trend chart)
# ---------------------------------------------------------------------------

def _period_dates(selection: str, custom_start: date, custom_end: date) -> tuple[date, date]:
    """Return (start, end) for the named preset or the custom range."""
    today = date.today()
    if selection == "This week":
        start = today - timedelta(days=today.weekday())          # Monday
        return start, today
    if selection == "Last week":
        monday_this = today - timedelta(days=today.weekday())
        end   = monday_this - timedelta(days=1)                  # last Sunday
        start = end - timedelta(days=6)                          # last Monday
        return start, end
    if selection == "This month":
        return today.replace(day=1), today
    if selection == "Last month":
        first_this = today.replace(day=1)
        end   = first_this - timedelta(days=1)
        start = end.replace(day=1)
        return start, end
    # Custom range
    return custom_start, custom_end


def _render_overview(all_records: list[dict]) -> None:
    st.subheader("Department Overview")

    if not all_records:
        st.info("No readings recorded yet. Add the first reading in **Record Reading**.")
        return

    # ════════════════════════════════════════════════════════════════════════
    # 1. Single Day Overview
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("#### Single Day Overview")

    selected_day = st.date_input("Select date", value=date.today(), key="wc_ov_day")
    selected_day_str = selected_day.isoformat()

    day_records = [r for r in all_records if r.get("date") == selected_day_str]

    cols = st.columns(3)
    for i, dept in enumerate(DEPARTMENTS):
        dept_day = sorted(
            [r for r in day_records if r.get("department") == dept],
            key=lambda r: r.get("timestamp", ""),
            reverse=True,
        )
        with cols[i]:
            st.markdown(f"**{dept}**")
            if not dept_day:
                st.caption("No reading for selected date")
            else:
                rec = dept_day[0]
                st.metric("Meter (m³)",           f"{_sf(rec.get('meter_m3')):,.1f}")
                st.metric("m³/day",               _fmt(rec.get("m3_per_day")))
                st.metric("Raw consumption (m³)",  _fmt(rec.get("raw_consumption_m3")))
                st.caption(f"Recorded: {rec.get('time', '')}".strip())

    st.divider()

    # ════════════════════════════════════════════════════════════════════════
    # 2. Period Summary
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("#### Period Summary")

    PERIOD_OPTIONS = ["This week", "Last week", "This month", "Last month", "Custom range"]
    period_sel = st.selectbox("Period", PERIOD_OPTIONS, key="wc_ov_period")

    custom_start = custom_end = date.today()
    if period_sel == "Custom range":
        default_start, default_end = _default_period(all_records)
        pc1, pc2 = st.columns(2)
        custom_start = pc1.date_input("Start date", value=default_start, key="wc_ov_cstart")
        custom_end   = pc2.date_input("End date",   value=default_end,   key="wc_ov_cend")
        if custom_start > custom_end:
            st.warning("Start date is after end date.")
            return

    period_start, period_end = _period_dates(period_sel, custom_start, custom_end)
    st.caption(f"Period: {period_start} — {period_end}")

    period_records = [r for r in all_records if _in_period(r, period_start, period_end)]

    pcols = st.columns(3)
    for i, dept in enumerate(DEPARTMENTS):
        dept_recs = sorted(
            [r for r in period_records if r.get("department") == dept],
            key=lambda r: r.get("timestamp", ""),
        )
        with pcols[i]:
            st.markdown(f"**{dept}**")
            if not dept_recs:
                st.caption("No readings for selected period")
                continue

            valid_m3pd = [_sf(r["m3_per_day"]) for r in dept_recs if r.get("m3_per_day") is not None]
            has_raw    = any(r.get("raw_consumption_m3") is not None for r in dept_recs)
            total_raw  = sum(_sf(r["raw_consumption_m3"]) for r in dept_recs if r.get("raw_consumption_m3") is not None)

            st.metric("Readings",               str(len(dept_recs)))
            st.metric("First meter (m³)",        f"{_sf(dept_recs[0].get('meter_m3')):,.1f}")
            st.metric("Last meter (m³)",         f"{_sf(dept_recs[-1].get('meter_m3')):,.1f}")
            st.metric("Total consumption (m³)",  f"{total_raw:,.1f}" if has_raw else "—")
            st.metric("Avg m³/day",              f"{sum(valid_m3pd)/len(valid_m3pd):.1f}" if valid_m3pd else "—")
            st.metric("Min m³/day",              f"{min(valid_m3pd):.1f}"                 if valid_m3pd else "—")
            st.metric("Max m³/day",              f"{max(valid_m3pd):.1f}"                 if valid_m3pd else "—")

    st.divider()

    # ════════════════════════════════════════════════════════════════════════
    # 3. Trend chart  (uses same period as Period Summary)
    # ════════════════════════════════════════════════════════════════════════
    chart_rows = [
        {
            "date":       r.get("date", ""),
            "department": r.get("department", ""),
            "m3_per_day": _sf(r.get("m3_per_day")),
        }
        for r in period_records
        if r.get("m3_per_day") is not None
    ]

    if not chart_rows:
        st.caption("Trend chart will appear once two or more readings exist in the selected period.")
        return

    st.subheader("m³/day Trend")
    chart_df = pd.DataFrame(chart_rows)
    chart_pivot = (
        chart_df
        .pivot_table(index="date", columns="department", values="m3_per_day", aggfunc="mean")
        .sort_index()
    )
    st.line_chart(chart_pivot, use_container_width=True)


# ---------------------------------------------------------------------------
# History table  (date filter + department filter + sort)
# ---------------------------------------------------------------------------

def _render_history(all_records: list[dict]) -> None:
    st.subheader("Reading History")

    if not all_records:
        st.info("No readings recorded yet.")
        return

    # ── Filters ──────────────────────────────────────────────────────────────
    default_start, default_end = _default_period(all_records)

    fc1, fc2 = st.columns(2)
    hist_start = fc1.date_input("Start date", value=default_start, key="wc_hist_start")
    hist_end   = fc2.date_input("End date",   value=default_end,   key="wc_hist_end")

    fc3, fc4 = st.columns([3, 1])
    dept_filter = fc3.multiselect(
        "Department", DEPARTMENTS, default=DEPARTMENTS, key="wc_hist_dept"
    )
    sort_newest = fc4.radio(
        "Sort", ["Newest first", "Oldest first"],
        horizontal=True, key="wc_sort"
    ) == "Newest first"

    if hist_start > hist_end:
        st.warning("Start date is after end date.")
        return

    filtered = [
        r for r in all_records
        if r.get("department") in dept_filter and _in_period(r, hist_start, hist_end)
    ]
    filtered.sort(key=lambda r: r.get("timestamp", ""), reverse=sort_newest)

    if not filtered:
        st.info("No records match the selected filters.")
    else:
        rows = [
            {
                "Date":                  r.get("date", ""),
                "Time":                  r.get("time", ""),
                "Department":            r.get("department", ""),
                "Meter (m³)":            _sf(r.get("meter_m3")),
                "Raw consumption (m³)":  _fmt(r.get("raw_consumption_m3")),
                "Hours since previous":  _fmt(r.get("hours_since_previous")),
                "m³/day":                _fmt(r.get("m3_per_day")),
                "Note":                  "First reading" if r.get("is_first_reading") else "",
            }
            for r in filtered
        ]
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        csv = df.to_csv(index=False)
        st.download_button(
            "Download CSV",
            csv,
            "water_consumption.csv",
            "text/csv",
            key="wc_dl_csv",
        )

    # ── PDF report section ───────────────────────────────────────────────────
    st.divider()
    _render_pdf_section(all_records)


# ---------------------------------------------------------------------------
# PDF report
# ---------------------------------------------------------------------------

def _build_pdf(records: list[dict], start: date, end: date, departments: list[str]) -> BytesIO:
    buffer = BytesIO()
    doc    = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()
    elems  = []

    # ── Title & metadata ─────────────────────────────────────────────────────
    elems.append(Paragraph("<b>Water Consumption Report</b>", styles["Title"]))
    elems.append(Spacer(1, 8))
    elems.append(Paragraph(f"Period: {start} to {end}", styles["Normal"]))
    dept_label = ", ".join(departments) if departments else "All"
    elems.append(Paragraph(f"Departments: {dept_label}", styles["Normal"]))
    elems.append(Paragraph(
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        styles["Normal"],
    ))
    elems.append(Spacer(1, 16))

    # ── Per-department summary ───────────────────────────────────────────────
    elems.append(Paragraph("<b>Summary by Department</b>", styles["Heading2"]))
    elems.append(Spacer(1, 6))

    summary_header = [
        "Department", "Readings", "First meter\n(m³)", "Last meter\n(m³)",
        "Total consump.\n(m³)", "Avg m³/day", "Min m³/day", "Max m³/day",
    ]
    summary_data = [summary_header]

    for dept in departments:
        dept_recs = sorted(
            [r for r in records if r.get("department") == dept],
            key=lambda r: r.get("timestamp", ""),
        )
        if not dept_recs:
            summary_data.append([dept, "0", "—", "—", "—", "—", "—", "—"])
            continue

        valid_m3pd = [_sf(r["m3_per_day"]) for r in dept_recs if r.get("m3_per_day") is not None]
        total_raw  = sum(
            _sf(r.get("raw_consumption_m3"))
            for r in dept_recs
            if r.get("raw_consumption_m3") is not None
        )
        has_raw = any(r.get("raw_consumption_m3") is not None for r in dept_recs)

        summary_data.append([
            dept,
            str(len(dept_recs)),
            f"{_sf(dept_recs[0].get('meter_m3')):,.1f}",
            f"{_sf(dept_recs[-1].get('meter_m3')):,.1f}",
            f"{total_raw:,.1f}" if has_raw else "—",
            f"{sum(valid_m3pd) / len(valid_m3pd):.1f}" if valid_m3pd else "—",
            f"{min(valid_m3pd):.1f}" if valid_m3pd else "—",
            f"{max(valid_m3pd):.1f}" if valid_m3pd else "—",
        ])

    sum_table = Table(summary_data, repeatRows=1)
    sum_table.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#2d6a9f")),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("GRID",        (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elems.append(sum_table)
    elems.append(Spacer(1, 18))

    # ── Detail table ─────────────────────────────────────────────────────────
    elems.append(Paragraph("<b>Detailed Readings</b>", styles["Heading2"]))
    elems.append(Spacer(1, 6))

    detail_header = ["Date", "Time", "Department", "Meter\n(m³)", "Raw cons.\n(m³)", "Hours", "m³/day"]
    detail_data   = [detail_header]

    sorted_all = sorted(records, key=lambda r: r.get("timestamp", ""))
    for r in sorted_all:
        detail_data.append([
            r.get("date", ""),
            r.get("time", ""),
            r.get("department", ""),
            f"{_sf(r.get('meter_m3')):,.1f}",
            _fmt(r.get("raw_consumption_m3")),
            _fmt(r.get("hours_since_previous")),
            _fmt(r.get("m3_per_day")),
        ])

    det_table = Table(detail_data, repeatRows=1)
    det_table.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#2d6a9f")),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("GRID",        (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elems.append(det_table)

    doc.build(elems)
    buffer.seek(0)
    return buffer


def _render_pdf_section(all_records: list[dict]) -> None:
    st.subheader("Generate PDF Report")

    default_start, default_end = _default_period(all_records)

    pc1, pc2 = st.columns(2)
    pdf_start = pc1.date_input("Report start date", value=default_start, key="wc_pdf_start")
    pdf_end   = pc2.date_input("Report end date",   value=default_end,   key="wc_pdf_end")

    dept_options = ["All departments"] + DEPARTMENTS
    pdf_dept_sel = st.selectbox("Department", dept_options, key="wc_pdf_dept")

    if pdf_start > pdf_end:
        st.warning("Start date must be before end date.")
        return

    if st.button("Generate PDF Report", key="wc_pdf_generate"):
        selected_depts = DEPARTMENTS if pdf_dept_sel == "All departments" else [pdf_dept_sel]

        report_records = [
            r for r in all_records
            if r.get("department") in selected_depts and _in_period(r, pdf_start, pdf_end)
        ]

        if not report_records:
            st.warning("No data found for the selected period and department(s).")
            return

        pdf_buf = _build_pdf(report_records, pdf_start, pdf_end, selected_depts)

        filename = (
            f"water_consumption_report_{pdf_start}_to_{pdf_end}.pdf"
        )
        st.download_button(
            "Download PDF Report",
            data=pdf_buf,
            file_name=filename,
            mime="application/pdf",
            key="wc_pdf_download",
        )


# ---------------------------------------------------------------------------
# Delete  (unchanged from original)
# ---------------------------------------------------------------------------

def _render_delete(all_records: list[dict]) -> None:
    if not can_write("Water Consumption"):
        st.error("Viewers have read-only access.")
        return

    st.subheader("Delete a Reading")

    if not all_records:
        st.info("No readings to delete.")
        return

    sorted_recs = sorted(all_records, key=lambda r: r.get("timestamp", ""), reverse=True)

    labels = [
        f"{r.get('date','')} {r.get('time','')} — {r.get('department','')} — "
        f"{_sf(r.get('meter_m3')):,.1f} m³"
        for r in sorted_recs
    ]
    idx = st.selectbox(
        "Select reading to delete",
        range(len(labels)),
        format_func=lambda i: labels[i],
        key="wc_del_select",
    )

    if st.button("Delete selected reading", type="primary", key="wc_del_btn"):
        target_id = sorted_recs[idx].get("id")
        updated   = [r for r in all_records if r.get("id") != target_id]
        storage.save_water_consumption(updated)
        st.success("Reading deleted.")
        st.rerun()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def render() -> None:
    st.title("Water Consumption")

    all_records = storage.load_water_consumption()
    write_ok    = can_write("Water Consumption")

    if write_ok:
        tab_entry, tab_overview, tab_history, tab_delete = st.tabs(
            ["Record Reading", "Overview", "History", "Delete"]
        )
        with tab_entry:
            _render_entry_form(all_records)
        with tab_overview:
            _render_overview(all_records)
        with tab_history:
            _render_history(all_records)
        with tab_delete:
            _render_delete(all_records)
    else:
        st.info("Read-only mode — viewing is enabled, data entry is restricted.", icon="ℹ️")
        tab_overview, tab_history = st.tabs(["Overview", "History"])
        with tab_overview:
            _render_overview(all_records)
        with tab_history:
            _render_history(all_records)
