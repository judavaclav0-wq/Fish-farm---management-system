"""
modules/batches.py
Batch Management — create, monitor and archive fish batches.

Batch composition model
-----------------------
A batch is not tied to a single tank.  After transfers, the same batch
can be distributed across multiple tanks.  All live numbers here are
computed by compute_batch_summary() in stock_engine, which replays every
movement and mortality event in date order to produce an accurate per-batch
fish count, mortality, and tank distribution.

Vaccination model
-----------------
Each batch carries a vaccination_schedule list of planned/completed
vaccination events.  The legacy vaccination_due_day / vaccination_done fields
are kept in the dict for backward compatibility but the UI is driven by
vaccination_schedule when that list is present.

Edit / archive model
--------------------
Batches are never hard-deleted.  Setting status="archived" hides a batch
from all active-batch selectors while preserving every historical reference
(tank initial_batch_composition, movements, daily logs).

Role access
-----------
Admin / Manager — full read/write.
Viewer — read-only: sees overview table, batch cards (info only), and
         distribution table.  All create/edit/archive/vaccination controls
         are hidden.
Operator — no access (blocked at page level and in sidebar navigation).
"""

import streamlit as st
import pandas as pd
from datetime import date
from core import storage
from core.stock_engine import compute_batch_summary
from core.auth import can_write, get_current_user, require_role


# ── Helpers ────────────────────────────────────────────────────────────────────

def _days_after_hatch(hatch_date_str: str, today: date) -> int | None:
    """Return days since hatch, or None if hatch_date is missing/invalid."""
    if not hatch_date_str:
        return None
    try:
        return (today - date.fromisoformat(str(hatch_date_str))).days
    except (ValueError, TypeError):
        return None


def _next_planned_vaccination(batch: dict) -> dict | None:
    """Return the earliest unfinished vaccination entry, or None."""
    pending = [
        v for v in batch.get("vaccination_schedule", [])
        if not v.get("done", False) and v.get("date", "")
    ]
    return min(pending, key=lambda v: v["date"]) if pending else None


def _vacc_status(batch: dict, today: date) -> str:
    """
    Vaccination status string.
    Uses vaccination_schedule if present; falls back to legacy fields.
    """
    schedule = batch.get("vaccination_schedule", [])
    if schedule:
        next_v = _next_planned_vaccination(batch)
        if next_v is None:
            done_any = any(v.get("done") for v in schedule)
            return "✅ Done — no next date" if done_any else "No vaccination planned"
        try:
            next_date = date.fromisoformat(next_v["date"])
        except (ValueError, TypeError, KeyError):
            return "— invalid date"
        delta = (next_date - today).days
        if delta < 0:
            return f"🔴 Overdue ({abs(delta)}d)"
        if delta == 0:
            return "🔴 Due today"
        if delta <= 7:
            return f"🟠 Upcoming ({delta}d)"
        return f"🟢 Upcoming ({delta}d)"
    # ── Legacy fallback ──────────────────────────────────────────────────────
    if batch.get("vaccination_done", False):
        return "✅ Done"
    dah = _days_after_hatch(batch.get("hatch_date", ""), today)
    if dah is None:
        return "—"
    due_day    = int(batch.get("vaccination_due_day") or 60)
    days_until = due_day - dah
    if days_until <= 0:
        return "🔴 Due now!"
    if days_until <= 7:
        return f"🟠 Due soon ({days_until}d)"
    return f"🟢 Not due ({days_until}d)"


def _vacc_is_urgent(batch: dict, today: date) -> bool:
    """True when next vaccination is overdue or due within 7 days."""
    schedule = batch.get("vaccination_schedule", [])
    if schedule:
        next_v = _next_planned_vaccination(batch)
        if next_v is None:
            return False
        try:
            return (date.fromisoformat(next_v["date"]) - today).days <= 7
        except (ValueError, TypeError, KeyError):
            return False
    # Legacy fallback
    if batch.get("vaccination_done", False):
        return False
    dah = _days_after_hatch(batch.get("hatch_date", ""), today)
    if dah is None:
        return False
    due_day = int(batch.get("vaccination_due_day") or 60)
    return (due_day - dah) <= 7


