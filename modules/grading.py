"""
modules/grading.py
Histograms — record fish weight samples per tank, analyse size distribution.
"""

import re
import streamlit as st
import pandas as pd
from datetime import date
from io import BytesIO, StringIO

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm

from core import storage
from core.stock_engine import compute_farm_state


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_weights(raw: str) -> list[float]:
    """Parse weight values from a free-form text blob.
    Accepts comma / space / semicolon / newline separators.
    Returns positive floats only; silently skips empty or non-numeric tokens.
    """
    tokens = re.split(r"[,;\s]+", raw.strip())
    weights = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        try:
            v = float(t.replace(",", "."))
            if v > 0:
                weights.append(v)
        except ValueError:
            continue
    return weights


# ── Analysis ──────────────────────────────────────────────────────────────────

def _analyse(weights: list[float], small_threshold: float, harvest_threshold: float) -> dict:
    """Calculate size-class statistics for a list of weight samples."""
    n = len(weights)
    if n == 0:
        return {}

    small   = [w for w in weights if w < small_threshold]
    harvest = [w for w in weights if w >= harvest_threshold]
    medium  = [w for w in weights if small_threshold <= w < harvest_threshold]

    return {
        "sample_count":        n,
        "avg_weight_g":        round(sum(weights) / n, 1),
        "min_weight_g":        round(min(weights), 1),
        "max_weight_g":        round(max(weights), 1),
        "small_count":         len(small),
        "medium_count":        len(medium),
        "harvest_ready_count": len(harvest),
        "small_pct":           round(len(small)   / n * 100, 1),
        "medium_pct":          round(len(medium)  / n * 100, 1),
        "harvest_ready_pct":   round(len(harvest) / n * 100, 1),
    }


def _ensure_pcts(rec: dict) -> dict:
    """Back-fill percentage fields for old records that may be missing them.

    Priority:
    1. If raw weights exist — recalculate everything from scratch.
    2. If only counts + sample_count exist — derive percentages from those.
    """
    rec = dict(rec)

    # Support legacy field name "weights_g" as well as current "weights"
    weights = rec.get("weights") or rec.get("weights_g") or []
    n = rec.get("sample_count") or len(weights)

    if n == 0:
        return rec

    needs_pcts = any(
        rec.get(k) is None
        for k in ("small_pct", "medium_pct", "harvest_ready_pct")
    )

    if needs_pcts:
        if weights:
            result = _analyse(
                [float(w) for w in weights],
                float(rec.get("small_threshold_g") or 0),
                float(rec.get("harvest_threshold_g") or 0),
            )
            for k in (
                "small_pct", "medium_pct", "harvest_ready_pct",
                "small_count", "medium_count", "harvest_ready_count",
                "avg_weight_g", "min_weight_g", "max_weight_g",
            ):
                if rec.get(k) is None:
                    rec[k] = result.get(k)
        else:
            # Fall back: derive percentages from stored counts
            for count_field, pct_field in [
                ("small_count",         "small_pct"),
                ("medium_count",        "medium_pct"),
                ("harvest_ready_count", "harvest_ready_pct"),
            ]:
                count = rec.get(count_field)
                if count is not None and rec.get(pct_field) is None:
                    try:
                        rec[pct_field] = round(int(count) / n * 100, 1)
                    except (TypeError, ValueError):
                        pass

    return rec


# ── PDF export ────────────────────────────────────────────────────────────────

def _category(weight: float, small_threshold: float, harvest_threshold: float) -> str:
    if weight < small_threshold:
        return "Small"
    if weight >= harvest_threshold:
        return "Harvest ready"
    return "Medium"


