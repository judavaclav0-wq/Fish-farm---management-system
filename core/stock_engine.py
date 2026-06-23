"""
core/stock_engine.py

Live Stock Engine.
Computes current tank state from Farm Setup + batches + daily logs + movements.

Supports historical calculation via as_of_date.

Batch composition model
-----------------------
A tank can hold fish from *multiple* batches simultaneously.
The engine replays all events (movements, mortality) in date order to track
how many fish of each batch currently reside in each tank.

Key new API:
  - proportional_split(total, composition)          – deterministic integer split
  - _compute_all_batch_compositions(...)            – full replay engine
  - compute_batch_summary(...)                      – per-batch roll-up
  - compute_tank_state(... batch_compositions=None) – adds batch_composition list

Backward compatibility
----------------------
- All existing return fields of compute_tank_state are unchanged.
- When batch_compositions is not provided, a single-batch fallback is used.
- Old records with one batch_id per tank continue to work correctly.

No Streamlit imports here.
"""

from __future__ import annotations
from typing import Any

from core.calculations import (
    safe_float,
    safe_int,
    calculate_biomass_kg,
    calculate_density_kg_m3,
)


# ─────────────────────────────────────────────────────────────────────────────
# Farm-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_all_tanks(farm_data: dict) -> list[dict]:
    """Extract all tanks from farm_data systems."""
    tanks = []
    for system in farm_data.get("systems", []):
        system_name = system.get("name", "")
        system_type = system.get("type", "")
        for tank in system.get("tanks", []):
            tank_copy = dict(tank)
            tank_copy["system_name"] = system_name
            tank_copy["system_type"] = system_type
            tanks.append(tank_copy)
    return tanks


def find_batch_for_tank(tank: dict, batches: list[dict]) -> dict | None:
    """Find assigned batch by batch_id (legacy single-batch lookup)."""
    batch_id = tank.get("batch_id")
    if not batch_id:
        return None
    for batch in batches:
        if batch.get("id") == batch_id or batch.get("batch_id") == batch_id:
            return batch
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation / matching utilities
# ─────────────────────────────────────────────────────────────────────────────

def _norm(value: Any) -> str:
    """Normalise IDs/names for safer matching (strips whitespace)."""
    if value is None:
        return ""
    return str(value).strip()


def _same_tank_id(value: Any, tank_id: str) -> bool:
    return _norm(value) != "" and _norm(value) == _norm(tank_id)


def _same_tank_fallback(value: Any, tank: dict, tank_id: str) -> bool:
    value_n = _norm(value)
    possible_refs = {
        _norm(tank_id),
        _norm(tank.get("id")),
        _norm(tank.get("tank_id")),
        _norm(tank.get("name")),
        _norm(tank.get("tank_name")),
    }
    return value_n != "" and value_n in possible_refs


def _matches_tank(move: dict, tank: dict, tank_id: str, prefix: str) -> bool:
    """
    Robustly match a movement's tank reference to a given tank.
    prefix = "from" or "to"
    """
    return (
        _same_tank_id(move.get(f"{prefix}_tank_id"), tank_id)
        or _same_tank_fallback(move.get(f"{prefix}_tank"), tank, tank_id)
        or _same_tank_fallback(move.get(f"{prefix}_tank_name"), tank, tank_id)
    )


def _movement_type(move: dict) -> str:
    """Normalise movement type (handles legacy values).

    "killing" maps to "harvest" so it reduces source stock without adding to a
    destination.  The raw stored value "killing" is what the dashboard uses to
    exclude killing records from the Harvest Overview.
    """
    raw = str(move.get("movement_type", "transfer") or "transfer").strip().lower()
    if raw in {"harvest", "harvest / slaughter", "harvest/slaughter",
               "slaughter", "culling", "killing"}:
        return "harvest"
    return "transfer"


def _movement_quantity_fish(move: dict) -> int:
    return safe_int(move.get("quantity_fish", move.get("quantity")), 0)


def normalize_empty_tank(tank: dict) -> dict:
    """
    Reset all stock-related fields on a tank dict when it holds no fish.

    Works on both Farm-Setup tank dicts and computed state dicts.
    Returns the same dict (mutated in place) for convenience.

    Rules:
      - fish_count <= 0  OR  biomass_kg <= 0  → tank is considered empty.
      - Clears: fish_count, biomass_kg, avg_weight_g, density_kg_m3,
                batch_id, batch_name, batch_composition.
      - Does NOT clear tank identity fields (id, name, volume, etc.).
    """
    fish    = safe_float(tank.get("fish_count"), 0.0)
    biomass = safe_float(tank.get("biomass_kg"), 0.0)
    if fish <= 0 or biomass <= 0:
        tank["fish_count"]        = 0
        tank["biomass_kg"]        = 0.0
        tank["avg_weight_g"]      = 0.0
        tank["density_kg_m3"]     = 0.0
        tank["batch_id"]          = None
        tank["batch_name"]        = ""
        tank["batch_composition"] = []
    return tank


