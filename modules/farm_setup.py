"""
modules/farm_setup.py
Farm Setup — hierarchy: Farm → System → Tanks
"""

import streamlit as st
from core import storage


SYSTEM_TYPES = ["Growout", "Pregrow", "Hatchery", "Nursery"]

SPECIES = ["Perch", "Pike-perch", "European catfish"]

WQ_PARAMS = [
    ("oxygen",      "O₂",          "mg/L", True,  6.0,   12.0,  0.1,   "%.1f"),
    ("ph",          "pH",          "",     True,  6.8,   7.8,   0.05,  "%.2f"),
    ("temperature", "Temperature", "°C",   True,  10.0,  22.0,  0.5,   "%.1f"),
    ("salinity",    "Salinity",    "ppt",  False, 0.0,   5.0,   0.1,   "%.1f"),
    ("no2",         "NO₂",         "mg/L", True,  0.0,   0.1,   0.005, "%.3f"),
    ("no3",         "NO₃",         "mg/L", True,  0.0,   80.0,  1.0,   "%.1f"),
    ("nh3",         "NH₃",         "mg/L", False, 0.0,   0.02,  0.001, "%.3f"),
    ("nh4",         "NH₄",         "mg/L", True,  0.0,   0.5,   0.01,  "%.3f"),
    ("tan",         "TAN",         "mg/L", False, 0.0,   1.0,   0.01,  "%.2f"),
]


def safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _threshold_grid(system: dict, form_key: str) -> dict:
    th = system.get("thresholds", {})

    h_param, h_active, h_min, h_max = st.columns([2.2, 0.7, 1.8, 1.8])
    h_param.caption("**Parameter**")
    h_active.caption("**Active**")
    h_min.caption("**Min**")
    h_max.caption("**Max**")

    result = {}
    for key, label, unit, def_active, def_min, def_max, step, fmt in WQ_PARAMS:
        col_label, col_active, col_min, col_max = st.columns([2.2, 0.7, 1.8, 1.8])

        display = f"{label} ({unit})" if unit else label
        col_label.markdown(display)

        active = col_active.checkbox(
            "on",
            value=th.get(key, {}).get("active", def_active),
            key=f"{form_key}_active_{key}",
            label_visibility="collapsed",
        )
        v_min = col_min.number_input(
            "min",
            value=safe_float(th.get(key, {}).get("min"), def_min),
            step=step,
            format=fmt,
            key=f"{form_key}_min_{key}",
            label_visibility="collapsed",
        )
        v_max = col_max.number_input(
            "max",
            value=safe_float(th.get(key, {}).get("max"), def_max),
            step=step,
            format=fmt,
            key=f"{form_key}_max_{key}",
            label_visibility="collapsed",
        )
        result[key] = {"active": active, "min": v_min, "max": v_max}

    return result


def _find_system(farm: dict, system_id: str) -> dict | None:
    for system in farm.get("systems", []):
        if system.get("id") == system_id:
            return system
    return None


def _find_tank(system: dict, tank_id: str) -> dict | None:
    for tank in system.get("tanks", []):
        if tank.get("id") == tank_id:
            return tank
    return None


def _system_badge(system_type: str) -> str:
    return {
        "Growout":  "🔵",
        "Pregrow":  "🟢",
        "Hatchery": "🟡",
        "Nursery":  "🟠",
    }.get(system_type, "⚪")