def _parse_date_safe(date_str: str) -> date | None:
    """Parse an ISO date string, returning None on failure."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(str(date_str))
    except (ValueError, TypeError):
        return None


# ── Read-only card renderer (Viewer) ──────────────────────────────────────────

def _render_batch_card_readonly(batch: dict, sm: dict, today: date) -> None:
    """Render a batch card with no interactive controls for Viewer role."""
    bid            = batch.get("id", "")
    hatch_date_str = batch.get("hatch_date", "") or ""
    next_v         = _next_planned_vaccination(batch)
    vacc_status    = _vacc_status(batch, today)
    is_urgent      = _vacc_is_urgent(batch, today)

    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 2, 2])

        # Identity
        c1.markdown(f"**{batch['name']}** — {batch.get('species', '—')}")
        c1.caption(f"Start: {batch.get('start_date', '—')}")
        if hatch_date_str:
            dah = _days_after_hatch(hatch_date_str, today)
            c1.caption(f"Hatch: {hatch_date_str}  |  DAH: {dah} days")
        if batch.get("notes"):
            c1.caption(f"Notes: {batch['notes']}")

        # Stock numbers
        c2.metric("Current fish", f"{sm.get('current_fish', 0):,}")
        c2.caption(
            f"Initial: {batch.get('initial_fish_count', 0):,}  |  "
            f"Biomass: {sm.get('current_biomass_kg', 0):.1f} kg"
        )
        c2.caption(
            f"Mortality: {sm.get('total_mortality', 0):,}  "
            f"({sm.get('mortality_pct', 0.0):.1f}%)"
        )

        # Vaccination status (display only)
        if is_urgent:
            c3.warning(vacc_status)
        else:
            c3.caption(f"Vaccination: {vacc_status}")
        if next_v:
            c3.caption(f"📅 Next: {next_v['date']}")

        # Vaccination history — read-only view of completed entries
        schedule = batch.get("vaccination_schedule", [])
        if schedule:
            done_count  = sum(1 for v in schedule if v.get("done"))
            total_count = len(schedule)
            done_entries = [v for v in schedule if v.get("done")]
            if done_entries:
                with st.expander(
                    f"Vaccination history ({done_count}/{total_count} done)",
                    expanded=False,
                ):
                    hist_rows = []
                    for v in sorted(done_entries, key=lambda x: x.get("date", ""), reverse=True):
                        hist_rows.append({
                            "Planned date": v.get("date", "—"),
                            "Done date":    v.get("done_date") or "—",
                            "Notes":        v.get("notes") or "—",
                        })
                    st.dataframe(
                        pd.DataFrame(hist_rows),
                        use_container_width=True,
                        hide_index=True,
                    )


# ─────────────────────────────────────────────────────────────────────────────
def render() -> None:
    if not require_role(["Admin", "Manager", "Viewer"]):
        st.warning("You do not have permission to access Batch Management.")
        return

    st.header("Batch Management")

    farm      = storage.load_farm()
    batches   = storage.load_batches()
    logs      = storage.load_daily_logs()
    movements = storage.load_movements()

    today = date.today()

    batch_summary = compute_batch_summary(farm, batches, logs, movements)
    summary_by_id = {s["batch_id"]: s for s in batch_summary}

    write_ok = can_write("Batch Management")   # False for Viewer

    # ── Read-only banner ──────────────────────────────────────────────────────
    if not write_ok:
        st.info(
            "Read-only mode. Batch information is visible, editing is restricted.",
            icon="ℹ️",
        )

    # ══════════════════════════════════════════════════════════════════════════
    # CREATE NEW BATCH  (write roles only)
    # ══════════════════════════════════════════════════════════════════════════
    if write_ok:
        with st.expander("Create new batch", expanded=True):
            with st.form("create_batch", clear_on_submit=True):

                col1, col2 = st.columns(2)
                b_name    = col1.text_input("Batch name", placeholder="e.g. Salmon-2024-A")
                b_date    = col1.date_input("Start date", value=date.today())
                b_species = col2.text_input("Species", value="")

                col_h1, col_h2 = st.columns(2)
                b_hatch_date = col_h1.date_input(
                    "Hatch date (optional)",
                    value=None,
                    help="Leave blank if unknown. Drives Days-after-hatch.",
                )
                b_first_vacc = col_h2.date_input(
                    "First vaccination date (optional)",
                    value=None,
                    help="Planned date for the first vaccination. More dates can be added later.",
                )

                col3, col4, col5 = st.columns(3)
                b_fish    = col3.number_input("Initial fish count", min_value=1, value=1000, step=100)
                b_weight  = col4.number_input("Initial avg weight (g)", min_value=0.1, value=50.0, step=0.5)
                b_biomass = col5.number_input(
                    "Initial biomass (kg)",
                    min_value=0.0,
                    value=round(b_fish * b_weight / 1000, 2),
                    step=0.5,
                    help="Auto-calculated but editable",
                )

                b_notes   = st.text_area("Notes", height=68)
                submitted = st.form_submit_button("Create batch")

                if submitted:
                    if not b_name.strip():
                        st.error("Batch name is required.")
                    else:
                        vacc_schedule = (
                            [{
                                "id":        storage.new_id("vac_"),
                                "date":      str(b_first_vacc),
                                "done":      False,
                                "done_date": "",
                                "notes":     "",
                            }]
                            if b_first_vacc else []
                        )
                        new_batch = {
                            "id":                   storage.new_id("batch_"),
                            "name":                 b_name.strip(),
                            "species":              b_species.strip(),
                            "start_date":           str(b_date),
                            "hatch_date":           str(b_hatch_date) if b_hatch_date else "",
                            "vaccination_due_day":  60,
                            "vaccination_done":     False,
                            "vaccination_schedule": vacc_schedule,
                            "initial_fish_count":   int(b_fish),
                            "initial_avg_weight_g": float(b_weight),
                            "initial_biomass_kg":   float(b_biomass),
                            "status":               "active",
                            "notes":                b_notes.strip(),
                        }
                        batches.append(new_batch)
                        storage.save_batches(batches)
                        storage.log_activity(
                            module="Batch Management",
                            action="create",
                            summary=f"Created batch {b_name.strip()}",
                        )
                        st.success(f"Batch '{b_name.strip()}' created.")
                        st.rerun()

    # ══════════════════════════════════════════════════════════════════════════
    # ACTIVE BATCHES
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("Active batches")
    active = [b for b in batches if b.get("status") == "active"]

    if not active:
        st.info("No active batches." + (" Create one above." if write_ok else ""))
    else:

        # ── Batch overview table ───────────────────────────────────────────
        overview_rows = []
        for batch in active:
            bid            = batch.get("id", "")
            sm             = summary_by_id.get(bid, {})
            hatch_date_str = batch.get("hatch_date", "") or ""
            initial_fish   = int(batch.get("initial_fish_count", 0) or 0)
            dah            = _days_after_hatch(hatch_date_str, today)
            next_v         = _next_planned_vaccination(batch)
            vacc_status    = _vacc_status(batch, today)

            overview_rows.append({
                "Batch":          batch["name"],
                "Species":        batch.get("species", "—"),
                "Hatch date":     hatch_date_str or "—",
                "DAH":            dah if dah is not None else "—",
                "Next vacc date": next_v["date"] if next_v else "—",
                "Vaccination":    vacc_status,
                "Initial fish":   initial_fish,
                "Current fish":   sm.get("current_fish", 0),
                "Mortality":      sm.get("total_mortality", 0),
                "Mortality %":    f"{sm.get('mortality_pct', 0.0):.1f}%",
            })

        st.dataframe(pd.DataFrame(overview_rows), use_container_width=True, hide_index=True)

        st.divider()

        # ── Per-batch cards ────────────────────────────────────────────────
        for batch in active:
            bid = batch.get("id", "")
            sm  = summary_by_id.get(bid, {})

            if not write_ok:
                # ── Viewer: read-only card ─────────────────────────────────
                _render_batch_card_readonly(batch, sm, today)
            else:
                # ── Write roles: full interactive card ─────────────────────
                hatch_date_str = batch.get("hatch_date", "") or ""
                next_v         = _next_planned_vaccination(batch)
                vacc_status    = _vacc_status(batch, today)
                is_urgent      = _vacc_is_urgent(batch, today)
                show_add_next  = st.session_state.get(f"add_next_vacc_{bid}", False)
                confirm_arch   = st.session_state.get(f"confirm_arch_{bid}", False)

                with st.container(border=True):

                    # ── Top row: identity / stock / vaccination / archive ───
                    c1, c2, c3, c4 = st.columns([3, 2, 2, 1])

                    c1.markdown(f"**{batch['name']}** — {batch.get('species', '—')}")
                    c1.caption(f"Start: {batch.get('start_date', '—')}")
                    if hatch_date_str:
                        dah = _days_after_hatch(hatch_date_str, today)
                        c1.caption(f"Hatch: {hatch_date_str}  |  DAH: {dah} days")
                    if batch.get("notes"):
                        c1.caption(f"Notes: {batch['notes']}")

                    c2.metric("Current fish", f"{sm.get('current_fish', 0):,}")
                    c2.caption(
                        f"Initial: {batch.get('initial_fish_count', 0):,}  |  "
                        f"Biomass: {sm.get('current_biomass_kg', 0):.1f} kg"
                    )
                    c2.caption(
                        f"Mortality: {sm.get('total_mortality', 0):,}  "
                        f"({sm.get('mortality_pct', 0.0):.1f}%)"
                    )

                    if is_urgent:
                        c3.warning(vacc_status)
                    else:
                        c3.caption(f"Vaccination: {vacc_status}")
                    if next_v:
                        c3.caption(f"📅 Next: {next_v['date']}")

                    if not confirm_arch:
                        if c4.button("Archive", key=f"arch_{bid}"):
                            st.session_state[f"confirm_arch_{bid}"] = True
                            st.rerun()

                    # ── Archive confirmation ───────────────────────────────
                    if confirm_arch:
                        st.warning(
                            "⚠️ This batch may still be referenced by tanks or historical "
                            "records. It will be **archived**, not permanently deleted."
                        )
                        ca1, ca2, _ = st.columns([2, 2, 4])
                        if ca1.button(
                            "Confirm archive",
                            key=f"arch_confirm_{bid}",
                            type="primary",
                        ):
                            for b in batches:
                                if b["id"] == bid:
                                    b["status"] = "archived"
                            storage.save_batches(batches)
                            storage.log_activity(
                                module="Batch Management",
                                action="delete",
                                summary=f"Archived batch {batch['name']}",
                            )
                            st.session_state.pop(f"confirm_arch_{bid}", None)
                            st.rerun()
                        if ca2.button("Cancel", key=f"arch_cancel_{bid}"):
                            st.session_state.pop(f"confirm_arch_{bid}", None)
                            st.rerun()

                    # ── Vaccination controls ───────────────────────────────
                    if show_add_next:
                        st.info(
                            f"Vaccination marked as done for **{batch['name']}**. "
                            "Schedule the next vaccination date:"
                        )
                        nv1, nv2, nv3 = st.columns([3, 1, 1])
                        nv_date = nv1.date_input(
                            "Next vaccination date",
                            value=None,
                            key=f"nv_{bid}",
                        )
                        if nv_date and nv_date < today:
                            nv1.warning("Selected date is in the past.")
                        if nv2.button("Add date", key=f"nv_add_{bid}", type="primary"):
                            if nv_date:
                                for b in batches:
                                    if b.get("id") == bid:
                                        b.setdefault("vaccination_schedule", [])
                                        b["vaccination_schedule"].append({
                                            "id":        storage.new_id("vac_"),
                                            "date":      str(nv_date),
                                            "done":      False,
                                            "done_date": "",
                                            "notes":     "",
                                        })
                                storage.save_batches(batches)
                                st.session_state.pop(f"add_next_vacc_{bid}", None)
                                st.rerun()
                            else:
                                nv1.error("Please select a date.")
                        if nv3.button("Skip", key=f"nv_skip_{bid}"):
                            st.session_state.pop(f"add_next_vacc_{bid}", None)
                            st.rerun()

                    elif next_v:
                        btn_cols = st.columns([5, 3])
                        btn_type = "primary" if is_urgent else "secondary"
                        with btn_cols[1]:
                            if st.button(
                                f"✅ Mark done  ({next_v['date']})",
                                key=f"vd_{bid}",
                                type=btn_type,
                                use_container_width=True,
                            ):
                                for b in batches:
                                    if b.get("id") == bid:
                                        for v in b.get("vaccination_schedule", []):
                                            if v.get("id") == next_v.get("id"):
                                                v["done"]      = True
                                                v["done_date"] = str(today)
                                        b["vaccination_done"] = True
                                storage.save_batches(batches)
                                storage.log_activity(
                                    module="Batch Management",
                                    action="update",
                                    summary=f"Marked vaccination as done for batch {batch['name']}",
                                )
                                st.session_state[f"add_next_vacc_{bid}"] = True
                                st.rerun()

                    else:
                        with st.expander("📅 Schedule vaccination date", expanded=False):
                            sv1, sv2 = st.columns([3, 1])
                            sv_date = sv1.date_input(
                                "Vaccination date",
                                value=None,
                                key=f"sv_{bid}",
                            )
                            if sv_date and sv_date < today:
                                sv1.warning("Selected date is in the past.")
                            if sv2.button("Schedule", key=f"sv_btn_{bid}", type="primary"):
                                if sv_date:
                                    for b in batches:
                                        if b.get("id") == bid:
                                            b.setdefault("vaccination_schedule", [])
                                            b["vaccination_schedule"].append({
                                                "id":        storage.new_id("vac_"),
                                                "date":      str(sv_date),
                                                "done":      False,
                                                "done_date": "",
                                                "notes":     "",
                                            })
                                    storage.save_batches(batches)
                                    st.rerun()
                                else:
                                    sv1.error("Please select a date.")

                    # ── Edit batch ─────────────────────────────────────────
                    with st.expander("✏️ Edit batch", expanded=False):
                        with st.form(f"edit_batch_{bid}", clear_on_submit=False):
                            ea, eb = st.columns(2)
                            e_name    = ea.text_input(
                                "Batch name",
                                value=batch.get("name", ""),
                                key=f"en_{bid}",
                            )
                            e_species = eb.text_input(
                                "Species",
                                value=batch.get("species", ""),
                                key=f"es_{bid}",
                            )

                            ec, ed = st.columns(2)
                            e_hatch = ec.date_input(
                                "Hatch date (optional)",
                                value=_parse_date_safe(batch.get("hatch_date", "")),
                                key=f"eh_{bid}",
                            )
                            e_notes = ed.text_area(
                                "Notes",
                                value=batch.get("notes", ""),
                                height=68,
                                key=f"eno_{bid}",
                            )

                            ee, ef = st.columns(2)
                            e_fish = ee.number_input(
                                "Initial fish count",
                                min_value=1,
                                value=int(batch.get("initial_fish_count", 1000) or 1000),
                                step=100,
                                key=f"ef_{bid}",
                            )
                            e_weight = ef.number_input(
                                "Initial avg weight (g)",
                                min_value=0.1,
                                value=float(batch.get("initial_avg_weight_g", 50.0) or 50.0),
                                step=0.5,
                                key=f"ew_{bid}",
                            )

                            save_edit = st.form_submit_button("💾 Save changes", type="primary")

                            if save_edit:
                                if not e_name.strip():
                                    st.error("Batch name is required.")
                                else:
                                    for b in batches:
                                        if b.get("id") == bid:
                                            b["name"]                 = e_name.strip()
                                            b["species"]              = e_species.strip()
                                            b["hatch_date"]           = str(e_hatch) if e_hatch else ""
                                            b["initial_fish_count"]   = int(e_fish)
                                            b["initial_avg_weight_g"] = float(e_weight)
                                            b["initial_biomass_kg"]   = float(e_fish) * float(e_weight) / 1000.0
                                            b["notes"]                = e_notes.strip()
                                    storage.save_batches(batches)
                                    storage.log_activity(
                                        module="Batch Management",
                                        action="update",
                                        summary=f"Edited batch {e_name.strip()}",
                                    )
                                    st.success("Batch updated.")
                                    st.rerun()

                    # ── Vaccination history + schedule editing ─────────────
                    schedule = batch.get("vaccination_schedule", [])
                    if schedule:
                        done_count  = sum(1 for v in schedule if v.get("done"))
                        total_count = len(schedule)
                        with st.expander(
                            f"Vaccination history ({done_count}/{total_count} done)",
                            expanded=False,
                        ):
                            pending_entries = [v for v in schedule if not v.get("done")]
                            done_entries    = [v for v in schedule if v.get("done")]

                            if pending_entries:
                                st.caption("**Planned** — edit date/notes or remove:")
                                for v in sorted(pending_entries, key=lambda x: x.get("date", "")):
                                    vid = v.get("id", "")
                                    p1, p2, p3, p4 = st.columns([2, 3, 1, 1])
                                    pv_date = p1.date_input(
                                        "Date",
                                        value=_parse_date_safe(v.get("date", "")),
                                        key=f"pvd_{bid}_{vid}",
                                        label_visibility="collapsed",
                                    )
                                    pv_notes = p2.text_input(
                                        "Notes",
                                        value=v.get("notes", ""),
                                        key=f"pvn_{bid}_{vid}",
                                        label_visibility="collapsed",
                                        placeholder="Notes (optional)",
                                    )
                                    if p3.button("Save", key=f"pvs_{bid}_{vid}"):
                                        for b in batches:
                                            if b.get("id") == bid:
                                                for vs in b.get("vaccination_schedule", []):
                                                    if vs.get("id") == vid:
                                                        vs["date"]  = str(pv_date) if pv_date else vs.get("date", "")
                                                        vs["notes"] = pv_notes
                                        storage.save_batches(batches)
                                        storage.log_activity(
                                            module="Batch Management",
                                            action="update",
                                            summary=f"Edited vaccination date for batch {batch['name']}",
                                        )
                                        st.rerun()
                                    if p4.button("Remove", key=f"pvr_{bid}_{vid}"):
                                        for b in batches:
                                            if b.get("id") == bid:
                                                b["vaccination_schedule"] = [
                                                    vs for vs in b.get("vaccination_schedule", [])
                                                    if vs.get("id") != vid
                                                ]
                                        storage.save_batches(batches)
                                        st.rerun()

                            if done_entries:
                                if pending_entries:
                                    st.divider()
                                st.caption("**Completed:**")
                                hist_rows = []
                                for v in sorted(done_entries, key=lambda x: x.get("date", ""), reverse=True):
                                    hist_rows.append({
                                        "Planned date": v.get("date", "—"),
                                        "Done date":    v.get("done_date") or "—",
                                        "Notes":        v.get("notes") or "—",
                                    })
                                st.dataframe(
                                    pd.DataFrame(hist_rows),
                                    use_container_width=True,
                                    hide_index=True,
                                )

        # ── Batch distribution across tanks ───────────────────────────────
        st.divider()
        st.subheader("Batch distribution across tanks")
        st.caption(
            "Fish counts are computed dynamically from movements and mortality. "
            "After a transfer, a batch automatically appears in both tanks."
        )

        dist_rows = []
        for batch in active:
            bid = batch.get("id", "")
            sm  = summary_by_id.get(bid, {})
            for td in sm.get("tank_distribution", []):
                dist_rows.append({
                    "Batch":      batch["name"],
                    "Tank":       td.get("tank_name", td.get("tank_id", "—")),
                    "Fish count": td.get("fish_count", 0),
                    "Biomass kg": "—",
                    "Batch %":    f"{td.get('percentage_of_batch', 0.0):.1f}%",
                })

        if dist_rows:
            st.dataframe(pd.DataFrame(dist_rows), use_container_width=True, hide_index=True)
        else:
            st.info(
                "No batch–tank links found yet."
                + (" In **Farm Setup**, open each tank and set its **Initial batch composition**." if write_ok else "")
            )

    # ══════════════════════════════════════════════════════════════════════════
    # ARCHIVED BATCHES  (write roles only — Viewer has no use for this)
    # ══════════════════════════════════════════════════════════════════════════
    if write_ok:
        archived = [b for b in batches if b.get("status") == "archived"]

        st.divider()

        if not archived:
            st.caption("No archived batches.")
        else:
            show_archived = st.checkbox(
                f"Show archived batches ({len(archived)})",
                key="show_archived_batches",
            )
            if show_archived:
                st.subheader("Archived batches")
                for batch in archived:
                    with st.container(border=True):
                        ac1, ac2, ac3 = st.columns([3, 3, 1])

                        ac1.markdown(f"~~{batch['name']}~~ — {batch.get('species', '—')}")
                        ac1.caption(
                            f"Start: {batch.get('start_date', '—')}"
                            + (f"  |  Hatch: {batch['hatch_date']}" if batch.get("hatch_date") else "")
                        )

                        initial_fish = batch.get("initial_fish_count", 0)
                        initial_bio  = batch.get("initial_biomass_kg", 0)
                        ac2.caption(f"Initial: {initial_fish:,} fish  |  {initial_bio:.1f} kg")
                        if batch.get("notes"):
                            ac2.caption(f"Notes: {batch['notes']}")

                        if ac3.button("Restore", key=f"rest_{batch['id']}"):
                            for b in batches:
                                if b["id"] == batch["id"]:
                                    b["status"] = "active"
                            storage.save_batches(batches)
                            storage.log_activity(
                                module="Batch Management",
                                action="update",
                                summary=f"Restored archived batch {batch['name']}",
                            )
                            st.rerun()