def _filter_by_date(records: list[dict], as_of_date: str | None) -> list[dict]:
    if not as_of_date:
        return records
    return [r for r in records if r.get("date") and r.get("date") <= as_of_date]


def _clean_composition(composition: dict[str, int], min_fish: int = 1) -> dict[str, int]:
    """
    Remove stale / zero entries from a batch composition dict.

    Rules (applied in order):
      1. Convert every value to int (guard against float drift).
      2. Remove entries where fish_count <= 0.
      3. Remove "unassigned" entries (empty batch_id) where fish_count <= min_fish
         — a single unassigned fish after a proportional split is almost always a
         rounding artefact, not a real fish with no batch.

    Named batches (non-empty batch_id) are ONLY removed when their count is
    zero or negative — even 1 genuine fish of a named batch is kept.

    Args:
        composition: {batch_id: fish_count}  (mutated copy returned)
        min_fish:    threshold for dropping unassigned dust (default 1)

    Returns:
        Cleaned {batch_id: fish_count} dict.
    """
    result: dict[str, int] = {}
    for bid, count in composition.items():
        count = max(0, int(count))           # normalise to non-negative int
        if count <= 0:
            continue                          # always drop zero/negative
        if not bid and count <= min_fish:
            continue                          # drop unassigned rounding dust
        result[bid] = count
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Batch composition helpers (NEW)
# ─────────────────────────────────────────────────────────────────────────────

def _get_batch_name(batch_id: str, batches: list[dict]) -> str:
    """Return the human-readable name for a batch_id.
    Returns "Unassigned" for empty IDs, the ID itself if not found.
    """
    if not batch_id:
        return "Unassigned"
    for b in batches:
        if b.get("id") == batch_id or b.get("batch_id") == batch_id:
            return b.get("name") or b.get("batch_name") or batch_id
    return batch_id  # fallback: display the raw ID


def _build_tank_lookup(farm_data: dict) -> dict[str, tuple[str, dict]]:
    """
    Build a lookup: normalised_key -> (canonical_tank_id, tank_obj).
    Registers both by canonical ID and by tank name so both can resolve
    movement references quickly.
    """
    lookup: dict[str, tuple[str, dict]] = {}
    for tank in get_all_tanks(farm_data):
        canonical = tank.get("id") or tank.get("tank_id") or tank.get("name")
        if not canonical:
            continue
        key_id   = _norm(canonical)
        key_name = _norm(tank.get("name", ""))
        # canonical ID wins; name is registered only if the slot is still free
        lookup[key_id] = (canonical, tank)
        if key_name and key_name not in lookup:
            lookup[key_name] = (canonical, tank)
    return lookup


def _resolve_tank(move: dict, prefix: str, lookup: dict) -> str | None:
    """
    Resolve a movement's 'from' or 'to' reference to a canonical tank ID.
    Tries {prefix}_tank_id, {prefix}_tank, {prefix}_tank_name in order.
    """
    for field in (f"{prefix}_tank_id", f"{prefix}_tank", f"{prefix}_tank_name"):
        val = _norm(move.get(field, ""))
        if val and val in lookup:
            return lookup[val][0]
    return None


def proportional_split(total: int, composition: dict[str, int]) -> dict[str, int]:
    """
    Split `total` fish proportionally across batches in `composition`.

    Uses the largest-remainder (Hamilton) method so the result always sums
    exactly to min(total, sum(composition.values())).

    Args:
        total:        Number of fish to distribute.
        composition:  {batch_id: current_fish_count}

    Returns:
        {batch_id: fish_allocated}  (only non-zero entries)
    """
    if not composition or total <= 0:
        return {}

    total_fish = sum(composition.values())
    if total_fish <= 0:
        return {}

    actual = min(total, total_fish)

    # Floor allocation
    result: dict[str, int] = {}
    remainders: dict[str, float] = {}
    for bid, count in composition.items():
        raw          = actual * count / total_fish
        floored      = int(raw)
        result[bid]  = floored
        remainders[bid] = raw - floored

    # Distribute shortfall via largest-remainder (deterministic: break ties by id)
    shortfall  = actual - sum(result.values())
    sorted_bids = sorted(remainders, key=lambda x: (-remainders[x], x))
    for i in range(shortfall):
        result[sorted_bids[i]] += 1

    return {k: v for k, v in result.items() if v > 0}