def _render_system(farm: dict, sys: dict, batches: list) -> None:
    sys.setdefault("tanks", [])
    sys_tanks  = sys.get("tanks", [])
    tank_count = len(sys_tanks)

    badge  = _system_badge(sys.get("type", ""))
    header = f"{badge} {sys.get('name', 'Unnamed system')}  ·  {sys.get('type','—')}  ·  {tank_count} tank(s)"

    with st.expander(header, expanded=False):
        form_key = f"sys_{sys['id']}"

        with st.form(form_key, border=False):
            st.markdown("#### System settings")
            c1, c2 = st.columns([2.5, 1.5])

            new_name = c1.text_input("System name", value=sys.get("name", ""))
            new_type = c2.selectbox(
                "Type",
                SYSTEM_TYPES,
                index=SYSTEM_TYPES.index(sys.get("type", SYSTEM_TYPES[0]))
                if sys.get("type") in SYSTEM_TYPES else 0,
            )

            st.markdown("**Water parameter thresholds**")
            new_thresholds = _threshold_grid(sys, form_key)

            save_col, del_col = st.columns([2, 1])
            save_sys = save_col.form_submit_button(
                "💾 Save system",
                type="primary",
                use_container_width=True,
            )
            delete_sys = del_col.form_submit_button(
                "🗑 Delete system",
                use_container_width=True,
            )

        if save_sys:
            if not new_name.strip():
                st.error("System name cannot be empty.")
            else:
                target = _find_system(farm, sys["id"])
                if target:
                    target["name"]       = new_name.strip()
                    target["type"]       = new_type
                    target["thresholds"] = new_thresholds

                    for tank in target.get("tanks", []):
                        tank["system_id"]   = target["id"]
                        tank["system_name"] = target["name"]
                        tank["system_type"] = target["type"]

                storage.save_farm(farm)
                st.success("System saved.")
                st.rerun()

        if delete_sys:
            farm["systems"] = [
                system for system in farm.get("systems", [])
                if system.get("id") != sys["id"]
            ]
            storage.save_farm(farm)
            st.rerun()

        st.divider()

        st.markdown(f"#### Tanks ({tank_count})")

        for tank in sys_tanks:
            _render_tank_editor(farm, sys, tank, batches)

        st.divider()

        st.markdown("#### Add tank")
        _render_add_tank(farm, sys, batches)


