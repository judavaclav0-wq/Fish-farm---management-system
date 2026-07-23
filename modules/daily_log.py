"""
modules/daily_log.py
Daily Report — enter daily farm measurements and per-tank production data.

Entry tab: one persistent report per date + system. Automatically prefills
from the existing report when one already exists, so users can add data
throughout the shift without navigating to History.
"""

import streamlit as st
import pandas as pd
from datetime import date
from core import storage
from core.stock_engine import compute_farm_state
from core.calculations import get_tank_fish_count, is_tank_occupied

# PDF
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from io import BytesIO


# ── Treatment configuration ────────────────────────────────────────────────────

TREATMENT_OPTIONS = ["None", "Formalin", "Wofa", "Halamid", "Vaccine", "Salt"]

TREATMENT_UNITS = {
    "None":     "",
    "Formalin": "l",
    "Wofa":     "l",
    "Halamid":  "g",
    "Vaccine":  "l",
    "Salt":     "kg",
}


def _treatments_str(treatment_type: str, treatment_amount: float, treatment_unit: str) -> str:
    """Build a backward-compatible treatments string, e.g. 'Wofa 2.0 l'."""
    if not treatment_type or treatment_type == "None":
        return ""
    return f"{treatment_type} {treatment_amount} {treatment_unit}".strip()


def _normalize_treatments(record: dict) -> list[dict]:
    """Return a list of treatment dicts from any record format (new or legacy)."""
    tl = record.get("treatments_list")
    if isinstance(tl, list):
        return [t for t in tl if isinstance(t, dict) and t.get("type") and t["type"] != "None"]
    t_type = record.get("treatment_type", "None") or "None"
    if t_type and t_type != "None":
        return [{
            "type":   t_type,
            "amount": float(record.get("treatment_amount", 0.0) or 0.0),
            "unit":   record.get("treatment_unit", TREATMENT_UNITS.get(t_type, "")) or "",
        }]
    return []


# ── PDF ────────────────────────────────────────────────────────────────────────

