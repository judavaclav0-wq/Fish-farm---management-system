"""
modules/movements.py
Movements — record fish transfers between tanks.
"""

import streamlit as st
import pandas as pd
from datetime import date, datetime
from core import storage
from core.stock_engine import get_all_tanks, compute_tank_state


# ─────────────────────────────────────────────────────────────────────────────
def render() -> None:
    st.header("Fish Movements")

    farm        = storage.load_farm()
    tanks       = get_all_tanks(farm)
    batches     = storage.load_batches()
    daily_logs  = storage.load_daily_logs()
    movements   = storage.load_movements()
    adjustments = storage.load_adjustments()

    if not tanks:
        st.warning("At least one tank is needed to record a movement.")
        return

    system_names = sorted({t.get("system_name", "—") for t in tanks})

    tab_new, tab_history, tab_adjustments = st.tabs(
        ["Record movement", "History", "Count Adjustments"]
    )

    # ── New movement ───────────────────────────────────────────────────────
    with tab_new:
        movement_type = st.selectbox(
            "Movement type",
            ["Transfer", "Harvest/Slaughter", "Killing", "External Stock In", "External Stock Out"],
        )

        col1, col2 = st.columns(2)
        mv_date     = col1.date_input("Date", value=date.today())
        mv_operator = col2.text_input("Operator", placeholder="Name")

        # ── External Stock In form ─────────────────────────────────────────
        if movement_type == "External Stock In":
            ext_col1, ext_col2 = st.columns(2)
            ext_to_system = ext_col1.selectbox(
                "Destination system", system_names, key="ext_to_system"
            )
            ext_to_system_tanks = [
                t for t in tanks if t.get("system_name", "—") == ext_to_system
            ]
            ext_to_tank_names = sorted([t["name"] for t in ext_to_system_tanks])
            if not ext_to_tank_names:
                st.warning("No tanks found in the selected system.")
            else:
                ext_to_tank_name = ext_col2.selectbox(
                    "Destination tank", ext_to_tank_names, key="ext_to_tank"
                )
                ext_to_tank = next(
                    (t for t in ext_to_system_tanks if t["name"] == ext_to_tank_name),
                    None,
                )

                if ext_to_tank:
                    dest_state = compute_tank_state(
                        tank=ext_to_tank,
                        batches=batches,
                        daily_logs=daily_logs,
                        movements=movements,
                        adjustments=adjustments,
                    )
                    d_fish    = dest_state.get("fish_count", 0)
                    d_biomass = dest_state.get("biomass_kg", 0.0)
                    d_avg     = dest_state.get("avg_weight_g", 0.0)
                    st.caption(
                        f"**{ext_to_tank_name}** current stock: "
                        f"{d_fish:,} fish | {d_biomass:.1f} kg | {d_avg:.1f} g avg"
                    )

                # Batch selector
                active_batches = [b for b in batches if b.get("status") == "active"]
                if not active_batches:
                    st.warning("No active batches. Create a batch first in Batch Management.")
                else:
                    batch_display = [
                        b.get("name") or b.get("id", "?") for b in active_batches
                    ]
                    ext_batch_sel = st.selectbox(
                        "Batch", batch_display, key="ext_batch_sel"
                    )
                    ext_batch_obj = next(
                        (b for b in active_batches
                         if (b.get("name") or b.get("id")) == ext_batch_sel),
                        None,
                    )
                    ext_batch_id   = ext_batch_obj.get("id", "")   if ext_batch_obj else ""
                    ext_batch_name = ext_batch_obj.get("name", "") if ext_batch_obj else ""

                    st.divider()

                    qty_c1, qty_c2, qty_c3 = st.columns(3)
                    ext_qty_fish = qty_c1.number_input(
                        "Fish count", min_value=1, value=100, step=10,
                        key="ext_qty_fish",
                    )
                    ext_avg_w = qty_c2.number_input(
                        "Average weight (g)",
                        min_value=0.1, value=100.0, step=1.0, format="%.1f",
                        key="ext_avg_w",
                    )
                    ext_biomass = round(float(ext_qty_fish) * float(ext_avg_w) / 1000, 3)
                    qty_c3.metric("Biomass (kg)", f"{ext_biomass:.3f}")

                    ext_source = st.text_input(
                        "External source",
                        placeholder="e.g. Hatchery, External nursery, Supplier, Other farm…",
                        key="ext_source_input",
                    )
                    ext_notes = st.text_area("Notes", height=68, key="ext_notes")

                    ext_submitted = st.button(
                        "Save movement", type="primary", key="ext_save_btn"
                    )

                    if ext_submitted:
                        errors = []
                        if not ext_to_tank:
                            errors.append("Select a destination tank.")
                        if not ext_batch_id:
                            errors.append("Select a batch.")
                        if int(ext_qty_fish) <= 0:
                            errors.append("Fish count must be greater than zero.")
                        if float(ext_avg_w) <= 0:
                            errors.append("Average weight must be greater than zero.")
                        if ext_biomass <= 0:
                            errors.append("Biomass must be greater than zero.")
                        if not ext_source.strip():
                            errors.append("External source is required.")

                        if errors:
                            for err in errors:
                                st.error(err)
                        else:
                            record = {
                                "id":           storage.new_id("mv_"),
                                "date":         str(mv_date),
                                "movement_type": "external_stock_in",

                                "from_system_name": "",
                                "from_tank_id":     "",
                                "from_tank_name":   "",

                                "to_system_name": ext_to_system,
                                "to_tank_id":     ext_to_tank["id"],
                                "to_tank_name":   ext_to_tank["name"],

                                "destination":    "",
                                "quantity_mode":  "fish",
                                "quantity_fish":  int(ext_qty_fish),
                                "quantity_kg":    float(ext_biomass),
                                "avg_weight_g":   float(ext_avg_w),

                                "batch_id":   ext_batch_id,
                                "batch_name": ext_batch_name,

                                # Stored in payload (jsonb) automatically — no migration needed.
                                "external_source": ext_source.strip(),

                                "reason":   "",
                                "operator": mv_operator.strip(),
                                "notes":    ext_notes.strip(),
                            }

                            storage.append_movement(record)

                            # Update batch initial_fish_count so mortality % is correct.
                            # We add the imported quantity to whatever baseline already exists.
                            for b in batches:
                                if b.get("id") == ext_batch_id:
                                    prior = int(b.get("initial_fish_count") or 0)
                                    b["initial_fish_count"] = prior + int(ext_qty_fish)
                                    break
                            storage.save_batches(batches)

                            storage.log_activity(
                                module="Movements",
                                action="create",
                                summary=(
                                    f"External Stock In: {ext_qty_fish:,} fish "
                                    f"(batch: {ext_batch_name}) → {ext_to_tank_name} "
                                    f"from {ext_source.strip()}"
                                ),
                                date=str(mv_date),
                                system_name=ext_to_system,
                                tank_id=str(ext_to_tank.get("id", "")),
                                tank_name=ext_to_tank_name,
                                operator=mv_operator.strip(),
                                details={
                                    "quantity_fish":   int(ext_qty_fish),
                                    "quantity_kg":     float(ext_biomass),
                                    "avg_weight_g":    float(ext_avg_w),
                                    "movement_type":   "external_stock_in",
                                    "external_source": ext_source.strip(),
                                    "batch_id":        ext_batch_id,
                                    "batch_name":      ext_batch_name,
                                },
                            )

                            st.success(
                                f"External Stock In saved: {ext_qty_fish:,} fish "
                                f"→ {ext_to_tank_name} from {ext_source.strip()}."
                            )
                            st.rerun()

        # ── External Stock Out form ────────────────────────────────────────
        elif movement_type == "External Stock Out":
            eso_col1, eso_col2 = st.columns(2)
            eso_from_system = eso_col1.selectbox(
                "From system", system_names, key="eso_from_system"
            )
            eso_from_system_tanks = [
                t for t in tanks if t.get("system_name", "—") == eso_from_system
            ]
            eso_from_tank_names = sorted([t["name"] for t in eso_from_system_tanks])
            if not eso_from_tank_names:
                st.warning("No tanks found in the selected system.")
            else:
                eso_from_tank_name = eso_col2.selectbox(
                    "From tank", eso_from_tank_names, key="eso_from_tank"
                )
                eso_from_tank = next(
                    (t for t in eso_from_system_tanks if t["name"] == eso_from_tank_name),
                    None,
                )

                if eso_from_tank:
                    eso_src_state = compute_tank_state(
                        tank=eso_from_tank,
                        batches=batches,
                        daily_logs=daily_logs,
                        movements=movements,
                        adjustments=adjustments,
                    )
                    eso_fish    = eso_src_state.get("fish_count", 0)
                    eso_biomass_cur = eso_src_state.get("biomass_kg", 0.0)
                    eso_avg_w_cur = eso_src_state.get("avg_weight_g", 0.0)
                    st.caption(
                        f"**{eso_from_tank_name}** current stock: "
                        f"{eso_fish:,} fish | {eso_biomass_cur:.1f} kg | {eso_avg_w_cur:.1f} g avg"
                    )

                    eso_batch_id   = eso_from_tank.get("batch_id", "")
                    eso_batch_name = eso_from_tank.get("batch_name", "")
                    if eso_batch_id or eso_batch_name:
                        st.caption(f"Batch: **{eso_batch_name or eso_batch_id}**")

                    eso_complete = st.checkbox(
                        "Tank was completely emptied",
                        key="eso_complete",
                    )

                    st.divider()

                    eso_q1, eso_q2, eso_q3 = st.columns(3)
                    eso_qty_fish = eso_q1.number_input(
                        "Fish count",
                        min_value=1,
                        value=max(1, eso_fish) if eso_fish > 0 else 1,
                        step=10,
                        key="eso_qty_fish",
                    )
                    eso_avg_w = eso_q2.number_input(
                        "Average weight (g)",
                        min_value=0.1,
                        value=max(0.1, float(eso_avg_w_cur)) if eso_avg_w_cur > 0 else 100.0,
                        step=1.0,
                        format="%.1f",
                        key="eso_avg_w",
                    )
                    eso_biomass = round(float(eso_qty_fish) * float(eso_avg_w) / 1000, 3)
                    eso_q3.metric("Biomass (kg)", f"{eso_biomass:.3f}")

                    eso_destination = st.text_input(
                        "External destination",
                        placeholder="e.g. Processing plant, Another farm, Retailer…",
                        key="eso_destination_input",
                    )
                    eso_notes = st.text_area("Notes", height=68, key="eso_notes")

                    eso_submitted = st.button(
                        "Save movement", type="primary", key="eso_save_btn"
                    )

                    if eso_submitted:
                        eso_errors = []
                        if not eso_from_tank:
                            eso_errors.append("Select a source tank.")
                        if int(eso_qty_fish) <= 0:
                            eso_errors.append("Fish count must be greater than zero.")
                        if float(eso_avg_w) <= 0:
                            eso_errors.append("Average weight must be greater than zero.")
                        if eso_biomass <= 0:
                            eso_errors.append("Biomass must be greater than zero.")
                        if not eso_destination.strip():
                            eso_errors.append("External destination is required.")
                        if not eso_complete and int(eso_qty_fish) > eso_fish:
                            eso_errors.append(
                                f"Cannot move {int(eso_qty_fish):,} fish — only "
                                f"{eso_fish:,} available in {eso_from_tank_name}."
                            )

                        if eso_errors:
                            for err in eso_errors:
                                st.error(err)
                        else:
                            record = {
                                "id":            storage.new_id("mv_"),
                                "date":          str(mv_date),
                                "movement_type": "external_stock_out",

                                "from_system_name": eso_from_system,
                                "from_tank_id":     eso_from_tank["id"],
                                "from_tank_name":   eso_from_tank["name"],

                                "to_system_name": "",
                                "to_tank_id":     None,
                                "to_tank_name":   "",

                                "destination":            "",
                                "external_destination":   eso_destination.strip(),
                                "quantity_mode":          "fish",
                                "quantity_fish":          int(eso_qty_fish),
                                "quantity_kg":            float(eso_biomass),
                                "avg_weight_g":           float(eso_avg_w),

                                "batch_id":   eso_batch_id,
                                "batch_name": eso_batch_name,

                                "reason":      "",
                                "operator":    mv_operator.strip(),
                                "notes":       eso_notes.strip(),
                                "tank_emptied": eso_complete,
                            }

                            storage.append_movement(record)

                            # Complete-tank reconciliation: if the tank was declared empty,
                            # create a variance adjustment to zero remaining fish.
                            if eso_complete:
                                remaining = eso_fish - int(eso_qty_fish)
                                if remaining != 0:
                                    adj_record = {
                                        "id":             storage.new_id("adj_"),
                                        "date":           str(mv_date),
                                        "timestamp":      datetime.now().isoformat(),
                                        "tank_id":        eso_from_tank["id"],
                                        "tank_name":      eso_from_tank_name,
                                        "system_name":    eso_from_system,
                                        "batch_id":       eso_batch_id or "",
                                        "batch_name":     eso_batch_name or "",
                                        "expected_count": eso_fish,
                                        "actual_count":   int(eso_qty_fish),
                                        "variance":       -remaining,
                                        "variance_pct":   round(
                                            -remaining / eso_fish * 100, 2
                                        ) if eso_fish > 0 else 0.0,
                                        "note":           "Tank emptied reconciliation",
                                        "movement_id":    record["id"],
                                        "operator":       mv_operator.strip(),
                                    }
                                    storage.append_adjustment(adj_record)

                            storage.log_activity(
                                module="Movements",
                                action="create",
                                summary=(
                                    f"External Stock Out: {int(eso_qty_fish):,} fish "
                                    f"from {eso_from_tank_name} → {eso_destination.strip()}"
                                ),
                                date=str(mv_date),
                                system_name=eso_from_system,
                                tank_id=str(eso_from_tank.get("id", "")),
                                tank_name=eso_from_tank_name,
                                operator=mv_operator.strip(),
                                details={
                                    "quantity_fish":        int(eso_qty_fish),
                                    "quantity_kg":          float(eso_biomass),
                                    "avg_weight_g":         float(eso_avg_w),
                                    "movement_type":        "external_stock_out",
                                    "external_destination": eso_destination.strip(),
                                    "tank_emptied":         eso_complete,
                                },
                            )

                            st.success(
                                f"External Stock Out saved: {int(eso_qty_fish):,} fish "
                                f"from {eso_from_tank_name} → {eso_destination.strip()}."
                            )
                            st.rerun()

        # ── Transfer / Harvest / Killing form ─────────────────────────────
        else:
            col3, col4 = st.columns(2)

            from_system = col3.selectbox("From system", system_names)
            from_system_tanks = [
                t for t in tanks if t.get("system_name", "—") == from_system
            ]
            from_tank_names = sorted([t["name"] for t in from_system_tanks])
            from_tank_name = col4.selectbox("From tank", from_tank_names)

            from_tank = next(t for t in from_system_tanks if t["name"] == from_tank_name)

            src_state = compute_tank_state(
                tank=from_tank,
                batches=batches,
                daily_logs=daily_logs,
                movements=movements,
                adjustments=adjustments,
            )

            current_fish    = src_state.get("fish_count", 0)   if src_state else 0
            current_biomass = src_state.get("biomass_kg", 0.0) if src_state else 0.0
            avg_weight      = src_state.get("avg_weight_g", 0.0) if src_state else 0.0

            st.caption(
                f"**{from_tank_name}** current stock: "
                f"{current_fish:,} fish | "
                f"{current_biomass:.1f} kg biomass | "
                f"{avg_weight:.1f} g avg weight"
            )

            batch_id   = from_tank.get("batch_id")
            batch_name = from_tank.get("batch_name", "")

            if batch_id or batch_name:
                st.caption(f"Batch: **{batch_name or batch_id}**")

            to_system   = ""
            to_tank     = None
            to_tank_name = ""

            if movement_type == "Transfer":
                col5, col6 = st.columns(2)

                to_system = col5.selectbox("To system", system_names)

                to_system_tanks = [
                    t for t in tanks
                    if t.get("system_name", "—") == to_system
                    and t.get("id") != from_tank.get("id")
                ]

                if not to_system_tanks:
                    st.warning("No available destination tanks for the selected system.")
                    return

                to_tank_names = sorted([t["name"] for t in to_system_tanks])
                to_tank_name  = col6.selectbox("To tank", to_tank_names)
                to_tank       = next(t for t in to_system_tanks if t["name"] == to_tank_name)

            # ── Count reconciliation (Transfer only) ──────────────────────────
            has_adjustment      = False
            actual_count        = current_fish
            adjustment_variance = 0
            adjustment_note     = ""

            if movement_type == "Transfer":
                has_adjustment = st.checkbox(
                    "Actual counted fish differs from system count",
                    key="mv_has_adjustment",
                )
                if has_adjustment:
                    with st.container(border=True):
                        st.caption(
                            "**Count reconciliation** — corrects the stock before this transfer "
                            "and records the discrepancy as a separate audit entry."
                        )
                        rc1, rc2, rc3 = st.columns(3)
                        rc1.metric("Expected (system)", f"{current_fish:,}")
                        actual_count = rc2.number_input(
                            "Actual counted fish",
                            min_value=0,
                            value=current_fish,
                            step=1,
                            key="mv_actual_count",
                        )
                        _adj_v = int(actual_count) - current_fish
                        _sign  = "+" if _adj_v >= 0 else ""
                        _pct   = (_adj_v / current_fish * 100) if current_fish > 0 else 0.0
                        rc3.metric("Variance", f"{_sign}{_adj_v:,} fish", f"{_sign}{_pct:.1f}%")
                        adjustment_note = st.text_input(
                            "Adjustment note (optional)",
                            placeholder="Reason for discrepancy…",
                            key="mv_adj_note",
                        )
                        adjustment_variance = _adj_v

            st.divider()

            quantity_mode = st.radio(
                "Quantity mode",
                ["Fish count", "Biomass kg"],
                horizontal=True,
            )

            if quantity_mode == "Fish count":
                quantity_fish = st.number_input(
                    "Fish quantity",
                    min_value=1,
                    value=100,
                    step=10,
                )
                quantity_kg = (
                    float(quantity_fish) * float(avg_weight) / 1000
                    if avg_weight > 0 else 0.0
                )
                st.caption(f"Estimated biomass moved: **{quantity_kg:.2f} kg**")

            else:
                quantity_kg = st.number_input(
                    "Biomass quantity (kg)",
                    min_value=0.0,
                    value=0.0,
                    step=0.5,
                    format="%.2f",
                )
                quantity_fish = (
                    int(round(float(quantity_kg) / (float(avg_weight) / 1000)))
                    if avg_weight > 0 else 0
                )
                st.caption(f"Estimated fish moved: **{quantity_fish:,} fish**")

            if movement_type == "Transfer":
                reason = st.selectbox(
                    "Reason",
                    ["Grading", "Stage transfer", "Emergency", "Splitting", "Other"],
                    key="mv_transfer_reason",
                )
            elif movement_type == "Harvest/Slaughter":
                st.caption("Reason: **Harvesting**")
                reason = "Harvesting"
            else:  # Killing
                reason = st.text_area(
                    "Reason",
                    height=68,
                    placeholder="Describe the cause — disease, injury, culling decision…",
                    key="mv_killing_reason",
                )
            mv_notes = st.text_area("Notes", height=68, key="mv_notes")

            submitted = st.button("Save movement", type="primary")

            if submitted:
                fish_for_validation = int(actual_count) if has_adjustment else current_fish
                if quantity_fish <= 0:
                    st.error("Quantity must be greater than zero.")
                elif movement_type == "Killing" and not str(reason).strip():
                    st.error("Please enter a reason for the killing.")
                elif quantity_fish > fish_for_validation:
                    if has_adjustment:
                        st.error(
                            f"Cannot move {quantity_fish:,} fish — only "
                            f"{fish_for_validation:,} fish counted in {from_tank_name}."
                        )
                    else:
                        st.error(
                            f"Cannot move {quantity_fish:,} fish — only "
                            f"{fish_for_validation:,} available in {from_tank_name}."
                        )
                else:
                    record = {
                        "id": storage.new_id("mv_"),
                        "date": str(mv_date),

                        "movement_type": {
                            "Transfer":          "transfer",
                            "Harvest/Slaughter": "harvest",
                            "Killing":           "killing",
                        }.get(movement_type, "transfer"),

                        "from_system_name": from_system,
                        "from_tank_id": from_tank["id"],
                        "from_tank_name": from_tank["name"],

                        "to_system_name": to_system if movement_type == "Transfer" else "",
                        "to_tank_id": to_tank["id"] if to_tank else None,
                        "to_tank_name": to_tank["name"] if to_tank else "",

                        "destination": "Harvest/Slaughter"
                        if movement_type == "Harvest/Slaughter" else "",

                        "quantity_mode": "fish"
                        if quantity_mode == "Fish count" else "kg",
                        "quantity_fish": int(quantity_fish),
                        "quantity_kg": float(quantity_kg),
                        "avg_weight_g": float(avg_weight),

                        "batch_id": batch_id,
                        "batch_name": batch_name,

                        "reason": reason,
                        "operator": mv_operator.strip(),
                        "notes": mv_notes.strip(),
                    }

                    storage.append_movement(record)

                    # Save count-reconciliation adjustment if checkbox was used
                    if has_adjustment and adjustment_variance != 0:
                        adj_record = {
                            "id":             storage.new_id("adj_"),
                            "date":           str(mv_date),
                            "timestamp":      datetime.now().isoformat(),
                            "tank_id":        from_tank["id"],
                            "tank_name":      from_tank_name,
                            "system_name":    from_system,
                            "batch_id":       batch_id or "",
                            "batch_name":     batch_name or "",
                            "expected_count": current_fish,
                            "actual_count":   int(actual_count),
                            "variance":       adjustment_variance,
                            "variance_pct":   round(
                                adjustment_variance / current_fish * 100, 2
                            ) if current_fish > 0 else 0.0,
                            "note":           adjustment_note.strip() if adjustment_note else "",
                            "movement_id":    record["id"],
                            "operator":       mv_operator.strip(),
                        }
                        storage.append_adjustment(adj_record)
                        storage.log_activity(
                            module="Movements",
                            action="adjustment",
                            summary=(
                                f"Count adjustment: {from_tank_name} expected "
                                f"{current_fish:,}, actual {int(actual_count):,} "
                                f"(variance {adjustment_variance:+,})"
                            ),
                            date=str(mv_date),
                            system_name=from_system,
                            tank_id=str(from_tank.get("id", "")),
                            tank_name=from_tank_name,
                            operator=mv_operator.strip(),
                            details={
                                "expected_count": current_fish,
                                "actual_count":   int(actual_count),
                                "variance":       adjustment_variance,
                                "adjustment_id":  adj_record["id"],
                            },
                        )

                    if movement_type == "Transfer":
                        mv_summary = (
                            f"Created transfer: {quantity_fish:,} fish "
                            f"from {from_tank_name} to {to_tank_name}"
                        )
                        st.success(
                            f"Movement saved: {quantity_fish:,} fish from "
                            f"{from_tank_name} → {to_tank_name}."
                        )
                    elif movement_type == "Harvest/Slaughter":
                        mv_summary = (
                            f"Created harvest/slaughter: {quantity_fish:,} fish from {from_tank_name}"
                        )
                        st.success(
                            f"Harvest saved: {quantity_fish:,} fish removed from "
                            f"{from_tank_name}."
                        )
                    else:  # Killing
                        mv_summary = (
                            f"Created killing record: {quantity_fish:,} fish from {from_tank_name}"
                        )
                        st.success(
                            f"Killing recorded: {quantity_fish:,} fish removed from "
                            f"{from_tank_name}."
                        )

                    storage.log_activity(
                        module="Movements",
                        action="create",
                        summary=mv_summary,
                        date=str(mv_date),
                        system_name=from_system,
                        tank_id=str(from_tank.get("id", "")),
                        tank_name=from_tank_name,
                        operator=mv_operator.strip(),
                        details={
                            "quantity_fish": int(quantity_fish),
                            "quantity_kg":   float(quantity_kg),
                            "movement_type": record["movement_type"],
                        },
                    )

                    st.rerun()

    # ── History tab ────────────────────────────────────────────────────────
    with tab_history:
        if not movements:
            st.info("No movements recorded yet.")
            return

        df = pd.DataFrame(movements)

        available_dates = sorted(df["date"].unique(), reverse=True)
        sel_dates = st.multiselect("Filter by date", available_dates, default=available_dates[:14])
        if sel_dates:
            df = df[df["date"].isin(sel_dates)]

        display_cols = [
            "date", "movement_type",
            "from_system_name", "from_tank_name",
            "to_system_name", "to_tank_name",
            "quantity_fish", "quantity_kg",
            "avg_weight_g", "batch_name",
            "external_source", "external_destination",
            "reason", "operator", "notes",
        ]
        present = [c for c in display_cols if c in df.columns]
        st.dataframe(
            df[present].sort_values("date", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

        csv = df[present].to_csv(index=False)
        st.download_button(
            "Download CSV",
            csv,
            file_name=f"movements_{date.today()}.csv",
            mime="text/csv",
        )

        st.divider()
        st.subheader("Edit / Delete movement")

        movement_options = []
        movement_lookup  = {}

        for move in sorted(movements, key=lambda x: x.get("date", ""), reverse=True):
            move_id = move.get("id")
            if not move_id:
                continue

            label = (
                f"{move.get('date', '—')} | "
                f"{move.get('movement_type', 'transfer')} | "
                f"{move.get('from_tank_name') or 'External'} → "
                f"{move.get('to_tank_name') or move.get('destination') or '—'} | "
                f"{move.get('quantity_fish', 0):,} fish"
            )

            movement_options.append(label)
            movement_lookup[label] = move

        if not movement_options:
            st.info("No editable movement records found.")
            return

        selected_label = st.selectbox(
            "Select movement",
            movement_options,
            key="edit_movement_select",
        )

        selected_move = movement_lookup[selected_label]
        selected_id   = selected_move.get("id")

        st.markdown("### Selected movement")

        _STORED_TO_DISPLAY = {
            "transfer":           "Transfer",
            "harvest":            "Harvest/Slaughter",
            "killing":            "Killing",
            "external_stock_in":  "External Stock In",
            "external_stock_out": "External Stock Out",
        }
        _EDIT_TYPES = ["Transfer", "Harvest/Slaughter", "Killing", "External Stock In", "External Stock Out"]

        edit_type_default = _STORED_TO_DISPLAY.get(
            str(selected_move.get("movement_type", "transfer")).strip().lower(),
            "Transfer",
        )

        edit_type = st.selectbox(
            "Movement type",
            _EDIT_TYPES,
            index=_EDIT_TYPES.index(edit_type_default),
            key=f"edit_type_{selected_id}",
        )

        c1, c2 = st.columns(2)

        edit_date = c1.date_input(
            "Date",
            value=pd.to_datetime(selected_move.get("date", str(date.today()))).date(),
            key=f"edit_date_{selected_id}",
        )

        edit_operator = c2.text_input(
            "Operator",
            value=selected_move.get("operator", ""),
            key=f"edit_operator_{selected_id}",
        )

        # ── External Stock In edit form ────────────────────────────────────
        if edit_type == "External Stock In":
            ec1, ec2 = st.columns(2)

            ext_edit_system_default = selected_move.get("to_system_name") or system_names[0]
            ext_edit_system_idx = (
                system_names.index(ext_edit_system_default)
                if ext_edit_system_default in system_names else 0
            )
            edit_ext_to_system = ec1.selectbox(
                "Destination system",
                system_names,
                index=ext_edit_system_idx,
                key=f"edit_ext_to_system_{selected_id}",
            )
            edit_ext_to_system_tanks = [
                t for t in tanks if t.get("system_name", "—") == edit_ext_to_system
            ]
            edit_ext_to_tank_names = sorted([t["name"] for t in edit_ext_to_system_tanks])

            ext_tank_default = selected_move.get("to_tank_name")
            ext_tank_idx = (
                edit_ext_to_tank_names.index(ext_tank_default)
                if ext_tank_default in edit_ext_to_tank_names else 0
            )
            edit_ext_to_tank_name = ec2.selectbox(
                "Destination tank",
                edit_ext_to_tank_names,
                index=ext_tank_idx,
                key=f"edit_ext_to_tank_{selected_id}",
            )
            edit_ext_to_tank = next(
                (t for t in edit_ext_to_system_tanks if t["name"] == edit_ext_to_tank_name),
                None,
            )

            # Batch selector
            active_batches = [b for b in batches if b.get("status") == "active"]
            edit_batch_display = [b.get("name") or b.get("id", "?") for b in active_batches]
            curr_batch_name = selected_move.get("batch_name", "")
            edit_ext_batch_idx = (
                edit_batch_display.index(curr_batch_name)
                if curr_batch_name in edit_batch_display else 0
            )
            edit_ext_batch_sel = st.selectbox(
                "Batch",
                edit_batch_display,
                index=edit_ext_batch_idx,
                key=f"edit_ext_batch_{selected_id}",
            )
            edit_ext_batch_obj = next(
                (b for b in active_batches
                 if (b.get("name") or b.get("id")) == edit_ext_batch_sel),
                None,
            )
            edit_ext_batch_id   = edit_ext_batch_obj.get("id", "")   if edit_ext_batch_obj else ""
            edit_ext_batch_name = edit_ext_batch_obj.get("name", "") if edit_ext_batch_obj else ""

            eq1, eq2, eq3 = st.columns(3)
            edit_ext_qty = eq1.number_input(
                "Fish count",
                min_value=1,
                value=max(1, int(selected_move.get("quantity_fish", 1))),
                step=10,
                key=f"edit_ext_qty_{selected_id}",
            )
            edit_ext_avg_w = eq2.number_input(
                "Average weight (g)",
                min_value=0.1,
                value=max(0.1, float(selected_move.get("avg_weight_g", 100.0))),
                step=1.0,
                format="%.1f",
                key=f"edit_ext_avg_w_{selected_id}",
            )
            edit_ext_biomass = round(float(edit_ext_qty) * float(edit_ext_avg_w) / 1000, 3)
            eq3.metric("Biomass (kg)", f"{edit_ext_biomass:.3f}")

            edit_ext_source = st.text_input(
                "External source",
                value=selected_move.get("external_source", ""),
                placeholder="e.g. Hatchery, External nursery, Supplier, Other farm…",
                key=f"edit_ext_source_{selected_id}",
            )
            edit_ext_notes = st.text_area(
                "Notes",
                value=selected_move.get("notes", ""),
                height=68,
                key=f"edit_ext_notes_{selected_id}",
            )

            col_save, col_delete = st.columns(2)

            if col_save.button(
                "Save changes", type="primary", key=f"save_movement_{selected_id}"
            ):
                ext_errors = []
                if not edit_ext_to_tank:
                    ext_errors.append("Select a destination tank.")
                if not edit_ext_batch_id:
                    ext_errors.append("Select a batch.")
                if int(edit_ext_qty) <= 0:
                    ext_errors.append("Fish count must be greater than zero.")
                if float(edit_ext_avg_w) <= 0:
                    ext_errors.append("Average weight must be greater than zero.")
                if not edit_ext_source.strip():
                    ext_errors.append("External source is required.")

                if ext_errors:
                    for err in ext_errors:
                        st.error(err)
                else:
                    old_batch_id  = selected_move.get("batch_id", "")
                    old_qty_fish  = int(selected_move.get("quantity_fish", 0))

                    updated_record = {
                        "id":           selected_id,
                        "date":         str(edit_date),
                        "movement_type": "external_stock_in",

                        "from_system_name": "",
                        "from_tank_id":     "",
                        "from_tank_name":   "",

                        "to_system_name": edit_ext_to_system,
                        "to_tank_id":     edit_ext_to_tank["id"] if edit_ext_to_tank else "",
                        "to_tank_name":   edit_ext_to_tank_name,

                        "destination":    "",
                        "quantity_mode":  "fish",
                        "quantity_fish":  int(edit_ext_qty),
                        "quantity_kg":    float(edit_ext_biomass),
                        "avg_weight_g":   float(edit_ext_avg_w),

                        "batch_id":   edit_ext_batch_id,
                        "batch_name": edit_ext_batch_name,

                        "external_source": edit_ext_source.strip(),
                        "reason":          "",
                        "operator":        edit_operator.strip(),
                        "notes":           edit_ext_notes.strip(),
                    }

                    updated_movements = [
                        updated_record if m.get("id") == selected_id else m
                        for m in movements
                    ]
                    storage.save_movements(updated_movements)

                    # Recalculate batch baseline: reverse old qty, apply new qty.
                    for b in batches:
                        if b.get("id") == old_batch_id:
                            b["initial_fish_count"] = max(
                                0,
                                int(b.get("initial_fish_count") or 0) - old_qty_fish,
                            )
                        if b.get("id") == edit_ext_batch_id:
                            b["initial_fish_count"] = (
                                int(b.get("initial_fish_count") or 0) + int(edit_ext_qty)
                            )
                    storage.save_batches(batches)

                    storage.log_activity(
                        module="Movements",
                        action="update",
                        summary=(
                            f"Updated External Stock In: {edit_ext_qty:,} fish "
                            f"→ {edit_ext_to_tank_name}"
                        ),
                        date=str(edit_date),
                        system_name=edit_ext_to_system,
                        tank_id=str(edit_ext_to_tank.get("id", "")) if edit_ext_to_tank else "",
                        tank_name=edit_ext_to_tank_name,
                        operator=edit_operator.strip(),
                        details={"movement_id": selected_id},
                    )
                    st.success("Movement updated.")
                    st.rerun()

            if col_delete.button(
                "Delete movement", key=f"delete_movement_{selected_id}"
            ):
                # Reverse batch baseline contribution from this record.
                del_batch_id  = selected_move.get("batch_id", "")
                del_qty_fish  = int(selected_move.get("quantity_fish", 0))
                for b in batches:
                    if b.get("id") == del_batch_id:
                        b["initial_fish_count"] = max(
                            0,
                            int(b.get("initial_fish_count") or 0) - del_qty_fish,
                        )
                        break
                storage.save_batches(batches)

                updated_movements = [
                    m for m in movements if m.get("id") != selected_id
                ]
                storage.save_movements(updated_movements)
                storage.log_activity(
                    module="Movements",
                    action="delete",
                    summary=(
                        f"Deleted External Stock In: "
                        f"{selected_move.get('quantity_fish', 0):,} fish "
                        f"from {selected_move.get('external_source', '—')}"
                    ),
                    date=selected_move.get("date", ""),
                    system_name=selected_move.get("to_system_name", ""),
                    tank_name=selected_move.get("to_tank_name", ""),
                    details={"movement_id": selected_id},
                )
                st.success("Movement deleted.")
                st.rerun()

        # ── External Stock Out edit form ───────────────────────────────────
        elif edit_type == "External Stock Out":
            eso_ec1, eso_ec2 = st.columns(2)

            eso_edit_system_default = selected_move.get("from_system_name") or system_names[0]
            eso_edit_system_idx = (
                system_names.index(eso_edit_system_default)
                if eso_edit_system_default in system_names else 0
            )
            eso_edit_from_system = eso_ec1.selectbox(
                "From system",
                system_names,
                index=eso_edit_system_idx,
                key=f"eso_edit_from_system_{selected_id}",
            )
            eso_edit_from_system_tanks = [
                t for t in tanks if t.get("system_name", "—") == eso_edit_from_system
            ]
            eso_edit_from_tank_names = sorted([t["name"] for t in eso_edit_from_system_tanks])

            eso_from_tank_default = selected_move.get("from_tank_name")
            eso_from_tank_idx = (
                eso_edit_from_tank_names.index(eso_from_tank_default)
                if eso_from_tank_default in eso_edit_from_tank_names else 0
            )
            eso_edit_from_tank_name = eso_ec2.selectbox(
                "From tank",
                eso_edit_from_tank_names,
                index=eso_from_tank_idx,
                key=f"eso_edit_from_tank_{selected_id}",
            )
            eso_edit_from_tank = next(
                (t for t in eso_edit_from_system_tanks if t["name"] == eso_edit_from_tank_name),
                None,
            )

            eso_eq1, eso_eq2, eso_eq3 = st.columns(3)
            eso_edit_qty = eso_eq1.number_input(
                "Fish count",
                min_value=1,
                value=max(1, int(selected_move.get("quantity_fish", 1))),
                step=10,
                key=f"eso_edit_qty_{selected_id}",
            )
            eso_edit_avg_w = eso_eq2.number_input(
                "Average weight (g)",
                min_value=0.1,
                value=max(0.1, float(selected_move.get("avg_weight_g", 100.0))),
                step=1.0,
                format="%.1f",
                key=f"eso_edit_avg_w_{selected_id}",
            )
            eso_edit_biomass = round(float(eso_edit_qty) * float(eso_edit_avg_w) / 1000, 3)
            eso_eq3.metric("Biomass (kg)", f"{eso_edit_biomass:.3f}")

            eso_edit_destination = st.text_input(
                "External destination",
                value=selected_move.get("external_destination", ""),
                placeholder="e.g. Processing plant, Another farm, Retailer…",
                key=f"eso_edit_destination_{selected_id}",
            )
            eso_edit_notes = st.text_area(
                "Notes",
                value=selected_move.get("notes", ""),
                height=68,
                key=f"eso_edit_notes_{selected_id}",
            )

            eso_col_save, eso_col_delete = st.columns(2)

            if eso_col_save.button(
                "Save changes", type="primary", key=f"save_movement_{selected_id}"
            ):
                eso_edit_errors = []
                if not eso_edit_from_tank:
                    eso_edit_errors.append("Select a source tank.")
                if int(eso_edit_qty) <= 0:
                    eso_edit_errors.append("Fish count must be greater than zero.")
                if float(eso_edit_avg_w) <= 0:
                    eso_edit_errors.append("Average weight must be greater than zero.")
                if not eso_edit_destination.strip():
                    eso_edit_errors.append("External destination is required.")

                if eso_edit_errors:
                    for err in eso_edit_errors:
                        st.error(err)
                else:
                    eso_updated = {
                        "id":            selected_id,
                        "date":          str(edit_date),
                        "movement_type": "external_stock_out",

                        "from_system_name": eso_edit_from_system,
                        "from_tank_id":     eso_edit_from_tank["id"] if eso_edit_from_tank else "",
                        "from_tank_name":   eso_edit_from_tank_name,

                        "to_system_name": "",
                        "to_tank_id":     None,
                        "to_tank_name":   "",

                        "destination":            "",
                        "external_destination":   eso_edit_destination.strip(),
                        "quantity_mode":          "fish",
                        "quantity_fish":          int(eso_edit_qty),
                        "quantity_kg":            float(eso_edit_biomass),
                        "avg_weight_g":           float(eso_edit_avg_w),

                        "batch_id":   selected_move.get("batch_id", ""),
                        "batch_name": selected_move.get("batch_name", ""),

                        "reason":       "",
                        "operator":     edit_operator.strip(),
                        "notes":        eso_edit_notes.strip(),
                        "tank_emptied": selected_move.get("tank_emptied", False),
                    }

                    updated_movements = [
                        eso_updated if m.get("id") == selected_id else m
                        for m in movements
                    ]
                    storage.save_movements(updated_movements)

                    storage.log_activity(
                        module="Movements",
                        action="update",
                        summary=(
                            f"Updated External Stock Out: {eso_edit_qty:,} fish "
                            f"from {eso_edit_from_tank_name} → {eso_edit_destination.strip()}"
                        ),
                        date=str(edit_date),
                        system_name=eso_edit_from_system,
                        tank_id=str(eso_edit_from_tank.get("id", "")) if eso_edit_from_tank else "",
                        tank_name=eso_edit_from_tank_name,
                        operator=edit_operator.strip(),
                        details={"movement_id": selected_id},
                    )
                    st.success("Movement updated.")
                    st.rerun()

            if eso_col_delete.button(
                "Delete movement", key=f"delete_movement_{selected_id}"
            ):
                updated_movements = [
                    m for m in movements if m.get("id") != selected_id
                ]
                storage.save_movements(updated_movements)
                storage.log_activity(
                    module="Movements",
                    action="delete",
                    summary=(
                        f"Deleted External Stock Out: "
                        f"{selected_move.get('quantity_fish', 0):,} fish "
                        f"from {selected_move.get('from_tank_name', '—')} "
                        f"→ {selected_move.get('external_destination', '—')}"
                    ),
                    date=selected_move.get("date", ""),
                    system_name=selected_move.get("from_system_name", ""),
                    tank_name=selected_move.get("from_tank_name", ""),
                    details={"movement_id": selected_id},
                )
                st.success("Movement deleted.")
                st.rerun()

        # ── Transfer / Harvest / Killing edit form ─────────────────────────
        else:
            c3, c4 = st.columns(2)

            from_system_default = selected_move.get("from_system_name") or system_names[0]
            from_system_idx = (
                system_names.index(from_system_default)
                if from_system_default in system_names else 0
            )

            edit_from_system = c3.selectbox(
                "From system",
                system_names,
                index=from_system_idx,
                key=f"edit_from_system_{selected_id}",
            )

            edit_from_system_tanks = [
                t for t in tanks if t.get("system_name", "—") == edit_from_system
            ]
            edit_from_tank_names = sorted([t["name"] for t in edit_from_system_tanks])

            from_tank_default = selected_move.get("from_tank_name")
            from_tank_idx = (
                edit_from_tank_names.index(from_tank_default)
                if from_tank_default in edit_from_tank_names else 0
            )

            edit_from_tank_name = c4.selectbox(
                "From tank",
                edit_from_tank_names,
                index=from_tank_idx,
                key=f"edit_from_tank_{selected_id}",
            )

            edit_from_tank = next(
                t for t in edit_from_system_tanks
                if t["name"] == edit_from_tank_name
            )

            edit_to_system   = ""
            edit_to_tank     = None
            edit_to_tank_name = ""

            if edit_type == "Transfer":
                c5, c6 = st.columns(2)

                to_system_default = selected_move.get("to_system_name") or system_names[0]
                to_system_idx = (
                    system_names.index(to_system_default)
                    if to_system_default in system_names else 0
                )

                edit_to_system = c5.selectbox(
                    "To system",
                    system_names,
                    index=to_system_idx,
                    key=f"edit_to_system_{selected_id}",
                )

                edit_to_system_tanks = [
                    t for t in tanks
                    if t.get("system_name", "—") == edit_to_system
                    and t.get("id") != edit_from_tank.get("id")
                ]

                if not edit_to_system_tanks:
                    st.warning("No available destination tanks for the selected system.")
                    return

                edit_to_tank_names = sorted([t["name"] for t in edit_to_system_tanks])

                to_tank_default = selected_move.get("to_tank_name")
                to_tank_idx = (
                    edit_to_tank_names.index(to_tank_default)
                    if to_tank_default in edit_to_tank_names else 0
                )

                edit_to_tank_name = c6.selectbox(
                    "To tank",
                    edit_to_tank_names,
                    index=to_tank_idx,
                    key=f"edit_to_tank_{selected_id}",
                )

                edit_to_tank = next(
                    t for t in edit_to_system_tanks
                    if t["name"] == edit_to_tank_name
                )

            edit_src_state = compute_tank_state(
                tank=edit_from_tank,
                batches=batches,
                daily_logs=daily_logs,
                movements=[m for m in movements if m.get("id") != selected_id],
                adjustments=adjustments,
            )

            edit_current_fish    = edit_src_state.get("fish_count", 0)   if edit_src_state else 0
            edit_current_biomass = edit_src_state.get("biomass_kg", 0.0) if edit_src_state else 0.0
            edit_avg_weight      = edit_src_state.get("avg_weight_g", 0.0) if edit_src_state else 0.0

            st.caption(
                f"Available after excluding selected record: "
                f"**{edit_current_fish:,} fish** | "
                f"**{edit_current_biomass:.1f} kg** | "
                f"**{edit_avg_weight:.1f} g avg weight**"
            )

            edit_batch_id   = edit_from_tank.get("batch_id")
            edit_batch_name = edit_from_tank.get("batch_name", "")

            if edit_batch_id or edit_batch_name:
                st.caption(f"Batch: **{edit_batch_name or edit_batch_id}**")

            edit_quantity_mode_default = (
                "Fish count"
                if selected_move.get("quantity_mode", "fish") == "fish"
                else "Biomass kg"
            )

            edit_quantity_mode = st.radio(
                "Quantity mode",
                ["Fish count", "Biomass kg"],
                horizontal=True,
                index=["Fish count", "Biomass kg"].index(edit_quantity_mode_default),
                key=f"edit_quantity_mode_{selected_id}",
            )

            if edit_quantity_mode == "Fish count":
                edit_quantity_fish = st.number_input(
                    "Fish quantity",
                    min_value=1,
                    value=max(1, int(selected_move.get("quantity_fish", 1))),
                    step=10,
                    key=f"edit_quantity_fish_{selected_id}",
                )
                edit_quantity_kg = (
                    float(edit_quantity_fish) * float(edit_avg_weight) / 1000
                    if edit_avg_weight > 0 else 0.0
                )
                st.caption(f"Estimated biomass moved: **{edit_quantity_kg:.2f} kg**")
            else:
                edit_quantity_kg = st.number_input(
                    "Biomass quantity (kg)",
                    min_value=0.0,
                    value=float(selected_move.get("quantity_kg", 0.0)),
                    step=0.5,
                    format="%.2f",
                    key=f"edit_quantity_kg_{selected_id}",
                )
                edit_quantity_fish = (
                    int(round(float(edit_quantity_kg) / (float(edit_avg_weight) / 1000)))
                    if edit_avg_weight > 0 else 0
                )
                st.caption(f"Estimated fish moved: **{edit_quantity_fish:,} fish**")

            if edit_type == "Transfer":
                _edit_reason_opts = ["Grading", "Stage transfer", "Emergency", "Splitting", "Other"]
                reason_default = selected_move.get("reason", _edit_reason_opts[0])
                reason_idx = (
                    _edit_reason_opts.index(reason_default)
                    if reason_default in _edit_reason_opts else 0
                )
                edit_reason = st.selectbox(
                    "Reason",
                    _edit_reason_opts,
                    index=reason_idx,
                    key=f"edit_reason_{selected_id}",
                )
            elif edit_type == "Harvest/Slaughter":
                st.caption("Reason: **Harvesting**")
                edit_reason = "Harvesting"
            else:  # Killing
                edit_reason = st.text_area(
                    "Reason",
                    value=selected_move.get("reason", ""),
                    height=68,
                    key=f"edit_reason_killing_{selected_id}",
                )

            edit_notes = st.text_area(
                "Notes",
                value=selected_move.get("notes", ""),
                height=68,
                key=f"edit_notes_{selected_id}",
            )

            col_save, col_delete = st.columns(2)

            if col_save.button(
                "Save changes", type="primary", key=f"save_movement_{selected_id}"
            ):
                if edit_quantity_fish <= 0:
                    st.error("Quantity must be greater than zero.")
                elif edit_type == "Killing" and not str(edit_reason).strip():
                    st.error("Please enter a reason for the killing.")
                elif edit_quantity_fish > edit_current_fish:
                    st.error(
                        f"Cannot move {edit_quantity_fish:,} fish — only "
                        f"{edit_current_fish:,} available in {edit_from_tank_name}."
                    )
                else:
                    updated_record = {
                        "id": selected_id,
                        "date": str(edit_date),

                        "movement_type": {
                            "Transfer":          "transfer",
                            "Harvest/Slaughter": "harvest",
                            "Killing":           "killing",
                        }.get(edit_type, "transfer"),

                        "from_system_name": edit_from_system,
                        "from_tank_id": edit_from_tank["id"],
                        "from_tank_name": edit_from_tank["name"],

                        "to_system_name": edit_to_system if edit_type == "Transfer" else "",
                        "to_tank_id": edit_to_tank["id"] if edit_to_tank else None,
                        "to_tank_name": edit_to_tank["name"] if edit_to_tank else "",

                        "destination": "Harvest/Slaughter"
                        if edit_type == "Harvest/Slaughter" else "",

                        "quantity_mode": "fish"
                        if edit_quantity_mode == "Fish count" else "kg",
                        "quantity_fish": int(edit_quantity_fish),
                        "quantity_kg": float(edit_quantity_kg),
                        "avg_weight_g": float(edit_avg_weight),

                        "batch_id": edit_batch_id,
                        "batch_name": edit_batch_name,

                        "reason": edit_reason,
                        "operator": edit_operator.strip(),
                        "notes": edit_notes.strip(),
                    }

                    updated_movements = [
                        updated_record if move.get("id") == selected_id else move
                        for move in movements
                    ]

                    storage.save_movements(updated_movements)
                    storage.log_activity(
                        module="Movements",
                        action="update",
                        summary=(
                            f"Updated {edit_type.lower()}: "
                            f"{edit_quantity_fish:,} fish from {edit_from_tank_name}"
                        ),
                        date=str(edit_date),
                        system_name=edit_from_system,
                        tank_id=str(edit_from_tank.get("id", "")),
                        tank_name=edit_from_tank_name,
                        operator=edit_operator.strip(),
                        details={"movement_id": selected_id},
                    )
                    st.success("Movement updated.")
                    st.rerun()

            if col_delete.button(
                "Delete movement", key=f"delete_movement_{selected_id}"
            ):
                updated_movements = [
                    move for move in movements
                    if move.get("id") != selected_id
                ]

                storage.save_movements(updated_movements)
                storage.log_activity(
                    module="Movements",
                    action="delete",
                    summary=(
                        f"Deleted movement: {selected_move.get('quantity_fish', 0):,} fish "
                        f"from {selected_move.get('from_tank_name', '—')}"
                    ),
                    date=selected_move.get("date", ""),
                    system_name=selected_move.get("from_system_name", ""),
                    tank_name=selected_move.get("from_tank_name", ""),
                    details={"movement_id": selected_id},
                )
                st.success("Movement deleted.")
                st.rerun()

    # ── Count Adjustments tab ─────────────────────────────────────────────────
    with tab_adjustments:
        if not adjustments:
            st.info("No count adjustments recorded yet.")
        else:
            df_adj = pd.DataFrame(adjustments)

            # ── Summary stats ──────────────────────────────────────────────────
            var_col   = df_adj["variance"] if "variance" in df_adj.columns else pd.Series([], dtype=int)
            total_pos = int(var_col[var_col > 0].sum()) if len(var_col) else 0
            total_neg = int(var_col[var_col < 0].sum()) if len(var_col) else 0
            net_adj   = int(var_col.sum()) if len(var_col) else 0

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Adjustments recorded", len(adjustments))
            m2.metric("Total positive",       f"+{total_pos:,}")
            m3.metric("Total negative",       f"{total_neg:,}")
            m4.metric("Net adjustment",       f"{net_adj:+,}")

            st.divider()

            # ── Date filter ────────────────────────────────────────────────────
            avail_dates = sorted(
                df_adj["date"].unique().tolist() if "date" in df_adj.columns else [],
                reverse=True,
            )
            sel_dates = st.multiselect(
                "Filter by date",
                avail_dates,
                default=avail_dates[:14],
                key="adj_date_filter",
            )
            if sel_dates and "date" in df_adj.columns:
                df_adj = df_adj[df_adj["date"].isin(sel_dates)]

            # ── Table ──────────────────────────────────────────────────────────
            display_cols = [
                "date", "tank_name", "system_name", "batch_name",
                "expected_count", "actual_count", "variance", "variance_pct",
                "note", "operator",
            ]
            present = [c for c in display_cols if c in df_adj.columns]
            st.dataframe(
                df_adj[present].sort_values("date", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

            csv_adj = df_adj[present].to_csv(index=False)
            st.download_button(
                "Download CSV",
                csv_adj,
                file_name=f"count_adjustments_{date.today()}.csv",
                mime="text/csv",
                key="adj_download",
            )

            st.divider()
            st.subheader("Delete adjustment record")

            adj_options = []
            adj_lookup  = {}
            for adj in sorted(adjustments, key=lambda x: x.get("date", ""), reverse=True):
                adj_id = adj.get("id")
                if not adj_id:
                    continue
                e = adj.get("expected_count", 0)
                a = adj.get("actual_count",  0)
                v = adj.get("variance",       0)
                label = (
                    f"{adj.get('date', '—')} | "
                    f"{adj.get('tank_name', '—')} | "
                    f"expected {e:,} → actual {a:,} ({v:+,} fish)"
                )
                adj_options.append(label)
                adj_lookup[label] = adj

            if adj_options:
                sel_adj_label = st.selectbox(
                    "Select adjustment",
                    adj_options,
                    key="adj_delete_select",
                )
                sel_adj = adj_lookup[sel_adj_label]

                if st.button("Delete adjustment", key="adj_delete_btn"):
                    updated_adj = [a for a in adjustments if a.get("id") != sel_adj.get("id")]
                    storage.save_adjustments(updated_adj)
                    storage.log_activity(
                        module="Movements",
                        action="delete",
                        summary=(
                            f"Deleted count adjustment for "
                            f"{sel_adj.get('tank_name', '—')}: "
                            f"variance {sel_adj.get('variance', 0):+,}"
                        ),
                        date=sel_adj.get("date", ""),
                        tank_name=sel_adj.get("tank_name", ""),
                        details={"adjustment_id": sel_adj.get("id")},
                    )
                    st.success("Adjustment record deleted.")
                    st.rerun()