def _render_tank_editor(farm: dict, sys: dict, tank: dict, batches: list) -> None:
    tid   = tank["id"]
    label = tank.get("name", "Unnamed tank")

    with st.expander(label, expanded=False):
        row1a, row1b = st.columns(2)

        new_name = row1a.text_input(
            "Tank name",
            value=tank.get("name", ""),
            key=f"{tid}_name",
        )

        # Fall back to index 0 for species not in the current list
        # (e.g. old records with "European perch" or "Atlantic salmon").
        saved_species = tank.get("species", "")
        species_idx   = SPECIES.index(saved_species) if saved_species in SPECIES else 0

        new_species = row1b.selectbox(
            "Species",
            SPECIES,
            index=species_idx,
            key=f"{tid}_species",
        )

        st.markdown("**Stock**")
        r2a, r2b, r2c = st.columns(3)

        new_fish = r2a.number_input(
            "Fish count",
            min_value=0,
            value=int(safe_float(tank.get("fish_count"), 0.0)),
            step=10,
            key=f"{tid}_fish",
        )

        new_avg_weight = r2b.number_input(
            "Avg weight (g)",
            min_value=0.1,
            value=safe_float(tank.get("avg_weight_g"), 100.0),
            step=1.0,
            format="%.1f",
            key=f"{tid}_weight",
        )

        new_feed = r2c.number_input(
            "Feed kg/day",
            min_value=0.0,
            value=safe_float(tank.get("feed_kg_day", tank.get("feed_kg_per_day")), 0.0),
            step=0.1,
            format="%.2f",
            key=f"{tid}_feed",
        )

        st.markdown("**Tank**")
        r3a, r3b = st.columns(2)

        new_volume = r3a.number_input(
            "Tank volume (m³)",
            min_value=0.1,
            value=max(0.1, safe_float(tank.get("tank_volume_m3", tank.get("volume_m3")), 10.0)),
            step=0.5,
            format="%.1f",
            key=f"{tid}_volume",
        )

        new_max_mort = r3b.number_input(
            "Max mortality/day (%)",
            min_value=0.0,
            max_value=100.0,
            value=safe_float(
                tank.get("max_mortality_percent_day", tank.get("max_mortality_pct")),
                0.5,
            ),
            step=0.05,
            format="%.2f",
            help="Alert threshold used in Daily Log and Dashboard",
            key=f"{tid}_maxmort",
        )

        calc_biomass = float(new_fish) * float(new_avg_weight) / 1000.0
        calc_density = calc_biomass / float(new_volume) if new_volume > 0 else 0.0

        st.markdown("**Calculated**")
        mc1, mc2 = st.columns(2)
        mc1.metric("Biomass (kg)", f"{calc_biomass:.1f}")
        mc2.metric("Density (kg/m³)", f"{calc_density:.2f}")

        st.markdown("**Batch composition**")
        active_batches = [b for b in batches if b.get("status") == "active"]

        batch_options = {
            b.get("id"): f"{b.get('name', 'Unnamed')} ({b.get('species', '—')})"
            for b in active_batches
        }

        if not active_batches:
            st.info("Create batches in **Batch Management** first.")
            new_batch_id       = None
            new_batch_name     = ""
            selected_batch_ids = []
            pct_map            = {}
        else:
            saved_comp = tank.get("initial_batch_composition", [])
            if saved_comp:
                default_ids = [
                    c["batch_id"] for c in saved_comp
                    if c.get("batch_id") and c["batch_id"] in batch_options
                ]
            elif tank.get("batch_id") and tank.get("batch_id") in batch_options:
                default_ids = [tank["batch_id"]]
            else:
                default_ids = []

            selected_batch_ids = st.multiselect(
                "Batches in this tank",
                options=list(batch_options.keys()),
                default=default_ids,
                format_func=lambda x: batch_options.get(x, x),
                key=f"{tid}_batch_multi",
            )

            pct_map = {}
            if not selected_batch_ids:
                st.caption("No batch assigned — tank will show as Unassigned.")
                new_batch_id   = None
                new_batch_name = ""
            elif len(selected_batch_ids) == 1:
                bid = selected_batch_ids[0]
                pct_map[bid]   = 100.0
                bobj = next((b for b in active_batches if b.get("id") == bid), None)
                new_batch_id   = bid
                new_batch_name = bobj.get("name", bid) if bobj else bid
                if bobj:
                    st.caption(
                        f"**{new_batch_name}** · {bobj.get('species','—')} · "
                        f"Start: {bobj.get('start_date','—')} · "
                        f"Initial: {bobj.get('initial_fish_count', 0):,} fish"
                    )
            else:
                saved_pcts = {
                    c["batch_id"]: float(c.get("percentage", 0))
                    for c in saved_comp if c.get("batch_id")
                }
                pct_cols = st.columns(len(selected_batch_ids))
                for i, bid in enumerate(selected_batch_ids):
                    default_pct = saved_pcts.get(bid, round(100.0 / len(selected_batch_ids), 1))
                    short_name  = batch_options.get(bid, bid).split(" (")[0][:18]
                    pct_map[bid] = pct_cols[i].number_input(
                        f"{short_name} %",
                        min_value=0.0,
                        max_value=100.0,
                        value=float(default_pct),
                        step=1.0,
                        format="%.1f",
                        key=f"{tid}_pct_{bid}",
                    )
                total_pct = sum(pct_map.values())
                if abs(total_pct - 100.0) > 0.01:
                    st.warning(f"Percentages sum to {total_pct:.1f}% — must equal 100%.")
                else:
                    st.caption(f"Total: {total_pct:.1f}% ✓")
                new_batch_id   = None
                new_batch_name = "Mixed"

        st.markdown("**O₂ thresholds**")
        o2a, o2b = st.columns(2)

        new_o2_min = o2a.number_input(
            "O₂ min (mg/L)",
            min_value=0.0,
            value=safe_float(tank.get("o2_min"), 6.0),
            step=0.1,
            format="%.1f",
            key=f"{tid}_o2min",
        )

        new_o2_max = o2b.number_input(
            "O₂ max (mg/L)",
            min_value=0.0,
            value=safe_float(tank.get("o2_max"), 12.0),
            step=0.1,
            format="%.1f",
            key=f"{tid}_o2max",
        )

        btn_save, btn_del = st.columns([3, 1])

        if btn_save.button(
            "💾 Save tank",
            key=f"{tid}_save",
            type="primary",
            use_container_width=True,
        ):
            if not new_name.strip():
                st.error("Tank name cannot be empty.")
            elif (
                selected_batch_ids
                and len(selected_batch_ids) > 1
                and abs(sum(pct_map.values()) - 100.0) > 0.01
            ):
                st.error(
                    f"Batch percentages must sum to 100% "
                    f"(currently {sum(pct_map.values()):.1f}%)."
                )
            else:
                selected_system = _find_system(farm, sys["id"])
                target_tank = _find_tank(selected_system, tid) if selected_system else None

                if target_tank is not None:
                    if len(selected_batch_ids) == 1:
                        _sbobj = next(
                            (b for b in active_batches if b.get("id") == selected_batch_ids[0]),
                            None,
                        )
                        resolved_species = _sbobj.get("species", new_species) if _sbobj else new_species
                    else:
                        resolved_species = new_species

                    if selected_batch_ids:
                        initial_batch_comp = [
                            {
                                "batch_id":   bid,
                                "batch_name": next(
                                    (b.get("name", bid) for b in active_batches if b.get("id") == bid),
                                    bid,
                                ),
                                "percentage": round(float(pct_map.get(bid, 100.0)), 2),
                                "fish_count": round(float(new_fish) * pct_map.get(bid, 100.0) / 100),
                            }
                            for bid in selected_batch_ids
                        ]
                    else:
                        initial_batch_comp = []

                    target_tank.update({
                        "id":          tid,
                        "name":        new_name.strip(),
                        "system_id":   sys["id"],
                        "system_name": sys.get("name", ""),
                        "system_type": sys.get("type", ""),
                        "species":     resolved_species,

                        "fish_count":   float(new_fish),
                        "avg_weight_g": float(new_avg_weight),
                        "biomass_kg":   calc_biomass,

                        "feed_kg_day": float(new_feed),

                        "tank_volume_m3": float(new_volume),

                        "max_mortality_percent_day": float(new_max_mort),

                        "o2_min": float(new_o2_min),
                        "o2_max": float(new_o2_max),

                        "batch_id":   new_batch_id,
                        "batch_name": new_batch_name,

                        "initial_batch_composition": initial_batch_comp,
                        # grading_status / grading_notes are intentionally omitted:
                        # existing values in the record are preserved as-is.
                    })

                    storage.save_farm(farm)
                    storage.log_activity(
                        module="Farm Setup",
                        action="update",
                        summary=f"Updated tank {new_name.strip()}",
                        tank_id=tid,
                        tank_name=new_name.strip(),
                        system_name=sys.get("name", ""),
                    )
                    st.success(f"Tank '{new_name.strip()}' saved.")
                    st.rerun()

        if btn_del.button("🗑 Delete", key=f"{tid}_del", use_container_width=True):
            selected_system = _find_system(farm, sys["id"])
            if selected_system:
                selected_system["tanks"] = [
                    item for item in selected_system.get("tanks", [])
                    if item.get("id") != tid
                ]
                storage.save_farm(farm)
            st.rerun()


