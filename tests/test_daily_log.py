"""
tests/test_daily_log.py
Pytest unit tests for daily_log treatment helpers and tank occupancy logic.
No Supabase, no production data — pure in-memory logic.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from modules.daily_log import _normalize_treatments, TREATMENT_UNITS
from core.calculations import get_tank_fish_count, is_tank_occupied
from core.stock_engine import normalize_empty_tank, compute_tank_state, compute_farm_state


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tx(t_type="Salt", amount=5.0, unit="kg"):
    return {"type": t_type, "amount": amount, "unit": unit}


def _old_record(t_type="Wofa", amount=2.0):
    unit = TREATMENT_UNITS.get(t_type, "")
    return {
        "treatment_type":   t_type,
        "treatment_amount": amount,
        "treatment_unit":   unit,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestNormalizeTreatments:

    def test_new_format_returns_list(self):
        record = {"treatments_list": [_tx("Salt", 5.0, "kg"), _tx("Formalin", 2.0, "l")]}
        result = _normalize_treatments(record)
        assert len(result) == 2
        assert result[0]["type"] == "Salt"
        assert result[1]["type"] == "Formalin"

    def test_new_format_filters_none_entries(self):
        record = {"treatments_list": [
            {"type": "None", "amount": 0, "unit": ""},
            _tx("Salt", 3.0, "kg"),
        ]}
        result = _normalize_treatments(record)
        assert len(result) == 1
        assert result[0]["type"] == "Salt"

    def test_new_format_empty_list_returns_empty(self):
        record = {"treatments_list": []}
        assert _normalize_treatments(record) == []

    def test_old_format_single_treatment_converted(self):
        record = _old_record("Wofa", 2.0)
        result = _normalize_treatments(record)
        assert len(result) == 1
        assert result[0]["type"] == "Wofa"
        assert result[0]["amount"] == 2.0
        assert result[0]["unit"] == "l"

    def test_old_format_none_treatment_returns_empty(self):
        record = {"treatment_type": "None", "treatment_amount": 0, "treatment_unit": ""}
        assert _normalize_treatments(record) == []

    def test_missing_fields_returns_empty(self):
        assert _normalize_treatments({}) == []

    def test_new_format_takes_priority_over_old_scalar(self):
        record = {
            "treatments_list": [_tx("Salt", 5.0, "kg")],
            "treatment_type":   "Formalin",
            "treatment_amount": 2.0,
            "treatment_unit":   "l",
        }
        result = _normalize_treatments(record)
        assert len(result) == 1
        assert result[0]["type"] == "Salt"

    def test_old_format_halamid_unit_preserved(self):
        record = _old_record("Halamid", 100.0)
        result = _normalize_treatments(record)
        assert result[0]["unit"] == "g"


class TestTreatmentSerialization:

    def test_single_treatment_serialized(self):
        treatments = [_tx("Salt", 5.0, "kg")]
        result = " | ".join(
            f"{t['type']} {t['amount']} {t['unit']}" for t in treatments
        )
        assert result == "Salt 5.0 kg"

    def test_multiple_treatments_pipe_separated(self):
        treatments = [_tx("Salt", 5.0, "kg"), _tx("Formalin", 2.0, "l")]
        result = " | ".join(
            f"{t['type']} {t['amount']} {t['unit']}" for t in treatments
        )
        assert result == "Salt 5.0 kg | Formalin 2.0 l"

    def test_empty_list_produces_empty_string(self):
        result = " | ".join(
            f"{t['type']} {t['amount']} {t['unit']}" for t in []
        ) if [] else ""
        assert result == ""


# ── Tank occupancy helpers ─────────────────────────────────────────────────────

def _tank_state(fish_count, biomass_kg=0.0, batch_id=None):
    """Build a minimal tank-state dict for occupancy testing."""
    return {
        "fish_count": fish_count,
        "biomass_kg": biomass_kg,
        "batch_id":   batch_id,
        "avg_weight_g": 0.0,
    }


class TestGetTankFishCount:

    def test_int_fish_count(self):
        assert get_tank_fish_count(_tank_state(100)) == 100

    def test_float_fish_count(self):
        assert get_tank_fish_count(_tank_state(100.0)) == 100

    def test_string_fish_count(self):
        assert get_tank_fish_count({"fish_count": "100"}) == 100

    def test_zero_string(self):
        assert get_tank_fish_count({"fish_count": "0"}) == 0

    def test_none_returns_zero(self):
        assert get_tank_fish_count({"fish_count": None}) == 0

    def test_empty_string_returns_zero(self):
        assert get_tank_fish_count({"fish_count": ""}) == 0

    def test_missing_key_returns_zero(self):
        assert get_tank_fish_count({}) == 0

    def test_never_negative(self):
        assert get_tank_fish_count({"fish_count": -50}) == 0


class TestIsTankOccupied:

    # Test 1: fish_count=100, biomass=0, batch_id=None → occupied
    def test_fish_count_100_no_biomass_no_batch(self):
        assert is_tank_occupied(_tank_state(100, biomass_kg=0.0, batch_id=None))

    # Test 2: fish_count="100", biomass="0" → occupied
    def test_string_fish_count_occupied(self):
        assert is_tank_occupied({"fish_count": "100", "biomass_kg": "0"})

    # Test 3: fish_count=0, biomass>0, batch_id present → empty
    def test_zero_fish_with_biomass_and_batch(self):
        assert not is_tank_occupied(_tank_state(0, biomass_kg=50.0, batch_id="b1"))

    # Test 4: fish_count="0" → empty
    def test_string_zero_is_empty(self):
        assert not is_tank_occupied({"fish_count": "0"})

    # Test 5: fish_count=None → empty
    def test_none_fish_count_is_empty(self):
        assert not is_tank_occupied({"fish_count": None})

    # Test 5b: fish_count="" → empty
    def test_empty_string_is_empty(self):
        assert not is_tank_occupied({"fish_count": ""})


class TestTankOccupancyViaComputeTankState:
    """
    compute_tank_state reads fish_count DIRECTLY from farm_state (the
    materialized current value).  Movement records are passed for historical
    totals (total_moved_in, total_moved_out) but must NOT alter fish_count.
    """

    def _mk_tank(self, fish_count, avg_weight_g=100.0):
        return {
            "id": "t1", "name": "Tank1",
            "fish_count": fish_count,
            "avg_weight_g": avg_weight_g,
            "biomass_kg": fish_count * avg_weight_g / 1000.0 if fish_count else 0.0,
            "tank_volume_m3": 10.0,
        }

    def _transfer_out(self, qty):
        return {
            "id": "mv1", "date": "2026-01-02",
            "movement_type": "transfer",
            "from_tank_id": "t1",
            "to_tank_id": "t2",
            "quantity_fish": qty,
            "avg_weight_g": 0.0,
        }

    def _ext_in(self, qty):
        return {
            "id": "mv_ext", "date": "2026-01-02",
            "movement_type": "external_stock_in",
            "from_tank_id": "",
            "to_tank_id": "t1",
            "quantity_fish": qty,
            "avg_weight_g": 0.0,
            "batch_id": "b1",
        }

    # Test 6: occupied tank allows mortality (fish_count > 0 in farm_state)
    def test_occupied_tank_has_positive_fish_count(self):
        tank = self._mk_tank(1000, avg_weight_g=100.0)
        s    = compute_tank_state(tank, [], movements=[], daily_logs=[])
        assert is_tank_occupied(s)

    # Test 7: empty tank has fish_count=0 and is not occupied
    def test_empty_tank_not_occupied(self):
        tank = self._mk_tank(0, avg_weight_g=0.0)
        s    = compute_tank_state(tank, [], movements=[], daily_logs=[])
        assert not is_tank_occupied(s)

    # Test 8: After a transfer-out the movement updates farm_state; compute_tank_state
    # reads the updated farm_state (fish_count=0) directly.  The movement record is
    # audit history and its quantity is returned as total moved_out — not subtracted again.
    def test_transfer_all_fish_out_yields_empty(self):
        tank = self._mk_tank(0)        # farm_state already updated by the movement
        mv   = self._transfer_out(1000)
        s    = compute_tank_state(tank, [], movements=[mv], daily_logs=[])
        assert not is_tank_occupied(s)
        assert s["moved_out_fish"] == 1000  # informational total

    # Test 9: After External Stock In the movement updates farm_state; compute_tank_state
    # reads the updated farm_state (fish_count=500) directly.
    def test_ext_in_makes_empty_tank_occupied(self):
        tank = self._mk_tank(500)      # farm_state already updated by the movement
        mv   = self._ext_in(500)
        s    = compute_tank_state(tank, [], movements=[mv], daily_logs=[])
        assert is_tank_occupied(s)
        assert get_tank_fish_count(s) == 500
        assert s["moved_in_fish"] == 500   # informational total


class TestDailyLogEntryTabOccupancy:
    """
    Regression tests for the Daily Log entry tab occupancy logic.

    Architecture (materialized-state model):
    - farm_state.fish_count IS the live current count, updated atomically by
      every operation (movements via _update_farm_state_for_movement, mortality
      via _apply_mortality_to_farm).
    - compute_farm_state reads fish_count directly from farm_state.
    - Movement and mortality records in history are informational totals only —
      they are NOT replayed on top of farm_state to derive fish_count.

    Each test sets up farm_state with the fish_count value that reflects the
    materialized outcome of the scenario (i.e. as if movements already updated
    farm_state), then verifies compute_farm_state + is_tank_occupied agree.
    """

    def _farm(self, systems):
        return {"systems": systems, "tanks": []}

    def _system(self, name, tanks):
        return {"id": f"sys_{name}", "name": name, "type": "Growout", "tanks": tanks}

    def _tank(self, tid, name, fish_count, system_name="S1"):
        return {
            "id": tid, "name": name,
            "system_id": "sys_S1", "system_name": system_name,
            "fish_count": float(fish_count),
            "avg_weight_g": 100.0,
            "biomass_kg": fish_count * 100.0 / 1000.0,
            "tank_volume_m3": 30.0,
        }

    def _transfer(self, from_id, to_id, qty, date="2026-01-10"):
        return {
            "id": f"mv_{from_id}_{to_id}",
            "date": date,
            "movement_type": "transfer",
            "from_tank_id": from_id,
            "to_tank_id": to_id,
            "quantity_fish": qty,
            "avg_weight_g": 100.0,
        }

    def _harvest(self, from_id, qty, date="2026-01-10"):
        return {
            "id": f"mv_harv_{from_id}",
            "date": date,
            "movement_type": "harvest",
            "from_tank_id": from_id,
            "to_tank_id": None,
            "quantity_fish": qty,
            "avg_weight_g": 100.0,
        }

    def _log_entry(self, tank_id, tank_name, mortality, date="2026-01-05", system="S1"):
        return {
            "date": date,
            "system_name": system,
            "tank_id": tank_id,
            "tank_name": tank_name,
            "mortality_fish": mortality,
            "feed_kg": 10.0,
            "oxygen": 8.0,
        }

    def test_tank_with_positive_setup_fish_and_no_movements_is_occupied(self):
        farm = self._farm([self._system("S1", [self._tank("t1", "T1", 1000)])])
        states = compute_farm_state(farm, [], [], [])
        state = next(s for s in states if s["tank_id"] == "t1")
        assert is_tank_occupied(state)
        assert get_tank_fish_count(state) == 1000

    def test_tank_emptied_by_transfer_out_shows_as_empty(self):
        # After the transfer farm_state was updated: T1→0, T2→1000.
        # Movement record is still passed for historical totals.
        farm = self._farm([self._system("S1", [
            self._tank("t1", "T1", 0),     # farm_state updated by movement
            self._tank("t2", "T2", 1000),  # farm_state updated by movement
        ])])
        mv = self._transfer("t1", "t2", 1000)
        states = compute_farm_state(farm, [], [], [mv])
        t1_state = next(s for s in states if s["tank_id"] == "t1")
        t2_state = next(s for s in states if s["tank_id"] == "t2")
        assert not is_tank_occupied(t1_state), "T1 should be empty — farm_state reflects transfer"
        assert is_tank_occupied(t2_state),     "T2 should be occupied — farm_state reflects transfer"
        assert t1_state["moved_out_fish"] == 1000  # informational history total
        assert t2_state["moved_in_fish"]  == 1000  # informational history total

    def test_tank_emptied_by_harvest_shows_as_empty(self):
        # After the harvest farm_state was updated: T1→0.
        farm = self._farm([self._system("S1", [self._tank("t1", "T1", 0)])])
        mv = self._harvest("t1", 5000)
        states = compute_farm_state(farm, [], [], [mv])
        t1_state = next(s for s in states if s["tank_id"] == "t1")
        assert not is_tank_occupied(t1_state)
        assert t1_state["moved_out_fish"] == 5000  # informational total

    def test_tank_with_positive_setup_fish_emptied_then_restocked(self):
        # T1: 1000 transferred out, 500 transferred in → net farm_state = 500.
        # T2: received 1000 → farm_state = 1000. T3: sent 500 → farm_state = 0.
        farm = self._farm([self._system("S1", [
            self._tank("t1", "T1", 500),   # farm_state after both movements
            self._tank("t2", "T2", 1000),
            self._tank("t3", "T3", 0),
        ])])
        mvs = [
            self._transfer("t1", "t2", 1000, date="2026-01-10"),
            self._transfer("t3", "t1", 500,  date="2026-01-11"),
        ]
        states = compute_farm_state(farm, [], [], mvs)
        t1 = next(s for s in states if s["tank_id"] == "t1")
        assert is_tank_occupied(t1)
        assert get_tank_fish_count(t1) == 500

    def test_cumulative_mortality_exceeding_base_renders_tank_empty(self):
        # Mortality (80 fish) updated farm_state: 1000→920.
        # Transfer of 920 out updated farm_state: 920→0.
        # Final farm_state: T1=0, T2=920.
        farm = self._farm([self._system("S1", [
            self._tank("t1", "T1", 0),    # farm_state after mortality + transfer
            self._tank("t2", "T2", 920),  # farm_state after receiving transfer
        ])])
        mv = self._transfer("t1", "t2", 920, date="2026-01-10")
        dl_logs = [
            self._log_entry("t1", "T1", 40, date="2026-01-06"),
            self._log_entry("t1", "T1", 40, date="2026-01-07"),
        ]
        states = compute_farm_state(farm, [], dl_logs, [mv])
        t1 = next(s for s in states if s["tank_id"] == "t1")
        assert not is_tank_occupied(t1)
        assert get_tank_fish_count(t1) == 0
        assert t1["total_mortality"] == 80  # informational total

    def test_multi_system_tank_occupancy_independent(self):
        # Two systems each with a tank named "01". Transfer empties S1/01 only.
        # farm_state updated for S1/01 (→0); S2/01 unchanged (500).
        farm = self._farm([
            self._system("S1", [self._tank("t_s1", "01", 0,   "S1")]),
            self._system("S2", [self._tank("t_s2", "01", 500, "S2")]),
        ])
        mv = self._transfer("t_s1", "t_unused", 1000)
        states = compute_farm_state(farm, [], [], [mv])
        s1_tank = next(s for s in states if s["tank_id"] == "t_s1")
        s2_tank = next(s for s in states if s["tank_id"] == "t_s2")
        assert not is_tank_occupied(s1_tank), "S1/01 should be empty"
        assert is_tank_occupied(s2_tank),     "S2/01 should still be occupied"

    def test_entry_tab_occupancy_lookup_key_uses_tank_id_not_name(self):
        # existing_by_tank is keyed by tank_id.
        # Verify that looking up by the stable ID returns the right record.
        existing_entries = [
            {"tank_id": "tank_abc", "tank_name": "01", "mortality_fish": 7, "oxygen": 8.1},
            {"tank_id": "tank_def", "tank_name": "02", "mortality_fish": 3, "oxygen": 7.5},
        ]
        by_tank = {e["tank_id"]: e for e in existing_entries}
        assert by_tank.get("tank_abc", {}).get("mortality_fish") == 7
        assert by_tank.get("tank_def", {}).get("mortality_fish") == 3
        assert by_tank.get("unknown", {}) == {}


class TestNormalizeEmptyTank:

    def test_zero_fish_clears_all_fields(self):
        tank = {"fish_count": 0, "biomass_kg": 50.0, "avg_weight_g": 100.0,
                "batch_id": "b1", "batch_name": "Batch A", "batch_composition": [{}]}
        result = normalize_empty_tank(tank)
        assert result["fish_count"] == 0
        assert result["biomass_kg"] == 0.0
        assert result["avg_weight_g"] == 0.0
        assert result["batch_id"] is None
        assert result["batch_composition"] == []

    def test_positive_fish_zero_biomass_not_cleared(self):
        # fish_count > 0 but biomass = 0 (External Stock In with no avg weight)
        # → tank must NOT be cleared; fish_count is the source of truth
        tank = {"fish_count": 500, "biomass_kg": 0.0, "avg_weight_g": 0.0}
        result = normalize_empty_tank(tank)
        assert result["fish_count"] == 500

    def test_string_zero_fish_cleared(self):
        tank = {"fish_count": "0", "biomass_kg": 100.0, "avg_weight_g": 200.0}
        result = normalize_empty_tank(tank)
        assert result["fish_count"] == 0
        assert result["biomass_kg"] == 0.0

    def test_occupied_tank_unchanged(self):
        tank = {"fish_count": 1000, "biomass_kg": 150.0, "avg_weight_g": 150.0,
                "batch_id": "b1", "batch_name": "Batch A"}
        result = normalize_empty_tank(tank)
        assert result["fish_count"] == 1000
        assert result["biomass_kg"] == 150.0


# ── Regression: farm_state materialization (9 canonical scenarios) ────────────

class TestFarmStateMaterialization:
    """
    Regression tests for the materialized-state architecture.

    Rule: farm_state.fish_count IS the single source of truth for current stock.
    Movement, mortality, and adjustment records are audit history — they supply
    informational totals (total_moved_in etc.) but NEVER alter fish_count.
    """

    def _farm_with_tank(self, tid, fish_count, avg_weight_g=100.0):
        tank = {
            "id": tid, "name": f"Tank_{tid}",
            "system_id": "sys_A", "system_name": "A",
            "fish_count": float(fish_count),
            "avg_weight_g": avg_weight_g,
            "biomass_kg": round(fish_count * avg_weight_g / 1000.0, 3),
            "tank_volume_m3": 50.0,
        }
        return {
            "systems": [{"id": "sys_A", "name": "A", "type": "Growout", "tanks": [tank]}],
            "tanks": [],
        }

    def _mv_in(self, tid, qty, mid="mv_in"):
        return {
            "id": mid, "date": "2026-01-01",
            "movement_type": "external_stock_in",
            "from_tank_id": "", "to_tank_id": tid,
            "quantity_fish": qty, "avg_weight_g": 0.0,
        }

    def _mv_out(self, tid, qty, mid="mv_out"):
        return {
            "id": mid, "date": "2026-01-02",
            "movement_type": "transfer",
            "from_tank_id": tid, "to_tank_id": "other",
            "quantity_fish": qty, "avg_weight_g": 0.0,
        }

    def _mortality_log(self, tid, mort, date="2026-01-03"):
        return {
            "date": date, "system_name": "A",
            "tank_id": tid, "tank_name": f"Tank_{tid}",
            "mortality_fish": mort, "feed_kg": 0.0, "oxygen": 0.0,
        }

    def _adj(self, tid, variance):
        return {
            "id": "adj1", "date": "2026-01-01",
            "tank_id": tid, "variance": variance,
        }

    # ── Test 1 ────────────────────────────────────────────────────────────────
    def test_materialized_fish_count_takes_precedence_over_history_replay(self):
        """
        farm_state has fish_count=1346. Movement history, if replayed, would
        give a different number. The live result must be 1346.
        """
        farm = self._farm_with_tank("t1", fish_count=1346)
        # History: +1346 in, -2194 out → replay would give 1346 + 1346 - 2194 = 498.
        mvs = [
            self._mv_in("t1", 1346, "mv_in"),
            self._mv_out("t1", 2194, "mv_out"),
        ]
        states = compute_farm_state(farm, [], [], mvs)
        s = next(st for st in states if st["tank_id"] == "t1")
        assert get_tank_fish_count(s) == 1346, (
            "fish_count must come from farm_state, not from replaying movements"
        )

    # ── Test 2 ────────────────────────────────────────────────────────────────
    def test_empty_farm_state_is_empty_even_when_history_implies_stock(self):
        """
        farm_state has fish_count=0. Movement history implies a stocked tank.
        The live result must be 0 (empty).
        """
        farm = self._farm_with_tank("t1", fish_count=0)
        mvs  = [self._mv_in("t1", 5000)]   # history shows 5000 came in
        states = compute_farm_state(farm, [], [], mvs)
        s = next(st for st in states if st["tank_id"] == "t1")
        assert get_tank_fish_count(s) == 0
        assert not is_tank_occupied(s)

    # ── Test 3 ────────────────────────────────────────────────────────────────
    def test_movement_history_totals_still_reported_as_informational_fields(self):
        """
        Movement records supply informational totals (moved_in_fish,
        moved_out_fish) without altering fish_count.
        """
        farm = self._farm_with_tank("t1", fish_count=800)
        mvs  = [self._mv_in("t1", 1000, "in"), self._mv_out("t1", 200, "out")]
        states = compute_farm_state(farm, [], [], mvs)
        s = next(st for st in states if st["tank_id"] == "t1")
        assert s["moved_in_fish"]  == 1000
        assert s["moved_out_fish"] == 200
        assert get_tank_fish_count(s) == 800   # farm_state, not 800+1000-200=1600

    # ── Test 4 ────────────────────────────────────────────────────────────────
    def test_mortality_not_subtracted_twice(self):
        """
        When mortality is logged, _apply_mortality_to_farm updates farm_state
        immediately. compute_farm_state reads fish_count from farm_state and must
        NOT subtract mortality again.

        Scenario: setup 1000, 80 logged dead → farm_state updated to 920.
        compute_farm_state must return 920, not 920 - 80 = 840.
        """
        farm = self._farm_with_tank("t1", fish_count=920)  # farm_state post-mortality
        dl   = [self._mortality_log("t1", 80)]
        states = compute_farm_state(farm, [], dl, [])
        s = next(st for st in states if st["tank_id"] == "t1")
        assert get_tank_fish_count(s) == 920, (
            "mortality must not be subtracted from the already-updated farm_state"
        )
        assert s["total_mortality"] == 80  # informational

    # ── Test 5 ────────────────────────────────────────────────────────────────
    def test_dashboard_and_daily_log_same_source(self):
        """
        Both the Dashboard and Daily Log call compute_farm_state, which now
        reads farm_state fish_count directly. Given identical farm_state, they
        must produce the same fish_count regardless of movement or adjustment
        records passed.
        """
        farm = self._farm_with_tank("t1", fish_count=1346)
        adj  = [self._adj("t1", 1363)]
        mvs  = [self._mv_out("t1", 1000)]

        # "Dashboard" path: includes adjustments
        dashboard_states = compute_farm_state(farm, [], [], mvs, adjustments=adj)
        # "Daily Log" path: now also includes adjustments (the bug fix)
        daily_log_states = compute_farm_state(farm, [], [], mvs, adjustments=adj)

        d = next(st for st in dashboard_states if st["tank_id"] == "t1")
        l = next(st for st in daily_log_states  if st["tank_id"] == "t1")
        assert get_tank_fish_count(d) == get_tank_fish_count(l) == 1346

    # ── Test 6 ────────────────────────────────────────────────────────────────
    def test_transfer_quantity_not_double_applied(self):
        """
        A transfer updates farm_state once (via _update_farm_state_for_movement).
        Subsequent compute_farm_state calls must not apply the transfer again.
        """
        farm = self._farm_with_tank("t1", fish_count=700)  # farm_state after 300 out
        mv   = self._mv_out("t1", 300)
        states = compute_farm_state(farm, [], [], [mv])
        s = next(st for st in states if st["tank_id"] == "t1")
        assert get_tank_fish_count(s) == 700, (
            "transfer quantity must not reduce fish_count again in compute_farm_state"
        )

    # ── Test 7 ────────────────────────────────────────────────────────────────
    def test_external_stock_in_not_double_counted(self):
        """
        External Stock In updates farm_state once. Subsequent compute_farm_state
        must not add the quantity again.
        """
        farm = self._farm_with_tank("t1", fish_count=1500)  # farm_state after +500 in
        mv   = self._mv_in("t1", 500)
        states = compute_farm_state(farm, [], [], [mv])
        s = next(st for st in states if st["tank_id"] == "t1")
        assert get_tank_fish_count(s) == 1500, (
            "External Stock In must not add to the already-updated farm_state"
        )

    # ── Test 8 ────────────────────────────────────────────────────────────────
    def test_harvest_killing_not_double_applied(self):
        """
        Harvest/Killing updates farm_state once. compute_farm_state must not
        subtract again.
        """
        farm = self._farm_with_tank("t1", fish_count=0)  # farm_state after full harvest
        mv   = {
            "id": "mv_h", "date": "2026-01-05",
            "movement_type": "harvest",
            "from_tank_id": "t1", "to_tank_id": None,
            "quantity_fish": 2000, "avg_weight_g": 0.0,
        }
        states = compute_farm_state(farm, [], [], [mv])
        s = next(st for st in states if st["tank_id"] == "t1")
        assert get_tank_fish_count(s) == 0
        assert not is_tank_occupied(s)

    # ── Test 9 ────────────────────────────────────────────────────────────────
    def test_tank_2_09_style_fixture(self):
        """
        Reproduces the tank 2.09 production scenario that triggered the bug:
        - farm_state.fish_count = 841 (initial setup value before fix)
        - History: +1346 in, -2194 out, -10 mortality → replay = -17 → 0 (wrong)
        - After migration farm_state is updated to the correct value via
          compute_farm_state+adjustments.  Here we simulate the post-migration
          state: farm_state.fish_count = 1346 (computed with adjustment +1363).
        - Expected: compute_farm_state returns 1346, tank is occupied.
        """
        # Post-migration: farm_state already reflects the adjustment.
        farm = self._farm_with_tank("t1", fish_count=1346)
        mvs  = [
            self._mv_in("t1",  1346, "mv_in"),
            self._mv_out("t1", 2194, "mv_out"),
        ]
        dl  = [self._mortality_log("t1", 10)]
        adj = [self._adj("t1", 1363)]

        states = compute_farm_state(farm, [], dl, mvs, adjustments=adj)
        s = next(st for st in states if st["tank_id"] == "t1")

        assert is_tank_occupied(s),           "tank must be occupied (1346 fish)"
        assert get_tank_fish_count(s) == 1346, "fish_count must come from farm_state"
        # Informational history totals are still available:
        assert s["moved_in_fish"]    == 1346
        assert s["moved_out_fish"]   == 2194
        assert s["total_mortality"]  == 10
        assert s["total_adj_variance"] == 1363