def _generate_histogram_pdf(
    farm_name: str,
    system_name: str,
    tank_name: str,
    log_date: str,
    operator: str,
    weights: list[float],
    small_threshold: float,
    harvest_threshold: float,
    results: dict,
    notes: str,
) -> BytesIO:
    """Build and return a ReportLab PDF as a BytesIO buffer."""

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    elements = []

    # ── Header ────────────────────────────────────────────────────────────────
    elements.append(Paragraph("<b>Histogram Report</b>", styles["Title"]))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(f"Farm: {farm_name}",     styles["Normal"]))
    elements.append(Paragraph(f"Unit: {system_name}",   styles["Normal"]))
    elements.append(Paragraph(f"Tank: {tank_name}",     styles["Normal"]))
    elements.append(Paragraph(f"Date: {log_date}",      styles["Normal"]))
    elements.append(Paragraph(f"Operator: {operator or '—'}", styles["Normal"]))
    elements.append(Spacer(1, 14))

    # ── Summary table ─────────────────────────────────────────────────────────
    elements.append(Paragraph("<b>Summary</b>", styles["Heading3"]))
    elements.append(Spacer(1, 4))

    summary_data = [
        ["Parameter", "Value"],
        ["Sample count",           str(results.get("sample_count", "—"))],
        ["Average weight",         f"{results.get('avg_weight_g', '—')} g"],
        ["Min weight",             f"{results.get('min_weight_g', '—')} g"],
        ["Max weight",             f"{results.get('max_weight_g', '—')} g"],
        ["Small threshold",        f"< {small_threshold} g"],
        ["Harvest-ready threshold", f"≥ {harvest_threshold} g"],
        ["Small %",                f"{results.get('small_pct', '—')} %"],
        ["Medium %",               f"{results.get('medium_pct', '—')} %"],
        ["Harvest ready %",        f"{results.get('harvest_ready_pct', '—')} %"],
    ]

    summary_table = Table(summary_data, colWidths=[9 * cm, 7 * cm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0),  colors.HexColor("#4C9BE8")),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("BACKGROUND",  (0, 1), (-1, -1), colors.HexColor("#F5F8FC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EEF4FB")]),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#C0C0C0")),
        ("FONTSIZE",    (0, 0), (-1, -1), 10),
        ("TOPPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(summary_table)
    elements.append(Spacer(1, 14))

    # ── Notes ─────────────────────────────────────────────────────────────────
    elements.append(Paragraph("<b>Notes</b>", styles["Heading3"]))
    elements.append(Paragraph(notes or "—", styles["Normal"]))
    elements.append(Spacer(1, 14))

    # ── Weights table ─────────────────────────────────────────────────────────
    elements.append(Paragraph("<b>Fish weights</b>", styles["Heading3"]))
    elements.append(Spacer(1, 4))

    weight_data = [["Fish #", "Weight (g)", "Category"]]
    for i, w in enumerate(weights, start=1):
        cat = _category(w, small_threshold, harvest_threshold)
        weight_data.append([str(i), f"{w:.1f}", cat])

    # Colour-code category column
    weight_table = Table(weight_data, colWidths=[3 * cm, 5 * cm, 7 * cm], repeatRows=1)

    cell_styles = [
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#4C9BE8")),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#C0C0C0")),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ALIGN",         (0, 0), (1, -1), "CENTER"),
    ]

    # Row background alternation + category colours
    for row_idx, (_, w) in enumerate(zip(range(len(weights)), weights), start=1):
        cat = _category(w, small_threshold, harvest_threshold)
        if cat == "Small":
            bg = colors.HexColor("#FFF0ED")
        elif cat == "Harvest ready":
            bg = colors.HexColor("#EDFFF5")
        else:
            bg = colors.white if row_idx % 2 == 0 else colors.HexColor("#F5F8FC")
        cell_styles.append(("BACKGROUND", (0, row_idx), (-1, row_idx), bg))

    weight_table.setStyle(TableStyle(cell_styles))
    elements.append(weight_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer


# ── Record resolution ────────────────────────────────────────────────────────

def _resolve_record(
    grading_logs: list,
    system_name: str,
    tank_name: str,
    date_filter: str | None = None,
) -> dict | None:
    """Return the single histogram record for unit + tank + optional date.

    When date_filter is None and multiple records exist, the most-recent record
    is returned.  Returns None when the unit/tank has no records.
    """
    tank_records = [
        _ensure_pcts(r)
        for r in grading_logs
        if r.get("system_name") == system_name
        and r.get("tank_name")  == tank_name
    ]
    if not tank_records:
        return None
    if date_filter is not None:
        date_matched = [r for r in tank_records if r.get("date") == date_filter]
        if date_matched:
            return date_matched[0]
        # Fallback to most recent when the stored date_filter is stale
        return sorted(tank_records, key=lambda r: r.get("date", ""), reverse=True)[0]
    if len(tank_records) == 1:
        return tank_records[0]
    return sorted(tank_records, key=lambda r: r.get("date", ""), reverse=True)[0]


# ── Save / delete matching ────────────────────────────────────────────────────

def _matches_record(existing: dict, original_date: str, original_tank_id: str, original_tank_nm: str) -> bool:
    """True when `existing` is the record identified by date + tank."""
    return (
        existing.get("date") == original_date
        and (
            (existing.get("tank_id") and existing.get("tank_id") == original_tank_id)
            or existing.get("tank_name") == original_tank_nm
        )
    )


# ── Main render ───────────────────────────────────────────────────────────────

def render() -> None:
    st.header("Histograms")

    farm         = storage.load_farm()
    batches      = storage.load_batches()
    logs         = storage.load_daily_logs()
    movements    = storage.load_movements()
    grading_logs = storage.load_grading_logs()

    tank_states = compute_farm_state(
        farm, batches, logs, movements, grading_logs=grading_logs
    )

    if not tank_states:
        st.warning("No tanks defined. Go to **Farm Setup** first.")
        return

    farm_name = farm.get("farm_name", "Farm")

    tab_entry, tab_history = st.tabs(["New histogram", "History"])

    # ═══════════════════════════════════════════════════════════════════════════
    # NEW HISTOGRAM TAB
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_entry:
        grading_date     = st.date_input("Date", value=date.today(), key="grading_date")
        grading_date_str = str(grading_date)

        system_names    = sorted({s.get("system_name", "—") for s in tank_states})
        selected_system = st.selectbox("Unit", system_names, key="grading_system")

        system_tanks = [s for s in tank_states if s.get("system_name") == selected_system]
        tank_options = [s.get("tank_name", "—") for s in system_tanks]

        if not tank_options:
            st.warning("No tanks in this unit.")
            return

        selected_tank_name = st.selectbox("Tank", tank_options, key="grading_tank")

        tank_state = next(
            (s for s in system_tanks if s.get("tank_name") == selected_tank_name),
            {},
        )

        # Current stock KPIs
        if tank_state:
            ci1, ci2, ci3, ci4 = st.columns(4)
            ci1.metric("Fish count",     f"{tank_state.get('fish_count', 0):,}")
            ci2.metric("Biomass (kg)",   f"{tank_state.get('biomass_kg', 0):,.1f}")
            ci3.metric("Avg weight (g)", f"{tank_state.get('avg_weight_g', 0):.1f}")
            ci4.metric("Batch",          tank_state.get("batch_name") or "—")

        st.divider()

        # Thresholds
        col_t1, col_t2 = st.columns(2)
        small_threshold = col_t1.number_input(
            "Small threshold (g) — fish below this are too small",
            min_value=0.0,
            value=float(tank_state.get("small_threshold_g") or 50.0),
            step=1.0,
            format="%.1f",
            key="grading_small_threshold",
        )
        harvest_threshold = col_t2.number_input(
            "Harvest-ready threshold (g) — fish at or above this are ready",
            min_value=0.0,
            value=float(tank_state.get("harvest_threshold_g") or 300.0),
            step=5.0,
            format="%.1f",
            key="grading_harvest_threshold",
        )

        operator = st.text_input("Operator", key="grading_operator")
        notes    = st.text_area("Notes",     key="grading_notes")

        st.markdown("### Weight samples")
        st.caption(
            "Paste individual fish weights in grams — "
            "separated by commas, spaces, semicolons, or newlines."
        )

        raw_weights = st.text_area(
            "Weights (g)",
            height=160,
            placeholder="e.g. 320 285 310 295\nor: 320, 285, 310, 295",
            key="grading_weights_input",
        )

        weights = _parse_weights(raw_weights) if raw_weights.strip() else []

        if weights:
            result = _analyse(weights, small_threshold, harvest_threshold)

            if len(weights) < 30:
                st.warning(
                    f"Only {len(weights)} samples — "
                    "at least 30 are recommended for reliable results."
                )

            # ── Results ──────────────────────────────────────────────────────
            st.markdown("### Results")

            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Samples",    result["sample_count"])
            r2.metric("Avg weight", f"{result['avg_weight_g']} g")
            r3.metric("Min",        f"{result['min_weight_g']} g")
            r4.metric("Max",        f"{result['max_weight_g']} g")

            s1, s2, s3 = st.columns(3)
            s1.metric(
                f"Small  (<{small_threshold} g)",
                f"{result['small_pct']} %",
            )
            s2.metric(
                f"Medium  ({small_threshold}–{harvest_threshold} g)",
                f"{result['medium_pct']} %",
            )
            s3.metric(
                f"Harvest ready  (≥{harvest_threshold} g)",
                f"{result['harvest_ready_pct']} %",
            )

            st.divider()

            btn_save, btn_pdf = st.columns(2)

            if btn_save.button("Save histogram", type="primary", key="grading_save_btn"):
                record = {
                    "date":                grading_date_str,
                    "farm_name":           farm_name,
                    "system_name":         selected_system,
                    "tank_id":             tank_state.get("tank_id", ""),
                    "tank_name":           selected_tank_name,
                    "operator":            operator.strip(),
                    "notes":               notes.strip(),
                    "sample_count":        result["sample_count"],
                    "avg_weight_g":        result["avg_weight_g"],
                    "min_weight_g":        result["min_weight_g"],
                    "max_weight_g":        result["max_weight_g"],
                    "small_threshold_g":   small_threshold,
                    "harvest_threshold_g": harvest_threshold,
                    "small_count":         result["small_count"],
                    "medium_count":        result["medium_count"],
                    "harvest_ready_count": result["harvest_ready_count"],
                    "small_pct":           result["small_pct"],
                    "medium_pct":          result["medium_pct"],
                    "harvest_ready_pct":   result["harvest_ready_pct"],
                    "weights":             weights,
                }
                storage.append_grading_log(record)
                storage.log_activity(
                    module="Histograms",
                    action="create",
                    summary=(
                        f"Created histogram for tank {selected_tank_name}, "
                        f"avg weight {result['avg_weight_g']} g"
                    ),
                    date=grading_date_str,
                    system_name=selected_system,
                    tank_id=str(tank_state.get("tank_id", "")),
                    tank_name=selected_tank_name,
                    operator=operator.strip(),
                    details={
                        "sample_count": result["sample_count"],
                        "avg_weight_g": result["avg_weight_g"],
                    },
                )
                st.success(
                    f"Histogram saved — {result['sample_count']} samples, "
                    f"avg {result['avg_weight_g']} g."
                )
                st.rerun()

            if btn_pdf.button("Generate PDF", key="grading_pdf_btn"):
                pdf_buf = _generate_histogram_pdf(
                    farm_name=farm_name,
                    system_name=selected_system,
                    tank_name=selected_tank_name,
                    log_date=grading_date_str,
                    operator=operator.strip(),
                    weights=weights,
                    small_threshold=small_threshold,
                    harvest_threshold=harvest_threshold,
                    results=result,
                    notes=notes.strip(),
                )
                safe_system = selected_system.replace(" ", "_")
                safe_tank   = selected_tank_name.replace(" ", "_")
                st.download_button(
                    "Download PDF",
                    pdf_buf,
                    file_name=f"histogram_report_{grading_date_str}_{safe_system}_{safe_tank}.pdf",
                    mime="application/pdf",
                    key="grading_pdf_download",
                )

        else:
            st.info("Enter weight samples above to see results.")

    # ═══════════════════════════════════════════════════════════════════════════
    # HISTORY TAB
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_history:
        if not grading_logs:
            st.info("No histogram records yet.")
            return

        df_all = pd.DataFrame(grading_logs)

        # ── Filters — drive both the summary table and the edit form ──────────
        # Unit → Tank → Date acts as the record-selection hierarchy.
        # Selecting a specific Unit + Tank is required before the edit form
        # appears.  The Date filter resolves one record when multiple exist.
        col_f1, col_f2, col_f3 = st.columns(3)

        systems_h       = ["All"] + sorted(df_all["system_name"].dropna().unique().tolist())
        filter_system_h = col_f1.selectbox("Unit", systems_h, key="hist_filter_system")

        df_filtered = df_all.copy()
        if filter_system_h != "All":
            df_filtered = df_filtered[df_filtered["system_name"] == filter_system_h]

        tanks_h       = ["All"] + sorted(df_filtered["tank_name"].dropna().unique().tolist())
        filter_tank_h = col_f2.selectbox("Tank", tanks_h, key="hist_filter_tank")

        if filter_tank_h != "All":
            df_filtered = df_filtered[df_filtered["tank_name"] == filter_tank_h]

        dates_h       = ["All"] + sorted(df_filtered["date"].dropna().unique().tolist(), reverse=True)
        filter_date_h = col_f3.selectbox("Date", dates_h, key="hist_filter_date")

        if filter_date_h != "All":
            df_filtered = df_filtered[df_filtered["date"] == filter_date_h]

        # ── Summary table ─────────────────────────────────────────────────────
        display_cols = [
            "date", "system_name", "tank_name", "operator",
            "sample_count", "avg_weight_g", "min_weight_g", "max_weight_g",
            "small_threshold_g", "harvest_threshold_g",
            "small_pct", "medium_pct", "harvest_ready_pct",
            "notes",
        ]
        present_cols = [c for c in display_cols if c in df_filtered.columns]

        st.dataframe(
            df_filtered[present_cols].sort_values("date", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

        csv_buf = StringIO()
        df_filtered[present_cols].sort_values("date", ascending=False).to_csv(
            csv_buf, index=False
        )
        st.download_button(
            "Download CSV",
            csv_buf.getvalue(),
            file_name="histogram_history.csv",
            mime="text/csv",
        )

        # ── Record detail + edit / delete ─────────────────────────────────────
        st.divider()
        st.subheader("Record detail")

        # Guard: require a specific Unit + Tank to be selected before editing.
        if filter_system_h == "All" or filter_tank_h == "All":
            st.info(
                "Select a specific **Unit** and **Tank** above to view "
                "and edit a record."
            )
            return

        # Resolve the record for the selected Unit + Tank (+ optional Date).
        # _resolve_record picks the most-recent record when date is not pinned.
        date_pin = filter_date_h if filter_date_h != "All" else None
        rec = _resolve_record(grading_logs, filter_system_h, filter_tank_h, date_pin)

        if rec is None:
            st.info(
                f"No histogram records for **{filter_system_h}** / **{filter_tank_h}**."
            )
            return

        # When multiple dates exist and none is pinned, notify the user.
        tank_record_count = sum(
            1 for r in grading_logs
            if r.get("system_name") == filter_system_h
            and r.get("tank_name")  == filter_tank_h
        )
        if tank_record_count > 1 and date_pin is None:
            st.caption(
                f"Multiple records for this tank — showing most recent "
                f"(**{rec.get('date', '—')}**). "
                "Use the **Date** filter above to select a specific record."
            )

        # Stable widget-key scoped to this record so that switching Unit/Tank/Date
        # forces Streamlit to create fresh widgets from the new record's values.
        _rec_key = rec.get("id") or (
            f"{rec.get('date', '')}"
            f"__{rec.get('system_name', '')}"
            f"__{rec.get('tank_id') or rec.get('tank_name', '')}"
        )

        weights_raw = rec.get("weights") or rec.get("weights_g") or []
        small_thr   = rec.get("small_threshold_g",   0)
        harvest_thr = rec.get("harvest_threshold_g", 0)

        # ── Detail view ───────────────────────────────────────────────────────
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Samples",    rec.get("sample_count", "—"))
        d2.metric("Avg weight", f"{rec.get('avg_weight_g', '—')} g")
        d3.metric("Min",        f"{rec.get('min_weight_g', '—')} g")
        d4.metric("Max",        f"{rec.get('max_weight_g', '—')} g")

        p1, p2, p3 = st.columns(3)
        p1.metric(f"Small  (<{small_thr} g)",               f"{rec.get('small_pct', '—')} %")
        p2.metric(f"Medium  ({small_thr}–{harvest_thr} g)", f"{rec.get('medium_pct', '—')} %")
        p3.metric(f"Harvest ready  (≥{harvest_thr} g)",     f"{rec.get('harvest_ready_pct', '—')} %")

        if weights_raw:
            st.markdown(f"**Raw weights ({len(weights_raw)} values):**")
            st.text(", ".join(str(w) for w in weights_raw))
        else:
            st.caption("No raw weights stored for this record.")

        st.markdown(
            f"**Operator:** {rec.get('operator') or '—'}  |  "
            f"**Notes:** {rec.get('notes') or '—'}"
        )

        # ── Historical PDF ────────────────────────────────────────────────────
        if weights_raw:
            if st.button("Generate historical PDF", key="hist_pdf_btn"):
                hist_results = {
                    "sample_count":       rec.get("sample_count"),
                    "avg_weight_g":       rec.get("avg_weight_g"),
                    "min_weight_g":       rec.get("min_weight_g"),
                    "max_weight_g":       rec.get("max_weight_g"),
                    "small_pct":          rec.get("small_pct"),
                    "medium_pct":         rec.get("medium_pct"),
                    "harvest_ready_pct":  rec.get("harvest_ready_pct"),
                }
                pdf_buf = _generate_histogram_pdf(
                    farm_name=rec.get("farm_name") or farm_name,
                    system_name=rec.get("system_name", ""),
                    tank_name=rec.get("tank_name", ""),
                    log_date=rec.get("date", ""),
                    operator=rec.get("operator", ""),
                    weights=[float(w) for w in weights_raw],
                    small_threshold=float(small_thr),
                    harvest_threshold=float(harvest_thr),
                    results=hist_results,
                    notes=rec.get("notes", ""),
                )
                safe_system = str(rec.get("system_name", "")).replace(" ", "_")
                safe_tank   = str(rec.get("tank_name",   "")).replace(" ", "_")
                rec_date    = rec.get("date", "")
                st.download_button(
                    "Download historical PDF",
                    pdf_buf,
                    file_name=f"histogram_report_{rec_date}_{safe_system}_{safe_tank}.pdf",
                    mime="application/pdf",
                    key="hist_pdf_download",
                )
        else:
            st.caption("No raw weights stored — PDF cannot be generated for this record.")

        # ── Edit form ─────────────────────────────────────────────────────────
        # Unit and Tank are now record selectors (the filters above), not
        # editable assignment fields.  Only data fields are editable here.
        st.divider()
        st.subheader("Edit record")
        st.caption(
            f"**Editing:** {rec.get('system_name', '—')} / "
            f"{rec.get('tank_name', '—')} / {rec.get('date', '—')}"
        )

        try:
            edit_date_default = date.fromisoformat(rec.get("date", str(date.today())))
        except ValueError:
            edit_date_default = date.today()

        edit_date = st.date_input(
            "Date", value=edit_date_default, key=f"hist_edit_date_{_rec_key}"
        )

        ec1, ec2 = st.columns(2)
        edit_small_thr = ec1.number_input(
            "Small threshold (g)",
            min_value=0.0,
            value=float(rec.get("small_threshold_g") or 50.0),
            step=1.0,
            format="%.1f",
            key=f"hist_edit_small_thr_{_rec_key}",
        )
        edit_harvest_thr = ec2.number_input(
            "Harvest-ready threshold (g)",
            min_value=0.0,
            value=float(rec.get("harvest_threshold_g") or 300.0),
            step=5.0,
            format="%.1f",
            key=f"hist_edit_harvest_thr_{_rec_key}",
        )

        edit_operator = st.text_input(
            "Operator", value=rec.get("operator", ""),
            key=f"hist_edit_operator_{_rec_key}"
        )
        edit_notes = st.text_area(
            "Notes", value=rec.get("notes", ""),
            key=f"hist_edit_notes_{_rec_key}"
        )

        edit_raw_weights = st.text_area(
            "Weights (g) — edit or replace values",
            value=", ".join(str(w) for w in weights_raw),
            height=120,
            key=f"hist_edit_weights_{_rec_key}",
        )

        edit_weights = _parse_weights(edit_raw_weights) if edit_raw_weights.strip() else []

        # Live recalculation preview
        if edit_weights:
            edit_result = _analyse(edit_weights, edit_small_thr, edit_harvest_thr)
            st.caption(
                f"Recalculated: {edit_result['sample_count']} samples · "
                f"avg {edit_result['avg_weight_g']} g · "
                f"Small {edit_result['small_pct']} % · "
                f"Medium {edit_result['medium_pct']} % · "
                f"Harvest ready {edit_result['harvest_ready_pct']} %"
            )
        else:
            edit_result = {}
            st.caption("No valid weights — enter values above before saving.")

        btn_col1, btn_col2 = st.columns(2)

        if btn_col1.button("Save changes", type="primary", key="hist_save_btn"):
            if not edit_weights:
                st.error("Cannot save — no valid weights entered.")
            else:
                # Unit and Tank are frozen from the original record —
                # editing does not move a histogram to a different tank.
                updated_record = {
                    "date":                str(edit_date),
                    "farm_name":           rec.get("farm_name", farm_name),
                    "system_name":         rec.get("system_name"),
                    "tank_id":             rec.get("tank_id", ""),
                    "tank_name":           rec.get("tank_name", ""),
                    "operator":            edit_operator.strip(),
                    "notes":               edit_notes.strip(),
                    "sample_count":        edit_result["sample_count"],
                    "avg_weight_g":        edit_result["avg_weight_g"],
                    "min_weight_g":        edit_result["min_weight_g"],
                    "max_weight_g":        edit_result["max_weight_g"],
                    "small_threshold_g":   edit_small_thr,
                    "harvest_threshold_g": edit_harvest_thr,
                    "small_count":         edit_result["small_count"],
                    "medium_count":        edit_result["medium_count"],
                    "harvest_ready_count": edit_result["harvest_ready_count"],
                    "small_pct":           edit_result["small_pct"],
                    "medium_pct":          edit_result["medium_pct"],
                    "harvest_ready_pct":   edit_result["harvest_ready_pct"],
                    "weights":             edit_weights,
                }

                original_date    = rec.get("date")
                original_tank_id = rec.get("tank_id")
                original_tank_nm = rec.get("tank_name")

                replaced = False
                new_logs = []
                for existing in grading_logs:
                    if not replaced and _matches_record(
                        existing, original_date, original_tank_id, original_tank_nm
                    ):
                        new_logs.append(updated_record)
                        replaced = True
                    else:
                        new_logs.append(existing)

                storage.save_grading_logs(new_logs)
                storage.log_activity(
                    module="Histograms",
                    action="update",
                    summary=(
                        f"Updated histogram for tank {rec.get('tank_name', '')}, "
                        f"date {str(edit_date)}"
                    ),
                    date=str(edit_date),
                    system_name=rec.get("system_name", ""),
                    tank_id=str(rec.get("tank_id", "")),
                    tank_name=rec.get("tank_name", ""),
                    operator=edit_operator.strip(),
                )
                st.success("Record updated.")
                st.rerun()

        if btn_col2.button("Delete histogram", key="hist_delete_btn"):
            original_date    = rec.get("date")
            original_tank_id = rec.get("tank_id")
            original_tank_nm = rec.get("tank_name")

            removed = False
            new_logs = []
            for existing in grading_logs:
                if not removed and _matches_record(
                    existing, original_date, original_tank_id, original_tank_nm
                ):
                    removed = True  # skip = delete
                else:
                    new_logs.append(existing)

            storage.save_grading_logs(new_logs)
            storage.log_activity(
                module="Histograms",
                action="delete",
                summary=(
                    f"Deleted histogram for tank {rec.get('tank_name', '—')} "
                    f"on {rec.get('date', '—')}"
                ),
                date=rec.get("date", ""),
                system_name=rec.get("system_name", ""),
                tank_name=rec.get("tank_name", ""),
            )
            st.success("Record deleted.")
            st.rerun()