def _render_add_tank(farm: dict, sys: dict, batches: list) -> None:
    active_batches = [b for b in batches if b.get("status") == "active"]

    with st.form(f"new_tank_{sys['id']}", clear_on_submit=True):
        row1a, row1b = st.columns(2)
        t_name    = row1a.text_input("Tank name", placeholder="e.g. Tank 01")
        t_species = row1b.selectbox("Species", SPECIES)

        st.markdown("**Stock**")
        row2a, row2b, row2c = st.columns(3)

        t_fish = row2a.number_input("Fish count", min_value=0, value=0, step=10)
        t_avg_weight = row2b.number_input(
            "Avg weight (g)",
            min_value=0.1,
            value=100.0,
            step=1.0,
            format="%.1f",
        )
        t_feed = row2c.number_input(
            "Feed kg/day",
            min_value=0.0,
            value=0.0,
            step=0.1,
            format="%.2f",
        )

        st.markdown("**Tank**")
        row3a, row3b = st.columns(2)

        t_volume = row3a.number_input(
            "Tank volume (m³)",
            min_value=0.1,
            value=10.0,
            step=0.5,
            format="%.1f",
        )
        t_max_mort = row3b.number_input(
            "Max mortality/day (%)",
            min_value=0.0,
            value=0.5,
            step=0.05,
            format="%.2f",
        )

        st.markdown("**O₂ thresholds**")
        row4a, row4b = st.columns(2)

        t_o2_min = row4a.number_input(
            "O₂ min (mg/L)",
            min_value=0.0,
            value=6.0,
            step=0.1,
            format="%.1f",
        )
        t_o2_max = row4b.number_input(
            "O₂ max (mg/L)",
            min_value=0.0,
            value=12.0,
            step=0.1,
            format="%.1f",
        )

        st.markdown("**Batch composition**")
        if active_batches:
            batch_labels = ["— None —"] + [
                f"{b.get('name', b.get('batch_name', 'Unnamed batch'))} ({b.get('species','—')})"
                for b in active_batches
            ]
            t_batch_idx = st.selectbox(
                "Initial batch (optional)",
                range(len(batch_labels)),
                format_func=lambda i: batch_labels[i],
            )
        else:
            st.caption("No active batches — create one in Batch Management first.")
            t_batch_idx = 0

        submitted = st.form_submit_button("➕ Add tank", type="primary")

        if submitted:
            if not t_name.strip():
                st.error("Tank name is required.")
            else:
                fish_f       = float(t_fish)
                weight_f     = float(t_avg_weight)
                calc_biomass = fish_f * weight_f / 1000.0

                sel_batch = (
                    active_batches[t_batch_idx - 1]
                    if active_batches and t_batch_idx > 0 else None
                )

                resolved_species = sel_batch.get("species", t_species) if sel_batch else t_species

                _t_batch_id   = sel_batch.get("id") if sel_batch else None
                _t_batch_name = (
                    sel_batch.get("name") or sel_batch.get("batch_name", "")
                    if sel_batch else ""
                )
                _initial_batch_comp = (
                    [{
                        "batch_id":   _t_batch_id,
                        "batch_name": _t_batch_name,
                        "percentage": 100.0,
                        "fish_count": int(fish_f),
                    }]
                    if sel_batch else []
                )

                new_tank = {
                    "id":          storage.new_id("tank_"),
                    "name":        t_name.strip(),
                    "system_id":   sys["id"],
                    "system_name": sys.get("name", ""),
                    "system_type": sys.get("type", ""),
                    "species":     resolved_species,

                    "fish_count":   fish_f,
                    "avg_weight_g": weight_f,
                    "biomass_kg":   calc_biomass,

                    "feed_kg_day": float(t_feed),

                    "tank_volume_m3": float(t_volume),

                    "max_mortality_percent_day": float(t_max_mort),

                    "o2_min": float(t_o2_min),
                    "o2_max": float(t_o2_max),

                    "batch_id":   _t_batch_id,
                    "batch_name": _t_batch_name,

                    "initial_batch_composition": _initial_batch_comp,
                }

                target_system = _find_system(farm, sys["id"])
                if target_system is not None:
                    target_system.setdefault("tanks", [])
                    target_system["tanks"].append(new_tank)

                storage.save_farm(farm)
                st.success(f"Tank '{t_name.strip()}' added to {sys.get('name', '')}.")
                st.rerun()