def _generate_pdf(
    farm_name, system, log_date, operator,
    tank_states, entries, measurements, summary,
    treatments, notes,
    chemicals=None,
    treatments_list=None,
):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer)

    styles = getSampleStyleSheet()
    elements = []

    # ── Header ─────────────────────────────────────────────
    elements.append(Paragraph("<b>Daily Report</b>", styles["Title"]))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"Farm: {farm_name}",   styles["Normal"]))
    elements.append(Paragraph(f"Unit: {system}",       styles["Normal"]))
    elements.append(Paragraph(f"Date: {log_date}",     styles["Normal"]))
    elements.append(Paragraph(f"Operator: {operator}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    # ── Measurements ───────────────────────────────────────
    elements.append(Paragraph("<b>Measurements</b>", styles["Heading3"]))
    for k, v in measurements.items():
        elements.append(Paragraph(f"{k.upper()}: {v}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    # ── Chemicals / Treatments ─────────────────────────────
    elements.append(Paragraph("<b>Chemicals / Treatments</b>", styles["Heading3"]))
    ch = chemicals or {}
    bic_kg = ch.get("bicarbonate_kg", 0)
    elements.append(Paragraph(f"Bicarbonate: {bic_kg} kg", styles["Normal"]))
    t_list = treatments_list or []
    if t_list:
        for t in t_list:
            elements.append(Paragraph(
                f"Treatment: {t.get('type', '')} {t.get('amount', '')} {t.get('unit', '')}".strip(),
                styles["Normal"],
            ))
    else:
        t_type   = ch.get("treatment_type",   "None") or "None"
        t_amount = ch.get("treatment_amount", 0)
        t_unit   = ch.get("treatment_unit",   "")
        elements.append(Paragraph(f"Treatment: {t_type}", styles["Normal"]))
        if t_type != "None":
            elements.append(Paragraph(f"Amount: {t_amount} {t_unit}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    # ── Tank table ─────────────────────────────────────────
    elements.append(Paragraph("<b>Tank data</b>", styles["Heading3"]))

    table_data = [["Tank", "Fish", "Biomass", "Density", "Feed", "O2", "Mortality"]]

    state_map = {s["tank_id"]: s for s in tank_states}

    for e in entries:
        s = state_map.get(e["tank_id"], {})
        table_data.append([
            e["tank_name"],
            s.get("fish_count", 0),
            round(s.get("biomass_kg", 0), 1),
            round(s.get("density_kg_m3", 0), 2),
            e.get("feed_kg", 0),
            e.get("oxygen", 0),
            e["mortality_fish"],
        ])

    table = Table(table_data)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 12))

    # ── Mortality summary ──────────────────────────────────
    elements.append(Paragraph("<b>Mortality summary</b>", styles["Heading3"]))
    elements.append(Paragraph(f"Lowest: {summary['lowest']} ({summary['lowest_tank']})",   styles["Normal"]))
    elements.append(Paragraph(f"Average: {round(summary['average'], 1)}",                  styles["Normal"]))
    elements.append(Paragraph(f"Highest: {summary['highest']} ({summary['highest_tank']})", styles["Normal"]))
    elements.append(Spacer(1, 12))

    # ── Notes ──────────────────────────────────────────────
    elements.append(Paragraph("<b>Notes</b>", styles["Heading3"]))
    elements.append(Paragraph(notes or "-", styles["Normal"]))

    doc.build(elements)
    buffer.seek(0)
    return buffer


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mortality_summary(tank_entries: list[dict]) -> dict:
    if not tank_entries:
        return {
            "lowest": 0, "lowest_tank": "—",
            "average": 0,
            "highest": 0, "highest_tank": "—",
            "tank_count": 0,
        }

    lowest  = min(tank_entries, key=lambda x: x["mortality_fish"])
    highest = max(tank_entries, key=lambda x: x["mortality_fish"])
    avg     = sum(x["mortality_fish"] for x in tank_entries) / len(tank_entries)

    return {
        "lowest":       lowest["mortality_fish"],
        "lowest_tank":  lowest["tank_name"],
        "average":      avg,
        "highest":      highest["mortality_fish"],
        "highest_tank": highest["tank_name"],
        "tank_count":   len(tank_entries),
    }


def _system_measurements_from_logs(entries: list[dict]) -> dict:
    # These are handled separately — exclude from the WQ parameter grid.
    skip_cols = {
        "date", "farm_name", "farm_id", "system_name", "operator",
        "tank_id", "tank_name", "feed_kg", "oxygen", "mortality_fish",
        "treatments", "treatments_list", "notes",
        "bicarbonate_kg", "treatment_type", "treatment_amount", "treatment_unit",
    }

    measurements = {}

    for entry in entries:
        for key, value in entry.items():
            if key not in skip_cols and isinstance(value, (int, float)):
                measurements[key] = value

    return measurements


def _apply_mortality_to_farm(
    entries: list[dict],
    prev_by_tank: dict[str, dict],
) -> None:
    """Update farm_state fish_count for the net change in logged mortality.

    Called after saving daily log rows.  `prev_by_tank` maps tank_id to the
    log row that existed before this save (empty dict if none).  Only the
    *delta* (new − old) is applied so that edits to an existing report don't
    double-subtract.
    """
    farm    = storage.load_farm()
    changed = False
    for entry in entries:
        tid   = entry["tank_id"]
        new   = int(entry.get("mortality_fish", 0))
        old   = int(prev_by_tank.get(tid, {}).get("mortality_fish", 0))
        delta = new - old
        if delta == 0:
            continue
        for system in farm.get("systems", []):
            for tank in system.get("tanks", []):
                if tank.get("id") == tid:
                    tank["fish_count"] = max(
                        0.0, float(tank.get("fish_count", 0)) - delta
                    )
                    changed = True
    if changed:
        storage.save_farm(farm)


# ── Main render ───────────────────────────────────────────────────────────────

def render() -> None:
    st.header("Daily report")

    farm        = storage.load_farm()
    batches     = storage.load_batches()
    logs        = storage.load_daily_logs()
    movements   = storage.load_movements()
    adjustments = storage.load_adjustments()

    tank_states = compute_farm_state(farm, batches, logs, movements, adjustments=adjustments)

    if not tank_states:
        st.warning("No tanks defined. Go to **Farm Setup** first.")
        return

    tab_entry, tab_history = st.tabs(["Entry", "History"])

    # ═══════════════════════════════════════════════════════════════════════════
    # ENTRY TAB
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_entry:
        today        = date.today()
        log_date     = st.date_input("Date", value=today, max_value=today)
        log_date_str = str(log_date)

        farm_name = farm.get("farm_name", "Farm")
        farm_id   = farm.get("id", "")

        system_names    = sorted({s.get("system_name", "—") for s in tank_states})
        selected_system = st.selectbox("Unit", options=system_names)

        # ── Look up existing report for this date + system ─────────────────
        existing_entries = [
            l for l in logs
            if l.get("date") == log_date_str and l.get("system_name") == selected_system
        ]
        report_exists    = bool(existing_entries)
        existing_by_tank = {e["tank_id"]: e for e in existing_entries}
        first_existing   = existing_entries[0] if existing_entries else {}

        if report_exists:
            st.info(
                "Daily report for this date and unit already exists. "
                "You are updating the existing report."
            )
        else:
            st.info("No report exists for this date and unit. You are creating a new report.")

        # ── Operator ───────────────────────────────────────────────────────
        operator = st.text_input(
            "Operator",
            value=first_existing.get("operator", ""),
            key=f"operator_{selected_system}_{log_date_str}",
        )

        visible_tanks = [
            s for s in tank_states
            if s.get("system_name") == selected_system
        ]

        # ── Dynamic WQ params from system thresholds ───────────────────────
        system_data = next(
            (s for s in farm.get("systems", []) if s.get("name") == selected_system),
            None,
        )
        thresholds = system_data.get("thresholds", {}) if system_data else {}

        measurements = {}
        for param, cfg in thresholds.items():
            if cfg.get("active"):
                prefill_val = float(first_existing.get(param, 0.0)) if report_exists else 0.0
                measurements[param] = st.number_input(
                    param.upper(),
                    value=prefill_val,
                    key=f"{param}_{selected_system}_{log_date_str}",
                )

        # ── Bicarbonate (system-level) ─────────────────────────────────────
        bicarbonate_kg = st.number_input(
            "Bicarbonate (kg)",
            min_value=0.0,
            value=float(first_existing.get("bicarbonate_kg", 0.0)) if report_exists else 0.0,
            step=0.5,
            format="%.2f",
            key=f"bicarbonate_{selected_system}_{log_date_str}",
        )

        # ── Treatments (system-level, multiple) ───────────────────────────
        st.markdown("**Treatments**")
        pending_key = f"_dl_pend_tx_{selected_system}_{log_date_str}"

        if pending_key not in st.session_state:
            st.session_state[pending_key] = (
                _normalize_treatments(first_existing) if report_exists else []
            )

        pending_treatments = st.session_state[pending_key]

        if pending_treatments:
            for i, t in enumerate(pending_treatments):
                tc1, tc2 = st.columns([5, 1])
                tc1.write(f"**{t['type']}** — {t['amount']} {t['unit']}")
                if tc2.button("✕", key=f"rm_t_{i}_{pending_key}"):
                    st.session_state[pending_key].pop(i)
                    st.rerun()
        else:
            st.caption("No treatments added.")

        with st.expander("Add treatment", expanded=not bool(pending_treatments)):
            add_ttype = st.selectbox(
                "Treatment type",
                options=[o for o in TREATMENT_OPTIONS if o != "None"],
                key=f"add_ttype_{selected_system}_{log_date_str}",
            )
            add_unit = TREATMENT_UNITS.get(add_ttype, "")
            add_amount = float(st.number_input(
                f"Amount ({add_unit})" if add_unit else "Amount",
                min_value=0.01,
                value=0.10,
                step=0.1,
                format="%.2f",
                key=f"add_tamount_{selected_system}_{log_date_str}",
            ))
            if st.button("Add treatment", key=f"add_tbtn_{selected_system}_{log_date_str}"):
                if add_amount <= 0:
                    st.error("Amount must be greater than zero.")
                else:
                    st.session_state[pending_key].append(
                        {"type": add_ttype, "amount": add_amount, "unit": add_unit}
                    )
                    st.rerun()

        st.divider()

        # ── Per-tank entries ───────────────────────────────────────────────
        tank_entries = []

        for state in visible_tanks:
            tank_id      = state["tank_id"]
            tank_name    = state.get("tank_name", "—")
            ex_tank      = existing_by_tank.get(tank_id, {})
            current_fish = get_tank_fish_count(state)

            st.markdown(f"**{tank_name}**")

            # Empty tank: no inputs, skip from tank_entries entirely.
            # Any existing historical log row for this tank is preserved
            # in storage because its tank_id won't appear in the save filter.
            if not is_tank_occupied(state):
                st.caption("Tank is empty — O2 and mortality entry disabled.")
                continue

            col1, col2, col3 = st.columns(3)

            prefill_o2   = float(ex_tank.get("oxygen", 0.0))    if ex_tank else 0.0
            prefill_mort = int(ex_tank.get("mortality_fish", 0)) if ex_tank else 0
            prefill_feed = float(ex_tank.get("feed_kg", state.get("feed_kg_day", 0.0)))

            oxygen    = col1.number_input(
                "O2",
                value=prefill_o2,
                key=f"o2_{tank_id}_{log_date_str}",
            )
            mortality = col2.number_input(
                "Mortality",
                value=prefill_mort,
                step=1,
                key=f"mort_{tank_id}_{log_date_str}",
            )
            feed_kg   = col3.number_input(
                "Feed (kg)",
                min_value=0.0,
                value=prefill_feed,
                step=0.1,
                format="%.2f",
                key=f"feed_{tank_id}_{log_date_str}",
            )

            entry = {
                "date":             log_date_str,
                "farm_name":        farm_name,
                "farm_id":          farm_id,
                "system_name":      selected_system,
                "operator":         operator.strip(),
                "tank_id":          tank_id,
                "tank_name":        tank_name,
                "feed_kg":          float(feed_kg),
                "oxygen":           float(oxygen),
                "mortality_fish":   int(mortality),
                # Chemical fields — same for every tank row in this system/date
                "bicarbonate_kg":   float(bicarbonate_kg),
                "treatments_list":  pending_treatments,
                # Backward-compat scalar fields (first treatment only)
                "treatment_type":   pending_treatments[0]["type"]   if pending_treatments else "None",
                "treatment_amount": pending_treatments[0]["amount"] if pending_treatments else 0.0,
                "treatment_unit":   pending_treatments[0]["unit"]   if pending_treatments else "",
                "treatments": " | ".join(
                    f"{t['type']} {t['amount']} {t['unit']}" for t in pending_treatments
                ) if pending_treatments else "",
            }

            for p, v in measurements.items():
                entry[p] = float(v)

            tank_entries.append(entry)

        summary = _mortality_summary(tank_entries)

        notes = st.text_area(
            "Notes",
            value=first_existing.get("notes", "") if report_exists else "",
            key=f"notes_{selected_system}_{log_date_str}",
        )

        for entry in tank_entries:
            entry["notes"] = notes.strip()

        col_save, col_pdf = st.columns(2)

        btn_label = "Update daily report" if report_exists else "Save daily report"
        btn_type  = "primary" if report_exists else "secondary"

        if col_save.button(btn_label, type=btn_type):
            if log_date > today:
                st.error("Cannot save a report for a future date.")
            else:
                # ── Pre-save concurrency check ─────────────────────────────
                # Reload current farm state so we catch any fish movements that
                # happened while this form was open.
                latest_farm      = storage.load_farm()
                latest_batches   = storage.load_batches()
                latest_logs      = storage.load_daily_logs()
                latest_movements = storage.load_movements()
                latest_adj       = storage.load_adjustments()
                latest_states    = compute_farm_state(
                    latest_farm, latest_batches, latest_logs, latest_movements,
                    adjustments=latest_adj,
                )
                latest_state_map = {s["tank_id"]: s for s in latest_states}

                stale_tanks = []
                for entry in tank_entries:
                    current = latest_state_map.get(entry["tank_id"], {})
                    if not is_tank_occupied(current) and entry.get("mortality_fish", 0) > 0:
                        stale_tanks.append(entry.get("tank_name", entry["tank_id"]))

                if stale_tanks:
                    st.error(
                        "Tank stock changed while the Daily Log was open — "
                        f"{', '.join(stale_tanks)} are now empty. "
                        "Please review and submit again."
                    )
                else:
                    # Capture previous mortality per tank for delta calculation.
                    prev_by_tank = {
                        l["tank_id"]: l
                        for l in latest_logs
                        if l.get("date") == log_date_str
                        and l.get("system_name") == selected_system
                    }

                    # Upsert: remove existing rows for this date + system + these tanks,
                    # then append the current entries.
                    remaining = [
                        l for l in latest_logs
                        if not (
                            l.get("date") == log_date_str
                            and l.get("system_name") == selected_system
                            and l.get("tank_id") in {e["tank_id"] for e in tank_entries}
                        )
                    ]
                    storage.save_daily_logs(remaining + tank_entries)

                    # Update farm_state fish_count for the net mortality change.
                    _apply_mortality_to_farm(tank_entries, prev_by_tank)

                    action = "update" if report_exists else "create"
                    verb   = "Updated" if report_exists else "Created"
                    storage.log_activity(
                        module="Daily Log",
                        action=action,
                        summary=f"{verb} daily report for {selected_system} with {len(tank_entries)} tank entries",
                        date=log_date_str,
                        system_name=selected_system,
                        operator=operator.strip(),
                        details={"tank_count": len(tank_entries)},
                    )
                    st.session_state.pop(pending_key, None)
                    st.success("Updated." if report_exists else "Saved.")
                    st.rerun()

        if col_pdf.button("Generate PDF"):
            states_today = compute_farm_state(
                farm, batches, logs + tank_entries, movements,
                as_of_date=log_date_str,
            )

            chemicals = {
                "bicarbonate_kg": float(bicarbonate_kg),
            }

            pdf = _generate_pdf(
                farm_name, selected_system, log_date_str, operator,
                states_today, tank_entries, measurements, summary,
                "", notes,
                chemicals=chemicals,
                treatments_list=pending_treatments,
            )

            st.download_button(
                "Download PDF",
                pdf,
                file_name=f"daily_report_{log_date}.pdf",
                mime="application/pdf",
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # HISTORY TAB
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_history:
        if not logs:
            st.info("No log entries yet.")
            return

        df = pd.DataFrame(logs)

        # Fill missing chemical columns for backward compat with old records
        for col_name, default in [
            ("bicarbonate_kg",   0.0),
            ("treatment_type",   "None"),
            ("treatment_amount", 0.0),
            ("treatment_unit",   ""),
            ("treatments",       ""),
        ]:
            if col_name not in df.columns:
                df[col_name] = default
            else:
                df[col_name] = df[col_name].fillna(default)

        # ── Summary table ──────────────────────────────────────────────────
        # Chemical fields are system-level: always use "first", never sum.
        summary_df = (
            df
            .groupby(["date", "system_name"], dropna=False)
            .agg(
                farm_name=       ("farm_name",        "first"),
                operator=        ("operator",          "first"),
                tanks=           ("tank_id",           "nunique"),
                total_feed_kg=   ("feed_kg",           "sum"),
                avg_oxygen=      ("oxygen",            "mean"),
                total_mortality= ("mortality_fish",    "sum"),
                bicarbonate_kg=  ("bicarbonate_kg",    "first"),
                treatment_type=  ("treatment_type",    "first"),
                treatment_amount=("treatment_amount",  "first"),
                treatment_unit=  ("treatment_unit",    "first"),
                treatments=      ("treatments",        "first"),
                notes=           ("notes",             "first"),
            )
            .reset_index()
            .sort_values(["date", "system_name"], ascending=[False, True])
        )

        st.dataframe(summary_df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Edit / Delete / PDF")

        available_dates = sorted(df["date"].dropna().unique(), reverse=True)
        selected_date   = st.selectbox("Select date", available_dates, key="history_edit_date")

        date_logs    = [log for log in logs if log.get("date") == selected_date]
        date_systems = sorted({log.get("system_name", "—") for log in date_logs})
        selected_system = st.selectbox("Select unit", date_systems, key="history_edit_system")

        selected_entries = [
            log for log in date_logs
            if log.get("system_name", "—") == selected_system
        ]

        if not selected_entries:
            st.info("No entries for selected date/unit.")
            return

        farm_name = selected_entries[0].get("farm_name") or farm.get("farm_name", "Farm")
        operator  = selected_entries[0].get("operator", "")
        notes     = selected_entries[0].get("notes", "")

        # Read system-level chemical fields from first entry (same on all tank rows)
        saved_bicarbonate_kg = float(selected_entries[0].get("bicarbonate_kg", 0) or 0)

        edit_tx_key = f"_dl_edit_tx_{selected_date}_{selected_system}"
        if edit_tx_key not in st.session_state:
            st.session_state[edit_tx_key] = _normalize_treatments(selected_entries[0])

        st.markdown("### Selected daily report")

        # ── System water parameters ────────────────────────────────────────
        system_measurements = _system_measurements_from_logs(selected_entries)

        if system_measurements:
            st.markdown("### System water parameters")

            edited_measurements = {}
            params = list(system_measurements.items())

            for i in range(0, len(params), 3):
                cols = st.columns(3)
                for col, (param, value) in zip(cols, params[i:i+3]):
                    edited_measurements[param] = float(col.number_input(
                        param.upper(),
                        value=float(value),
                        key=f"edit_system_{param}_{selected_date}_{selected_system}",
                    ))
        else:
            edited_measurements = {}

        # ── Chemicals / Treatments (system-level edit) ─────────────────────
        st.markdown("### Chemicals / Treatments")

        edit_bicarbonate_kg = float(st.number_input(
            "Bicarbonate (kg)",
            min_value=0.0,
            value=saved_bicarbonate_kg,
            step=0.5,
            format="%.2f",
            key=f"edit_bicarbonate_{selected_date}_{selected_system}",
        ))

        st.markdown("**Treatments**")
        edit_treatments = st.session_state[edit_tx_key]

        if edit_treatments:
            for i, t in enumerate(edit_treatments):
                etx1, etx2 = st.columns([5, 1])
                etx1.write(f"**{t['type']}** — {t['amount']} {t['unit']}")
                if etx2.button("✕", key=f"rm_edit_t_{i}_{edit_tx_key}"):
                    st.session_state[edit_tx_key].pop(i)
                    st.rerun()
        else:
            st.caption("No treatments.")

        with st.expander("Add treatment", expanded=not bool(edit_treatments)):
            add_ttype_e = st.selectbox(
                "Treatment type",
                options=[o for o in TREATMENT_OPTIONS if o != "None"],
                key=f"add_ttype_e_{selected_date}_{selected_system}",
            )
            add_unit_e = TREATMENT_UNITS.get(add_ttype_e, "")
            add_amount_e = float(st.number_input(
                f"Amount ({add_unit_e})" if add_unit_e else "Amount",
                min_value=0.01,
                value=0.10,
                step=0.1,
                format="%.2f",
                key=f"add_tamount_e_{selected_date}_{selected_system}",
            ))
            if st.button("Add treatment", key=f"add_tbtn_e_{selected_date}_{selected_system}"):
                if add_amount_e <= 0:
                    st.error("Amount must be greater than zero.")
                else:
                    st.session_state[edit_tx_key].append(
                        {"type": add_ttype_e, "amount": add_amount_e, "unit": add_unit_e}
                    )
                    st.rerun()

        # ── Tank data ──────────────────────────────────────────────────────
        st.markdown("### Tank data")

        edited_entries = []

        for entry in selected_entries:
            tank_id   = entry.get("tank_id")
            tank_name = entry.get("tank_name", "—")

            with st.expander(tank_name, expanded=False):
                c1, c2, c3 = st.columns(3)

                new_oxygen = c1.number_input(
                    "O2",
                    value=float(entry.get("oxygen", 0.0)),
                    key=f"edit_o2_{selected_date}_{tank_id}",
                )

                new_mortality = c2.number_input(
                    "Mortality",
                    min_value=0,
                    value=int(entry.get("mortality_fish", 0)),
                    step=1,
                    key=f"edit_mort_{selected_date}_{tank_id}",
                )

                new_feed = c3.number_input(
                    "Feed kg",
                    min_value=0.0,
                    value=float(entry.get("feed_kg", 0.0)),
                    step=0.1,
                    format="%.2f",
                    key=f"edit_feed_{selected_date}_{tank_id}",
                )

                edited_entry = dict(entry)
                edited_entry["oxygen"]        = float(new_oxygen)
                edited_entry["mortality_fish"] = int(new_mortality)
                edited_entry["feed_kg"]       = float(new_feed)

                for param, value in edited_measurements.items():
                    edited_entry[param] = float(value)

                edited_entries.append(edited_entry)

        # ── Report notes ───────────────────────────────────────────────────
        st.markdown("### Report notes")

        edit_operator = st.text_input(
            "Operator",
            value=operator,
            key=f"edit_operator_{selected_date}_{selected_system}",
        )

        edit_notes = st.text_area(
            "Notes",
            value=notes,
            key=f"edit_notes_{selected_date}_{selected_system}",
        )

        # Apply system-level fields to every entry
        edit_treatments  = st.session_state[edit_tx_key]
        first_edit_tx    = edit_treatments[0] if edit_treatments else {}

        for entry in edited_entries:
            entry["operator"]         = edit_operator.strip()
            entry["notes"]            = edit_notes.strip()
            entry["bicarbonate_kg"]   = edit_bicarbonate_kg
            entry["treatments_list"]  = edit_treatments
            entry["treatment_type"]   = first_edit_tx.get("type",   "None") if first_edit_tx else "None"
            entry["treatment_amount"] = first_edit_tx.get("amount", 0.0)    if first_edit_tx else 0.0
            entry["treatment_unit"]   = first_edit_tx.get("unit",   "")     if first_edit_tx else ""
            entry["treatments"] = " | ".join(
                f"{t['type']} {t['amount']} {t['unit']}" for t in edit_treatments
            ) if edit_treatments else ""

        col_save, col_delete, col_pdf = st.columns(3)

        if col_save.button("Save changes", type="primary"):
            remaining_logs = [
                log for log in logs
                if not (
                    log.get("date") == selected_date
                    and log.get("system_name", "—") == selected_system
                    and log.get("tank_id") in {e["tank_id"] for e in edited_entries}
                )
            ]
            storage.save_daily_logs(remaining_logs + edited_entries)
            storage.log_activity(
                module="Daily Log",
                action="update",
                summary=f"Updated daily report for {selected_system} on {selected_date}",
                date=selected_date,
                system_name=selected_system,
                operator=edit_operator.strip(),
                details={"tank_count": len(edited_entries)},
            )
            st.session_state.pop(edit_tx_key, None)
            st.success("Daily report updated.")
            st.rerun()

        if col_delete.button("Delete selected report"):
            remaining_logs = [
                log for log in logs
                if not (
                    log.get("date") == selected_date
                    and log.get("system_name", "—") == selected_system
                )
            ]
            storage.save_daily_logs(remaining_logs)
            storage.log_activity(
                module="Daily Log",
                action="delete",
                summary=f"Deleted daily report for {selected_system} on {selected_date}",
                date=selected_date,
                system_name=selected_system,
            )
            st.success("Daily report deleted.")
            st.rerun()

        if col_pdf.button("Generate historical PDF"):
            states_selected_date = compute_farm_state(
                farm, batches, logs, movements,
                as_of_date=selected_date,
            )

            hist_chemicals = {
                "bicarbonate_kg": edit_bicarbonate_kg,
            }

            pdf = _generate_pdf(
                farm_name, selected_system, selected_date, edit_operator,
                states_selected_date, edited_entries, edited_measurements,
                _mortality_summary(edited_entries),
                "", edit_notes,
                chemicals=hist_chemicals,
                treatments_list=edit_treatments,
            )

            st.download_button(
                "Download historical PDF",
                pdf,
                file_name=f"daily_report_{selected_date}_{selected_system}.pdf",
                mime="application/pdf",
            )
