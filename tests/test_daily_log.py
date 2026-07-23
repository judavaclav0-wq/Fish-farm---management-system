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
from core.stock_engine import normalize_empty_tank, compute_tank_state


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

    # Test 6: occupied tank allows mortality (fish_count > 0 in compute result)
    def test_occupied_tank_has_positive_fish_count(self):
        tank = self._mk_tank(1000, avg_weight_g=100.0)
        s    = compute_tank_state(tank, [], movements=[], daily_logs=[])
        assert is_tank_occupied(s)

    # Test 7: empty tank has fish_count=0 and is not occupied
    def test_empty_tank_not_occupied(self):
        tank = self._mk_tank(0, avg_weight_g=0.0)
        s    = compute_tank_state(tank, [], movements=[], daily_logs=[])
        assert not is_tank_occupied(s)

    # Test 8: tank becomes empty after transfer-all-out
    def test_transfer_all_fish_out_yields_empty(self):
        tank = self._mk_tank(1000)
        s    = compute_tank_state(tank, [], movements=[self._transfer_out(1000)], daily_logs=[])
        assert not is_tank_occupied(s)

    # Test 9: External Stock In changes tank from empty to occupied
    def test_ext_in_makes_empty_tank_occupied(self):
        tank = self._mk_tank(0)
        s    = compute_tank_state(tank, [], movements=[self._ext_in(500)], daily_logs=[])
        assert is_tank_occupied(s)
        assert get_tank_fish_count(s) == 500


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