def _render_add_system(farm: dict) -> None:
    with st.expander("➕ Add new system", expanded=False):
        with st.form("add_system", clear_on_submit=True):
            c1, c2    = st.columns(2)
            sys_name  = c1.text_input("System name", placeholder="e.g. Growout A")
            sys_type  = c2.selectbox("Type", SYSTEM_TYPES)
            submitted = st.form_submit_button("Create system", type="primary")

        if submitted:
            if not sys_name.strip():
                st.error("System name is required.")
            else:
                default_th = {
                    key: {"active": def_active, "min": def_min, "max": def_max}
                    for key, _, _, def_active, def_min, def_max, _, _ in WQ_PARAMS
                }

                systems = farm.get("systems", [])
                systems.append({
                    "id":         storage.new_id("sys_"),
                    "name":       sys_name.strip(),
                    "type":       sys_type,
                    "thresholds": default_th,
                    "tanks":      [],
                })

                farm["systems"] = systems
                storage.save_farm(farm)
                st.success(
                    f"System '{sys_name.strip()}' created. "
                    "Open it below to configure thresholds and add tanks."
                )
                st.rerun()


def _migrate_farm(farm: dict) -> dict:
    changed = False

    farm.setdefault("systems", [])

    for sys in farm.get("systems", []):
        if "thresholds" not in sys:
            sys["thresholds"] = {
                key: {"active": def_active, "min": def_min, "max": def_max}
                for key, _, _, def_active, def_min, def_max, _, _ in WQ_PARAMS
            }
            changed = True

        if "tanks" not in sys:
            sys["tanks"] = []
            changed = True

    # Move legacy root-level tanks into their matching system.
    legacy_tanks = farm.get("tanks", [])

    if legacy_tanks:
        for tank in legacy_tanks:
            system_id     = tank.get("system_id")
            target_system = _find_system(farm, system_id) if system_id else None

            if target_system is None and farm.get("systems"):
                target_system = farm["systems"][0]

            if target_system is not None:
                target_system.setdefault("tanks", [])

                existing_ids = {
                    item.get("id") for item in target_system.get("tanks", [])
                }

                if tank.get("id") not in existing_ids:
                    tank["system_id"]   = target_system.get("id")
                    tank["system_name"] = target_system.get("name", "")
                    tank["system_type"] = target_system.get("type", "")
                    target_system["tanks"].append(tank)
                    changed = True

        farm["tanks"] = []
        changed = True

    for sys in farm.get("systems", []):
        for tank in sys.get("tanks", []):
            if "fish_count" not in tank:
                old_biomass = safe_float(tank.get("biomass_kg"), 0.0)
                old_weight  = safe_float(tank.get("avg_weight_g"), 100.0)

                if old_weight > 0 and old_biomass > 0:
                    tank["fish_count"] = old_biomass / (old_weight / 1000.0)
                else:
                    tank["fish_count"] = 0.0

                changed = True

            fish_c   = safe_float(tank.get("fish_count"), 0.0)
            weight_c = safe_float(tank.get("avg_weight_g"), 100.0)
            tank["biomass_kg"] = fish_c * weight_c / 1000.0

            # Normalize old alias fields into canonical field names.
            if "feed_kg_day" not in tank and "feed_kg_per_day" in tank:
                tank["feed_kg_day"] = safe_float(tank.get("feed_kg_per_day"), 0.0)
                changed = True

            if "tank_volume_m3" not in tank and "volume_m3" in tank:
                tank["tank_volume_m3"] = safe_float(tank.get("volume_m3"), 10.0)
                changed = True

            if "max_mortality_percent_day" not in tank and "max_mortality_pct" in tank:
                tank["max_mortality_percent_day"] = safe_float(tank.get("max_mortality_pct"), 0.5)
                changed = True

            defaults = {
                "avg_weight_g":             100.0,
                "feed_kg_day":              0.0,
                "tank_volume_m3":           10.0,
                "max_mortality_percent_day": 0.5,
                "o2_min":                   6.0,
                "o2_max":                   12.0,
                "batch_id":                 None,
                "batch_name":               "",
                "initial_batch_composition": [],
                # grading_status / grading_notes: intentionally not defaulted here.
                # Old records that already carry these fields are left untouched.
                # New records simply won't have them, which is fine.
            }

            for field, default in defaults.items():
                if field not in tank or tank.get(field) is None:
                    tank[field] = default
                    changed = True

            # Remove old duplicate aliases.
            for old_field in ["feed_kg_per_day", "volume_m3", "max_mortality_pct"]:
                if old_field in tank:
                    tank.pop(old_field, None)
                    changed = True

            tank["system_id"]   = sys.get("id")
            tank["system_name"] = sys.get("name", "")
            tank["system_type"] = sys.get("type", "")

    if changed:
        storage.save_farm(farm)

    return farm


def render() -> None:
    st.header("Farm Setup")

    farm    = storage.load_farm()
    farm    = _migrate_farm(farm)
    batches = storage.load_batches()

    with st.expander("Farm profile", expanded=not bool(farm.get("farm_name"))):
        with st.form("farm_name_form"):
            farm_name = st.text_input("Farm name", value=farm.get("farm_name", ""))
            if st.form_submit_button("Save"):
                farm["farm_name"] = farm_name.strip()
                farm["name"]      = farm_name.strip()
                storage.save_farm(farm)
                st.success("Farm name saved.")
                st.rerun()

    st.divider()

    _render_add_system(farm)

    st.divider()

    systems = farm.get("systems", [])
    if not systems:
        st.info("No systems yet. Use **Add new system** above to get started.")
        return

    for sys in systems:
        _render_system(farm, sys, batches)
