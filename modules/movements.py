"""
modules/movements.py
Movements — record fish transfers between tanks.
"""

import streamlit as st
import pandas as pd
from datetime import date
from core import storage
from core.stock_engine import get_all_tanks, compute_tank_state


# ─────────────────────────────────────────────────────────────────────────────
def render() -> None:
    st.header("Fish Movements")

    farm       = storage.load_farm()
    tanks      = get_all_tanks(farm)
    batches    = storage.load_batches()
    daily_logs = storage.load_daily_logs()
    movements  = storage.load_movements()

    if not tanks:
        st.warning("At least one tank is needed to record a movement.")
        return

    system_names = sorted({t.get("system_name", "—") for t in tanks})

    tab_new, tab_history = st.tabs(["Record movement", "History"])

    # ── New movement ───────────────────────────────────────────────────────
    with tab_new:
        movement_type = st.selectbox(
            "Movement type",
            ["Transfer", "Harvest / slaughter"],
        )

        col1, col2 = st.columns(2)
        mv_date = col1.date_input("Date", value=date.today())
        mv_operator = col2.text_input("Operator", placeholder="Name")

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
        )

        current_fish = src_state.get("fish_count", 0) if src_state else 0
        current_biomass = src_state.get("biomass_kg", 0.0) if src_state else 0.0
        avg_weight = src_state.get("avg_weight_g", 0.0) if src_state else 0.0

        st.caption(
            f"**{from_tank_name}** current stock: "
            f"{current_fish:,} fish | "
            f"{current_biomass:.1f} kg biomass | "
            f"{avg_weight:.1f} g avg weight"
        )

        batch_id = from_tank.get("batch_id")
        batch_name = from_tank.get("batch_name", "")

        if batch_id or batch_name:
            st.caption(f"Batch: **{batch_name or batch_id}**")

        to_system = ""
        to_tank = None
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
            to_tank_name = col6.selectbox("To tank", to_tank_names)
            to_tank = next(t for t in to_system_tanks if t["name"] == to_tank_name)

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
                min_value=0.1,
                value=10.0,
                step=0.5,
                format="%.2f",
            )
            quantity_fish = (
                int(round(float(quantity_kg) / (float(avg_weight) / 1000)))
                if avg_weight > 0 else 0
            )
            st.caption(f"Estimated fish moved: **{quantity_fish:,} fish**")

        reason_options = (
            ["Grading", "Thinning", "Stage transfer", "Emergency", "Other"]
            if movement_type == "Transfer"
            else ["Harvest", "Slaughter", "Culling", "Other"]
        )

        reason = st.selectbox("Reason", reason_options)
        mv_notes = st.text_area("Notes", height=68)

        submitted = st.button("Save movement", type="primary")

        if submitted:
            if quantity_fish <= 0:
                st.error("Quantity must be greater than zero.")
            elif quantity_fish > current_fish:
                st.error(
                    f"Cannot move {quantity_fish:,} fish — only "
                    f"{current_fish:,} available in {from_tank_name}."
                )
            else:
                record = {
                    "id": storage.new_id("mv_"),
                    "date": str(mv_date),

                    "movement_type": "transfer"
                    if movement_type == "Transfer" else "harvest",

                    "from_system_name": from_system,
                    "from_tank_id": from_tank["id"],
                    "from_tank_name": from_tank["name"],

                    "to_system_name": to_system if movement_type == "Transfer" else "",
                    "to_tank_id": to_tank["id"] if to_tank else None,
                    "to_tank_name": to_tank["name"] if to_tank else "",

                    "destination": "Slaughter"
                    if movement_type == "Harvest / slaughter" else "",

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

                if movement_type == "Transfer":
                    mv_summary = (
                        f"Created transfer: {quantity_fish:,} fish "
                        f"from {from_tank_name} to {to_tank_name}"
                    )
                    st.success(
                        f"Movement saved: {quantity_fish:,} fish from "
                        f"{from_tank_name} → {to_tank_name}."
                    )
                else:
                    mv_summary = (
                        f"Created harvest: {quantity_fish:,} fish from {from_tank_name}"
                    )
                    st.success(
                        f"Harvest saved: {quantity_fish:,} fish removed from "
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
        movement_lookup = {}

        for move in sorted(movements, key=lambda x: x.get("date", ""), reverse=True):
            move_id = move.get("id")
            if not move_id:
                continue

            label = (
                f"{move.get('date', '—')} | "
                f"{move.get('movement_type', 'transfer')} | "
                f"{move.get('from_tank_name', '—')} → "
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
        selected_id = selected_move.get("id")

        st.markdown("### Selected movement")

        edit_type_default = (
            "Transfer"
            if selected_move.get("movement_type", "transfer") == "transfer"
            else "Harvest / slaughter"
        )

        edit_type = st.selectbox(
            "Movement type",
            ["Transfer", "Harvest / slaughter"],
            index=["Transfer", "Harvest / slaughter"].index(edit_type_default),
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

        c3, c4 = st.columns(2)

        from_system_default = selected_move.get("from_system_name") or system_names[0]
        from_system_idx = system_names.index(from_system_default) if from_system_default in system_names else 0

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
        from_tank_idx = edit_from_tank_names.index(from_tank_default) if from_tank_default in edit_from_tank_names else 0

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

        edit_to_system = ""
        edit_to_tank = None
        edit_to_tank_name = ""

        if edit_type == "Transfer":
            c5, c6 = st.columns(2)

            to_system_default = selected_move.get("to_system_name") or system_names[0]
            to_system_idx = system_names.index(to_system_default) if to_system_default in system_names else 0

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
            to_tank_idx = edit_to_tank_names.index(to_tank_default) if to_tank_default in edit_to_tank_names else 0

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
            movements=[
                move for move in movements
                if move.get("id") != selected_id
            ],
        )

        edit_current_fish = edit_src_state.get("fish_count", 0) if edit_src_state else 0
        edit_current_biomass = edit_src_state.get("biomass_kg", 0.0) if edit_src_state else 0.0
        edit_avg_weight = edit_src_state.get("avg_weight_g", 0.0) if edit_src_state else 0.0

        st.caption(
            f"Available after excluding selected record: "
            f"**{edit_current_fish:,} fish** | "
            f"**{edit_current_biomass:.1f} kg** | "
            f"**{edit_avg_weight:.1f} g avg weight**"
        )

        edit_batch_id = edit_from_tank.get("batch_id")
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
                min_value=0.1,
                value=max(0.1, float(selected_move.get("quantity_kg", 0.1))),
                step=0.5,
                format="%.2f",
                key=f"edit_quantity_kg_{selected_id}",
            )
            edit_quantity_fish = (
                int(round(float(edit_quantity_kg) / (float(edit_avg_weight) / 1000)))
                if edit_avg_weight > 0 else 0
            )
            st.caption(f"Estimated fish moved: **{edit_quantity_fish:,} fish**")

        edit_reason_options = (
            ["Grading", "Thinning", "Stage transfer", "Emergency", "Other"]
            if edit_type == "Transfer"
            else ["Harvest", "Slaughter", "Culling", "Other"]
        )

        reason_default = selected_move.get("reason", edit_reason_options[0])
        reason_idx = edit_reason_options.index(reason_default) if reason_default in edit_reason_options else 0

        edit_reason = st.selectbox(
            "Reason",
            edit_reason_options,
            index=reason_idx,
            key=f"edit_reason_{selected_id}",
        )

        edit_notes = st.text_area(
            "Notes",
            value=selected_move.get("notes", ""),
            height=68,
            key=f"edit_notes_{selected_id}",
        )

        col_save, col_delete = st.columns(2)

        if col_save.button("Save changes", type="primary", key=f"save_movement_{selected_id}"):
            if edit_quantity_fish <= 0:
                st.error("Quantity must be greater than zero.")
            elif edit_quantity_fish > edit_current_fish:
                st.error(
                    f"Cannot move {edit_quantity_fish:,} fish — only "
                    f"{edit_current_fish:,} available in {edit_from_tank_name}."
                )
            else:
                updated_record = {
                    "id": selected_id,
                    "date": str(edit_date),

                    "movement_type": "transfer"
                    if edit_type == "Transfer" else "harvest",

                    "from_system_name": edit_from_system,
                    "from_tank_id": edit_from_tank["id"],
                    "from_tank_name": edit_from_tank["name"],

                    "to_system_name": edit_to_system if edit_type == "Transfer" else "",
                    "to_tank_id": edit_to_tank["id"] if edit_to_tank else None,
                    "to_tank_name": edit_to_tank["name"] if edit_to_tank else "",

                    "destination": "Slaughter"
                    if edit_type == "Harvest / slaughter" else "",

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
                        f"Updated {'transfer' if edit_type == 'Transfer' else 'harvest'}: "
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

        if col_delete.button("Delete movement", key=f"delete_movement_{selected_id}"):
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