def _compute_all_batch_compositions(
    farm_data:   dict,
    batches:     list[dict],
    daily_logs:  list[dict],
    movements:   list[dict],
    as_of_date:  str | None = None,
    adjustments: list[dict] | None = None,
) -> dict:
    """
    Replay all events in date order to compute current batch composition per tank.

    Algorithm:
      1. Initialise each tank from farm_setup (fish_count / batch_id).
      2. For each date in order:
         a. Apply movements: split proportionally, subtract from source,
            add to destination (transfers only; harvest removes fish entirely).
         b. Apply mortality: split proportionally, subtract from each tank.

    Returns:
        {
          "state":    {canonical_tank_id: {batch_id: fish_count}},
          "mortality": {batch_id: total_fish_that_died_from_daily_mortality},
        }
    """
    tank_lookup = _build_tank_lookup(farm_data)

    # ── Step 1: Initialise state from farm setup ────────────────────────────
    # state[tank_id][batch_id] = current fish count for that batch in that tank
    state: dict[str, dict[str, int]] = {}
    seen_canonical: set[str] = set()

    for _, (canonical_id, tank) in tank_lookup.items():
        if canonical_id in seen_canonical:
            continue
        seen_canonical.add(canonical_id)

        fish = safe_int(tank.get("fish_count"), 0)
        if fish <= 0:
            state[canonical_id] = {}
            continue

        # Priority A: initial_batch_composition (multi-batch capable)
        ibc = tank.get("initial_batch_composition")
        if ibc and isinstance(ibc, list):
            comp: dict[str, int] = {}
            for entry in ibc:
                entry_bid = entry.get("batch_id") or ""
                pct = float(entry.get("percentage") or 0)
                entry_fish = round(fish * pct / 100)
                if entry_fish > 0:
                    comp[entry_bid] = comp.get(entry_bid, 0) + entry_fish
            # Absorb rounding shortfall into the largest entry so total == fish
            total = sum(comp.values())
            diff  = fish - total
            if diff != 0 and comp:
                largest = max(comp, key=lambda k: comp[k])
                comp[largest] = max(0, comp[largest] + diff)
                comp = {k: v for k, v in comp.items() if v > 0}
            state[canonical_id] = comp if comp else {"": fish}
        else:
            # Priority B: legacy single batch_id
            bid = tank.get("batch_id") or ""
            state[canonical_id] = {bid: fish}

    # Tracks total fish lost to daily-log mortality (not harvest) per batch
    batch_mortality: dict[str, int] = {}

    # ── Step 2: Filter events by as_of_date ────────────────────────────────
    filtered_moves = _filter_by_date(movements,          as_of_date)
    filtered_logs  = _filter_by_date(daily_logs,         as_of_date)
    filtered_adjs  = _filter_by_date(adjustments or [], as_of_date)

    adj_by_date: dict[str, list] = {}
    for adj in filtered_adjs:
        d = adj.get("date", "")
        if d:
            adj_by_date.setdefault(d, []).append(adj)

    # ── Step 3: Collect all event dates (sorted) ───────────────────────────
    all_dates = sorted(set(
        [m.get("date", "") for m in filtered_moves if m.get("date")]
        + [l.get("date", "") for l in filtered_logs  if l.get("date")]
        + list(adj_by_date.keys())
    ))

    # Group movements by date
    moves_by_date: dict[str, list] = {}
    for m in filtered_moves:
        d = m.get("date", "")
        if d:
            moves_by_date.setdefault(d, []).append(m)

    # Aggregate mortality per (date, canonical_tank_id)
    tank_mort_by_date: dict[str, dict[str, int]] = {}
    for log in filtered_logs:
        d = log.get("date", "")
        if not d:
            continue
        # Resolve tank reference to canonical ID
        tank_ref = _norm(log.get("tank_id", "")) or _norm(log.get("tank_name", ""))
        cid = None
        if tank_ref and tank_ref in tank_lookup:
            cid = tank_lookup[tank_ref][0]
        if not cid:
            continue
        mort = safe_int(log.get("mortality_fish", log.get("mortality")), 0)
        if mort > 0:
            tank_mort_by_date.setdefault(d, {})
            tank_mort_by_date[d][cid] = tank_mort_by_date[d].get(cid, 0) + mort

    # ── Step 4: Replay events date by date ─────────────────────────────────
    for date_str in all_dates:

        # ── 4a. Movements (process before mortality for the same date) ──────
        for move in moves_by_date.get(date_str, []):
            qty = safe_int(move.get("quantity_fish", move.get("quantity")), 0)
            if qty <= 0:
                continue

            move_type = _movement_type(move)
            from_id   = _resolve_tank(move, "from", tank_lookup)
            if not from_id:
                continue

            from_comp  = state.get(from_id, {})
            total_from = sum(from_comp.values())
            if total_from <= 0:
                continue

            # Cap at available fish; split proportionally
            actual_qty = min(qty, total_from)
            split = proportional_split(actual_qty, from_comp)

            # Subtract from source tank, then clean up rounding dust
            for bid, cnt in split.items():
                from_comp[bid] = max(0, from_comp.get(bid, 0) - cnt)
            state[from_id] = _clean_composition(from_comp)

            # Add to destination (transfers only; harvests remove fish entirely)
            if move_type == "transfer":
                to_id = _resolve_tank(move, "to", tank_lookup)
                if to_id:
                    to_comp = state.setdefault(to_id, {})
                    for bid, cnt in split.items():
                        to_comp[bid] = to_comp.get(bid, 0) + cnt
                    # (no need to remove zeros — additions are always > 0)

        # ── 4b. Mortality ────────────────────────────────────────────────────
        for tank_id, total_mort in tank_mort_by_date.get(date_str, {}).items():
            if total_mort <= 0:
                continue

            tank_comp  = state.get(tank_id, {})
            total_fish = sum(tank_comp.values())
            if total_fish <= 0:
                continue

            actual_mort = min(total_mort, total_fish)
            split = proportional_split(actual_mort, tank_comp)

            for bid, cnt in split.items():
                tank_comp[bid] = max(0, tank_comp.get(bid, 0) - cnt)
                batch_mortality[bid] = batch_mortality.get(bid, 0) + cnt

            # Clean up after mortality — remove zero and unassigned-dust entries
            state[tank_id] = _clean_composition(tank_comp)

        # ── 4c. Stock adjustments (count reconciliation) ─────────────────────
        for adj in adj_by_date.get(date_str, []):
            adj_ref = _norm(adj.get("tank_id", "")) or _norm(adj.get("tank_name", ""))
            cid = tank_lookup.get(adj_ref, (None, None))[0] if adj_ref else None
            if not cid:
                continue
            variance = safe_int(adj.get("variance"), 0)
            if variance == 0:
                continue
            tank_comp  = state.get(cid, {})
            total_fish = sum(tank_comp.values())
            if variance < 0:
                reduction = min(abs(variance), total_fish)
                if reduction > 0:
                    split = proportional_split(reduction, tank_comp)
                    for bid, cnt in split.items():
                        tank_comp[bid] = max(0, tank_comp.get(bid, 0) - cnt)
            elif total_fish > 0:
                split = proportional_split(variance, tank_comp)
                for bid, cnt in split.items():
                    tank_comp[bid] = tank_comp.get(bid, 0) + cnt
            else:
                tank_comp[""] = tank_comp.get("", 0) + variance
            state[cid] = _clean_composition(tank_comp)

    return {"state": state, "mortality": batch_mortality}


# ─────────────────────────────────────────────────────────────────────────────
# Per-tank state computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_tank_state(
    tank:               dict,
    batches:            list[dict],
    daily_logs:         list[dict] | None = None,
    movements:          list[dict] | None = None,
    as_of_date:         str | None = None,
    grading_logs:       list[dict] | None = None,
    batch_compositions: dict | None = None,   # pre-computed by compute_farm_state
    adjustments:        list[dict] | None = None,
) -> dict[str, Any]:
    """
    Compute current state for a single tank.

    batch_compositions: if provided (passed from compute_farm_state), the returned
    dict will include a rich "batch_composition" list that reflects the current
    distribution of batches across tanks computed from the full event history.

    If batch_compositions is None (e.g. when called stand-alone from movements.py),
    a single-batch fallback is used — existing behaviour is preserved.
    """

    daily_logs            = _filter_by_date(daily_logs  or [], as_of_date)
    movements             = _filter_by_date(movements   or [], as_of_date)
    grading_logs_filtered = _filter_by_date(grading_logs or [], as_of_date)

    tank_id = tank.get("id") or tank.get("tank_id") or tank.get("name")

    base_fish_count = safe_int(tank.get("fish_count"), 0)
    avg_weight_g    = safe_float(tank.get("avg_weight_g"), 0.0)
    feed_kg_day     = safe_float(tank.get("feed_kg_day"), 0.0)
    tank_volume_m3  = safe_float(
        tank.get("tank_volume_m3") or tank.get("volume_m3"), 0.0
    )

    # ── Grading logs ──────────────────────────────────────────────────────
    tank_name_norm = _norm(tank.get("name") or tank.get("tank_name"))

    tank_grading_logs = [
        g for g in grading_logs_filtered
        if _same_tank_id(g.get("tank_id"), tank_id)
        or (_norm(g.get("tank_name")) != "" and _norm(g.get("tank_name")) == tank_name_norm)
    ]
    tank_grading_logs = sorted(tank_grading_logs, key=lambda x: x.get("date", ""))

    latest_grading = tank_grading_logs[-1] if tank_grading_logs else None

    grading_avg_weight_g: float | None = None
    grading_date:         str | None   = None
    grading_sample_count: int          = 0
    small_pct:            float        = 0.0
    medium_pct:           float        = 0.0
    harvest_ready_pct:    float        = 0.0
    small_threshold_g:    float        = 0.0
    harvest_threshold_g:  float        = 0.0

    if latest_grading:
        grading_avg_weight_g  = safe_float(latest_grading.get("avg_weight_g"), None)
        grading_date          = latest_grading.get("date")
        grading_sample_count  = safe_int(latest_grading.get("sample_count"), 0)
        small_pct             = safe_float(latest_grading.get("small_pct"), 0.0)
        medium_pct            = safe_float(latest_grading.get("medium_pct"), 0.0)
        harvest_ready_pct     = safe_float(latest_grading.get("harvest_ready_pct"), 0.0)
        small_threshold_g     = safe_float(latest_grading.get("small_threshold_g"), 0.0)
        harvest_threshold_g   = safe_float(latest_grading.get("harvest_threshold_g"), 0.0)

    # ── Batch (legacy single-batch lookup) ────────────────────────────────
    batch = find_batch_for_tank(tank, batches)

    batch_name    = ""
    batch_species = ""
    if batch:
        batch_name    = batch.get("name") or batch.get("batch_name", "")
        batch_species = batch.get("species", "")

    species        = tank.get("species") or batch_species or ""
    grading_status = tank.get("grading_status", "Not graded")
    grading_notes  = tank.get("grading_notes", "")

    max_mortality_pct = safe_float(tank.get("max_mortality_percent_day"), 0.0)

    # ── Daily logs ────────────────────────────────────────────────────────
    tank_logs = [
        log for log in daily_logs
        if _same_tank_id(log.get("tank_id"), tank_id)
        or _same_tank_fallback(log.get("tank_name"), tank, tank_id)
    ]
    tank_logs = sorted(tank_logs, key=lambda x: x.get("date", ""))

    total_mortality = sum(
        safe_int(log.get("mortality_fish", log.get("mortality")), 0)
        for log in tank_logs
    )
    total_feed_logged = sum(
        safe_float(log.get("feed_kg"), 0.0)
        for log in tank_logs
    )

    # ── Movements ─────────────────────────────────────────────────────────
    transfer_moves_in = [
        m for m in movements
        if _movement_type(m) == "transfer" and _matches_tank(m, tank, tank_id, "to")
    ]
    transfer_moves_out = [
        m for m in movements
        if _movement_type(m) == "transfer" and _matches_tank(m, tank, tank_id, "from")
    ]
    harvest_moves_out = [
        m for m in movements
        if _movement_type(m) == "harvest" and _matches_tank(m, tank, tank_id, "from")
    ]

    moves_out = transfer_moves_out + harvest_moves_out

    moved_in_fish = sum(_movement_quantity_fish(m) for m in transfer_moves_in)
    moved_out_fish = sum(_movement_quantity_fish(m) for m in moves_out)
    harvested_fish = sum(_movement_quantity_fish(m) for m in harvest_moves_out)
    transferred_out_fish = sum(_movement_quantity_fish(m) for m in transfer_moves_out)

    # ── Weighted avg_weight from chronological events ─────────────────────
    # Movements and grading logs are merged into one date-ordered stream.
    # Grading logs act as in-situ calibration checkpoints: when encountered
    # they update the per-fish weight estimate without changing fish count.
    # Critically, they only take effect while the tank holds fish (wt_fish > 0),
    # so a grading from a previous population does NOT contaminate the weight
    # of fish that arrived after the tank was fully emptied and restocked.
    base_avg     = safe_float(tank.get("avg_weight_g"), 0.0)
    wt_biomass_g = float(base_fish_count) * base_avg
    wt_fish      = float(base_fish_count)

    all_events: list[tuple[str, str, str, dict]] = []
    for m in transfer_moves_in:
        all_events.append((m.get("date", ""), m.get("timestamp", m.get("time", "")), "in",    m))
    for m in moves_out:
        all_events.append((m.get("date", ""), m.get("timestamp", m.get("time", "")), "out",   m))
    for g in tank_grading_logs:
        if safe_float(g.get("avg_weight_g"), 0.0) > 0:
            all_events.append((g.get("date", ""), "", "grade", g))
    all_events.sort(key=lambda x: (x[0], x[1]))

    for ev_date, ev_ts, direction, record in all_events:
        if direction == "grade":
            g_avg = safe_float(record.get("avg_weight_g"), 0.0)
            if g_avg > 0 and wt_fish > 0:
                # Calibrate the per-fish weight; fish count does not change.
                wt_biomass_g = wt_fish * g_avg
        elif direction == "in":
            qty = _movement_quantity_fish(record)
            if qty <= 0:
                continue
            m_avg = safe_float(record.get("avg_weight_g"), 0.0)
            if m_avg > 0:
                wt_biomass_g += float(qty) * m_avg
            else:
                # No avg stored: assume same weight as current blend.
                blend = wt_biomass_g / wt_fish if wt_fish > 0 else 0.0
                wt_biomass_g += float(qty) * blend
            wt_fish += float(qty)
        else:  # out (transfer out or harvest)
            qty = _movement_quantity_fish(record)
            if qty <= 0:
                continue
            if wt_fish > 0:
                remove       = min(float(qty), wt_fish)
                blend        = wt_biomass_g / wt_fish
                wt_biomass_g = max(0.0, wt_biomass_g - remove * blend)
                wt_fish      = max(0.0, wt_fish - remove)

    # Mortality is proportional and doesn't change per-fish weight;
    # applying it last (after all movements) is mathematically equivalent.
    if wt_fish > 0 and total_mortality > 0:
        remove       = min(float(total_mortality), wt_fish)
        blend        = wt_biomass_g / wt_fish
        wt_biomass_g = max(0.0, wt_biomass_g - remove * blend)
        wt_fish      = max(0.0, wt_fish - remove)

    avg_weight_g = round(wt_biomass_g / wt_fish, 1) if wt_fish > 0 else 0.0

    # ── Stock adjustments (count reconciliation) ─────────────────────────
    filtered_adj = _filter_by_date(adjustments or [], as_of_date)
    tank_adj = [
        a for a in filtered_adj
        if _same_tank_id(a.get("tank_id"), tank_id)
        or _same_tank_fallback(a.get("tank_name"), tank, tank_id)
    ]
    total_adj_variance = sum(safe_int(a.get("variance"), 0) for a in tank_adj)

    # ── Core fish count ───────────────────────────────────────────────────
    raw_fish_count = (
        base_fish_count + moved_in_fish - moved_out_fish
        - total_mortality + total_adj_variance
    )
    current_fish_count = max(0, raw_fish_count)

    # Empty-tank reset: when there are no fish, all per-fish metrics must be 0.
    # This prevents stale Farm-Setup avg_weight_g or stock-adjustment divergence
    # from leaving a non-zero avg_weight_g on a tank with fish_count == 0.
    if current_fish_count == 0:
        avg_weight_g = 0.0

    biomass = calculate_biomass_kg(current_fish_count, avg_weight_g)
    density = calculate_density_kg_m3(biomass, tank_volume_m3)

    # ── Cumulative mortality % ────────────────────────────────────────────
    cumulative_mortality_pct = 0.0
    if base_fish_count > 0:
        cumulative_mortality_pct = total_mortality / base_fish_count * 100

    last_log = tank_logs[-1] if tank_logs else None

    daily_mortality_fish = 0
    daily_mortality_pct  = 0.0
    if last_log:
        daily_mortality_fish = safe_int(last_log.get("mortality_fish"), 0)
        # Only calculate a meaningful daily mortality % when the tank still
        # holds fish.  If the tank was emptied by a movement the last log's
        # mortality figure belongs to a prior state and must not produce a
        # false "above limit" alert.
        if current_fish_count > 0:
            fish_before_today = current_fish_count + daily_mortality_fish
            if fish_before_today > 0:
                daily_mortality_pct = daily_mortality_fish / fish_before_today * 100
        # current_fish_count == 0 → daily_mortality_pct stays 0.0

    # ── Warnings ──────────────────────────────────────────────────────────
    warnings = []
    if raw_fish_count < 0:
        warnings.append(
            f"Stock calculation below zero: {raw_fish_count} fish. "
            "Check mortality and movement records."
        )
    # Guard: only fire daily-mortality alert for tanks that currently hold fish.
    # An emptied tank's last log would otherwise produce a spurious alert.
    if current_fish_count > 0 and max_mortality_pct > 0 and daily_mortality_pct > max_mortality_pct:
        warnings.append(
            f"Daily mortality above limit: {daily_mortality_pct:.2f}% "
            f"(limit {max_mortality_pct:.2f}%)"
        )
    # ── Batch composition (NEW) ───────────────────────────────────────────
    # If pre-computed compositions are available, use them for a rich list.
    # Otherwise fall back to the legacy single-batch from tank.batch_id.
    if batch_compositions is not None and tank_id in batch_compositions:
        comp       = batch_compositions[tank_id]
        total_comp = sum(comp.values())
        batch_composition = sorted(
            [
                {
                    "batch_id":   bid,
                    "batch_name": _get_batch_name(bid, batches),
                    "fish_count": count,
                    "percentage": round(count / total_comp * 100, 1) if total_comp > 0 else 0.0,
                }
                for bid, count in comp.items()
                if count > 0
            ],
            key=lambda x: x["fish_count"],
            reverse=True,
        )
    else:
        # Legacy fallback: single batch from tank.batch_id
        single_bid   = tank.get("batch_id") or ""
        single_bname = batch_name
        if single_bid and current_fish_count > 0:
            batch_composition = [{
                "batch_id":   single_bid,
                "batch_name": single_bname,
                "fish_count": current_fish_count,
                "percentage": 100.0,
            }]
        else:
            batch_composition = []

    # ── Reconcile composition total with current_fish_count ───────────────
    #
    # The composition replay engine (_compute_all_batch_compositions) uses
    # integer arithmetic and proportional_split rounding, which can diverge
    # by ±few fish from compute_tank_state's authoritative clamped count.
    # We synchronise here so the display is always consistent.
    #
    # Rules:
    #   1. Empty tank (current_fish_count == 0) → composition must be [].
    #      No residuals, no "Batch X 100 %" artefacts.
    #   2. Non-empty tank with a small count difference → absorb into the
    #      largest batch (already sorted desc).  Threshold: up to 10 fish or
    #      2 % of current count, whichever is larger.
    #   3. After any adjustment, recalculate percentages from actual counts.
    #   4. If composition is empty but tank has fish → show "Unassigned 100%".

    if current_fish_count == 0:
        # Rule 1: empty tank — wipe everything, no residuals allowed.
        batch_composition = []

    else:
        # Rule 2: reconcile totals.
        if batch_composition:
            comp_total = sum(b["fish_count"] for b in batch_composition)
            diff = current_fish_count - comp_total

            if diff != 0:
                threshold = max(10, current_fish_count // 50)   # ≤ 2 % or 10 fish
                if abs(diff) <= threshold:
                    # Absorb rounding diff into the largest-count batch (index 0)
                    batch_composition[0]["fish_count"] = max(
                        0, batch_composition[0]["fish_count"] + diff
                    )
                    # Drop any entry that became zero after the adjustment
                    batch_composition = [
                        b for b in batch_composition if b["fish_count"] > 0
                    ]
                # Large divergence: leave composition as-is (will be caught by
                # the "Unassigned 100 %" fallback below if it becomes empty).

            # Rule 3: always recalculate percentages from actual fish counts.
            comp_total = sum(b["fish_count"] for b in batch_composition)
            if comp_total > 0:
                for b in batch_composition:
                    b["percentage"] = round(b["fish_count"] / comp_total * 100, 1)
            else:
                batch_composition = []

        # Rule 4: non-empty tank but no composition → show Unassigned.
        if not batch_composition:
            batch_composition = [{
                "batch_id":   "",
                "batch_name": "Unassigned",
                "fish_count": current_fish_count,
                "percentage": 100.0,
            }]

    # ── Return full state dict ────────────────────────────────────────────
    return {
        "tank_id":     tank_id,
        "tank_name":   tank.get("name", ""),
        "system_name": tank.get("system_name", ""),
        "system_type": tank.get("system_type", ""),
        "species":     species,

        "batch_id":   tank.get("batch_id"),
        "batch_name": batch_name,

        # NEW: list of {batch_id, batch_name, fish_count, percentage}
        "batch_composition": batch_composition,

        "grading_status": grading_status,
        "grading_notes":  grading_notes,

        "base_fish_count":        base_fish_count,
        "moved_in_fish":          moved_in_fish,
        "moved_out_fish":         moved_out_fish,
        "transferred_out_fish":   transferred_out_fish,
        "harvested_fish":         harvested_fish,
        "total_mortality":        total_mortality,
        "total_adj_variance":     total_adj_variance,
        "adjustment_count":       len(tank_adj),

        "daily_mortality_fish":    daily_mortality_fish,
        "daily_mortality_pct":     round(daily_mortality_pct, 2),
        "cumulative_mortality_pct": round(cumulative_mortality_pct, 2),

        "raw_fish_count": raw_fish_count,
        "fish_count":     current_fish_count,
        "is_empty":       current_fish_count == 0,

        "avg_weight_g": round(avg_weight_g, 1),
        "feed_kg_day":  round(feed_kg_day, 2),

        "tank_volume_m3": round(tank_volume_m3, 2),
        "biomass_kg":     round(biomass, 2),
        "density_kg_m3":  round(density, 2),

        "max_mortality_percent_day": max_mortality_pct,
        "total_feed_kg":            round(total_feed_logged, 2),

        "grading_date":          grading_date,
        "grading_sample_count":  grading_sample_count,
        "grading_avg_weight_g":  grading_avg_weight_g,
        "small_pct":             round(small_pct, 1),
        "medium_pct":            round(medium_pct, 1),
        "harvest_ready_pct":     round(harvest_ready_pct, 1),
        "small_threshold_g":     small_threshold_g,
        "harvest_threshold_g":   harvest_threshold_g,

        "warnings":        warnings,
        "last_log":        last_log,
        "log_count":       len(tank_logs),
        "movement_count":  len(transfer_moves_in) + len(moves_out),

        "tank": tank,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Farm-wide state computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_farm_state(
    farm_data:    dict,
    batches:      list[dict],
    daily_logs:   list[dict] | None = None,
    movements:    list[dict] | None = None,
    as_of_date:   str | None = None,
    grading_logs: list[dict] | None = None,
    adjustments:  list[dict] | None = None,
) -> list[dict[str, Any]]:
    """
    Compute current state for every tank in the farm.

    Batch composition is pre-computed globally (one pass over all events
    in date order) so proportional splits happen across the full multi-tank
    history before individual tank states are assembled.
    """
    # Pre-compute batch compositions for all tanks in a single chronological pass
    comp_result        = _compute_all_batch_compositions(
        farm_data, batches, daily_logs or [], movements or [], as_of_date,
        adjustments=adjustments,
    )
    batch_compositions = comp_result["state"]   # {tank_id: {batch_id: fish_count}}

    tanks = get_all_tanks(farm_data)
    return [
        compute_tank_state(
            tank=tank,
            batches=batches,
            daily_logs=daily_logs   or [],
            movements=movements     or [],
            as_of_date=as_of_date,
            grading_logs=grading_logs or [],
            batch_compositions=batch_compositions,
            adjustments=adjustments,
        )
        for tank in tanks
    ]


def compute_batch_summary(
    farm_data:    dict,
    batches:      list[dict],
    daily_logs:   list[dict],
    movements:    list[dict],
    grading_logs: list[dict] | None = None,
    as_of_date:   str | None = None,
    adjustments:  list[dict] | None = None,
) -> list[dict]:
    """
    Return per-batch roll-up: current fish, mortality, tank distribution.

    Uses _compute_all_batch_compositions so multi-batch tanks are handled
    correctly — mortality and movements are split proportionally.

    Returns a list of dicts (one per active batch):
    {
        batch_id, batch_name, species,
        hatch_date, vaccination_due_day, vaccination_done,
        initial_fish, current_fish, current_biomass_kg,
        total_mortality, mortality_pct,
        tank_distribution: [
            {tank_id, tank_name, fish_count, percentage_of_batch}
        ]
    }
    """
    comp_result     = _compute_all_batch_compositions(
        farm_data, batches, daily_logs or [], movements or [], as_of_date,
        adjustments=adjustments,
    )
    state           = comp_result["state"]      # {tank_id: {batch_id: fish_count}}
    batch_mortality = comp_result["mortality"]  # {batch_id: total_died_from_mortality}

    # Build per-tank info (avg_weight_g for biomass; tank name for display)
    grading_filtered = _filter_by_date(grading_logs or [], as_of_date)
    tank_info: dict[str, dict] = {}
    for tank in get_all_tanks(farm_data):
        tid = tank.get("id") or tank.get("tank_id") or tank.get("name")
        if not tid:
            continue
        avg_w = safe_float(tank.get("avg_weight_g"), 50.0)

        # Override avg weight from latest grading log for this tank
        tank_name_n = _norm(tank.get("name", ""))
        tank_gradings = [
            g for g in grading_filtered
            if _same_tank_id(g.get("tank_id"), tid)
            or (_norm(g.get("tank_name")) != "" and _norm(g.get("tank_name")) == tank_name_n)
        ]
        if tank_gradings:
            latest_g = sorted(tank_gradings, key=lambda x: x.get("date", ""))[-1]
            override = safe_float(latest_g.get("avg_weight_g"), None)
            if override and override > 0:
                avg_w = override

        tank_info[tid] = {
            "tank_name":  tank.get("name", tid),
            "avg_weight": avg_w,
        }

    result = []
    for batch in batches:
        if batch.get("status") != "active":
            continue
        bid = batch.get("id", "")
        if not bid:
            continue

        # Find all tanks that currently hold fish from this batch
        tank_distribution: list[dict] = []
        total_fish    = 0
        total_biomass = 0.0

        for tank_id, comp in state.items():
            fish = comp.get(bid, 0)
            if fish <= 0:
                continue
            info    = tank_info.get(tank_id, {})
            avg_w   = float(info.get("avg_weight", 50.0) or 50.0)
            biomass = calculate_biomass_kg(fish, avg_w)
            total_fish    += fish
            total_biomass += biomass
            tank_distribution.append({
                "tank_id":             tank_id,
                "tank_name":           info.get("tank_name", tank_id),
                "fish_count":          fish,
                "percentage_of_batch": 0.0,   # filled below
            })

        # Fill percentage_of_batch
        for td in tank_distribution:
            td["percentage_of_batch"] = (
                round(td["fish_count"] / total_fish * 100, 1)
                if total_fish > 0 else 0.0
            )
        tank_distribution.sort(key=lambda x: x["fish_count"], reverse=True)

        initial_fish = int(batch.get("initial_fish_count", 0) or 0)
        total_mort   = batch_mortality.get(bid, 0)
        mort_pct     = round(total_mort / initial_fish * 100, 1) if initial_fish > 0 else 0.0

        result.append({
            "batch_id":            bid,
            "batch_name":          batch.get("name", ""),
            "species":             batch.get("species", ""),
            "hatch_date":          batch.get("hatch_date", "") or "",
            "vaccination_due_day": int(batch.get("vaccination_due_day") or 60),
            "vaccination_done":    bool(batch.get("vaccination_done", False)),
            "initial_fish":        initial_fish,
            "current_fish":        total_fish,
            "current_biomass_kg":  round(total_biomass, 2),
            "total_mortality":     total_mort,
            "mortality_pct":       mort_pct,
            "tank_distribution":   tank_distribution,
        })

    return result


def farm_summary(tank_states: list[dict]) -> dict[str, Any]:
    total_fish     = sum(s.get("fish_count", 0)       for s in tank_states)
    total_biomass  = sum(s.get("biomass_kg", 0.0)     for s in tank_states)
    total_feed     = sum(s.get("total_feed_kg", 0.0)  for s in tank_states)
    total_mortality = sum(s.get("total_mortality", 0) for s in tank_states)
    tanks_with_warnings = [s for s in tank_states if s.get("warnings")]

    return {
        "total_fish":         total_fish,
        "total_biomass_kg":   round(total_biomass, 2),
        "total_feed_kg":      round(total_feed, 2),
        "total_mortality":    total_mortality,
        "tanks_with_warnings": len(tanks_with_warnings),
        "active_tanks":        len(tank_states),
    }
